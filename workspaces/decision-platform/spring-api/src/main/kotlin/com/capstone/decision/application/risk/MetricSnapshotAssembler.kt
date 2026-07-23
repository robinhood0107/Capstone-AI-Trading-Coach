package com.capstone.decision.application.risk

import com.capstone.decision.application.risk.port.ActivePrincipleSnapshot
import com.capstone.decision.application.risk.port.BalancePort
import com.capstone.decision.application.risk.port.BalanceSnapshot
import com.capstone.decision.application.risk.port.DisclosureRiskPort
import com.capstone.decision.application.risk.port.DisclosureRiskSnapshot
import com.capstone.decision.application.risk.port.EvaluationSourceRequest
import com.capstone.decision.application.risk.port.InstrumentCatalogPort
import com.capstone.decision.application.risk.port.InstrumentSnapshot
import com.capstone.decision.application.risk.port.MarginPort
import com.capstone.decision.application.risk.port.NewsEvidencePort
import com.capstone.decision.application.risk.port.OrderMetricPort
import com.capstone.decision.application.risk.port.PortfolioContextRef
import com.capstone.decision.application.risk.port.PricePort
import com.capstone.decision.application.risk.port.RiskSnapshotPort
import com.capstone.decision.application.risk.port.SignalMetricBundle
import com.capstone.decision.application.risk.port.SignalPort
import com.capstone.decision.domain.risk.CanonicalJson
import com.capstone.decision.domain.risk.DisclosureEvidenceIdentity
import com.capstone.decision.domain.risk.EvaluationBounds
import com.capstone.decision.domain.risk.MetricCell
import com.capstone.decision.domain.risk.MetricIssueCode
import com.capstone.decision.domain.risk.MetricKey
import com.capstone.decision.domain.risk.MetricSnapshot
import com.capstone.decision.domain.risk.MetricSource
import com.capstone.decision.domain.risk.MetricUnit
import com.capstone.decision.domain.risk.MetricValue
import com.capstone.decision.domain.risk.OptionalComponentEvidence
import com.capstone.decision.domain.risk.OrderIntentSnapshot
import com.capstone.decision.domain.risk.PortfolioSnapshotIdentity
import com.capstone.decision.domain.risk.PortfolioSource
import com.capstone.decision.domain.risk.PrincipleSnapshotIdentity
import java.math.BigDecimal
import java.time.Instant

data class MetricAcquisitionPlan(
    val metricKeys: Set<MetricKey>,
    val optionalComponents: Set<String> = emptySet(),
) {
    fun requires(key: MetricKey): Boolean = key in metricKeys
}

data class MetricAssemblyRequest(
    val actorUserId: String,
    val evaluationId: String,
    val evaluationAsOf: Instant,
    val principle: ActivePrincipleSnapshot,
    val portfolioContext: PortfolioContextRef,
    val orderIntent: OrderIntentSnapshot,
    val systemRuleCatalogVersion: Int,
    val readinessPolicyVersion: String,
    val acquisitionPlan: MetricAcquisitionPlan,
)

// 이 class만 source port I/O를 조율하며 rule 비교, persistence, 현재시각 조회는 수행하지 않는다.
class MetricSnapshotAssembler(
    private val pricePort: PricePort,
    private val kisMockBalancePort: BalancePort,
    private val internalPaperBalancePort: BalancePort,
    private val marginPort: MarginPort,
    private val orderMetricPort: OrderMetricPort,
    private val riskSnapshotPort: RiskSnapshotPort,
    private val instrumentCatalogPort: InstrumentCatalogPort,
    private val newsEvidencePort: NewsEvidencePort,
    private val disclosureRiskPort: DisclosureRiskPort,
    private val signalPort: SignalPort,
) {
    init {
        require(kisMockBalancePort.source == PortfolioSource.KIS_MOCK)
        require(internalPaperBalancePort.source == PortfolioSource.INTERNAL_PAPER)
    }

    /**
     * 명시 source의 BalancePort만 한 번 호출하며 KIS 실패 뒤 INTERNAL_PAPER를 호출하지 않는다.
     */
    fun assemble(request: MetricAssemblyRequest): MetricSnapshot {
        val sourceRequest =
            EvaluationSourceRequest(
                actorUserId = request.actorUserId,
                portfolioContext = request.portfolioContext,
                orderIntent = request.orderIntent,
                evaluationAsOf = request.evaluationAsOf,
            )
        val plan = request.acquisitionPlan
        val price =
            if (PRICE_KEYS.any(plan::requires)) {
                validatePositiveWholeMetric(pricePort.load(sourceRequest), MetricUnit.KRW)
            } else {
                notApplicable()
            }
        val balance =
            if (BALANCE_KEYS.any(plan::requires)) {
                validateBalance(
                    selectedBalancePort(request.portfolioContext.source).load(sourceRequest),
                    request.portfolioContext,
                )
            } else {
                MetricCell.NotApplicable(MetricIssueCode.NOT_APPLICABLE)
            }
        val margin =
            if (plan.requires(MetricKey.MARGIN_REQUIREMENT_KRW)) {
                validateNonNegativeWholeMetric(
                    marginPort.load(sourceRequest),
                    MetricUnit.KRW,
                )
            } else {
                notApplicable()
            }
        val dailyOrderCount =
            if (plan.requires(MetricKey.DAILY_ORDER_COUNT)) {
                validateNonNegativeWholeMetric(
                    orderMetricPort.loadDailyOrderCount(sourceRequest),
                    MetricUnit.COUNT,
                )
            } else {
                notApplicable()
            }
        val risk =
            if (RISK_KEYS.any(plan::requires)) {
                riskSnapshotPort.load(sourceRequest)
            } else {
                null
            }
        val instrumentCell =
            if (INSTRUMENT_KEYS.any(plan::requires)) {
                validateInstrument(
                    instrumentCatalogPort.load(sourceRequest),
                    request.orderIntent.symbol,
                )
            } else {
                MetricCell.NotApplicable(MetricIssueCode.NOT_APPLICABLE)
            }
        val news =
            if (plan.requires(MetricKey.NEGATIVE_NEWS_SCORE)) {
                validateDecimalMetric(
                    newsEvidencePort.loadNegativeScore(sourceRequest),
                    MetricUnit.RATIO,
                    minimum = BigDecimal.ZERO,
                    maximum = BigDecimal.ONE,
                )
            } else {
                notApplicable()
            }
        val disclosureCell =
            if (plan.requires(MetricKey.DISCLOSURE_RISK_SCORE)) {
                disclosureRiskPort.load(sourceRequest)
            } else {
                MetricCell.NotApplicable(MetricIssueCode.NOT_APPLICABLE)
            }
        val signals =
            if (SIGNAL_KEYS.any(plan::requires) || plan.optionalComponents.any(GENERIC_SIGNAL_COMPONENTS::contains)) {
                signalPort.load(sourceRequest)
            } else {
                emptySignals()
            }

        val metrics = linkedMapOf<MetricKey, MetricCell<MetricValue>>()
        metrics[MetricKey.CURRENT_PRICE_KRW] = price
        metrics[MetricKey.MARGIN_REQUIREMENT_KRW] = margin
        metrics[MetricKey.DAILY_ORDER_COUNT] = dailyOrderCount
        metrics[MetricKey.NEGATIVE_NEWS_SCORE] = news
        metrics[MetricKey.DAILY_LOSS_RATE] =
            risk?.dailyLossRate?.let {
                validateDecimalMetric(
                    it,
                    MetricUnit.RATIO,
                    minimum = BigDecimal.ONE.negate(),
                    maximum = BigDecimal.ZERO,
                )
            } ?: notApplicable()
        metrics[MetricKey.MDD] =
            risk?.maxDrawdown?.let {
                validateDecimalMetric(
                    it,
                    MetricUnit.RATIO,
                    minimum = BigDecimal.ONE.negate(),
                    maximum = BigDecimal.ZERO,
                )
            } ?: notApplicable()
        metrics[MetricKey.ANNUALIZED_VOLATILITY] =
            risk?.annualizedVolatility?.let {
                validateDecimalMetric(
                    it,
                    MetricUnit.RATIO,
                    minimum = BigDecimal.ZERO,
                )
            } ?: notApplicable()
        metrics[MetricKey.HMM_RISK_OFF_PROBABILITY] =
            validateDecimalMetric(
                signals.hmmRiskOffProbability,
                MetricUnit.RATIO,
                minimum = BigDecimal.ZERO,
                maximum = BigDecimal.ONE,
            )
        metrics[MetricKey.MEAN_REVERSION_Z_SCORE] =
            validateDecimalMetric(
                signals.meanReversionAbsoluteZScore,
                MetricUnit.ABS_Z_SCORE,
                minimum = BigDecimal.ZERO,
            )

        val instrument = (instrumentCell as? MetricCell.Available)?.value
        metrics[MetricKey.ETF_ETN_RISK_SCORE] = instrumentRiskMetric(instrumentCell)
        val disclosure = (disclosureCell as? MetricCell.Available)?.value
        metrics[MetricKey.DISCLOSURE_RISK_SCORE] = disclosureMetric(disclosureCell)

        addBalanceMetrics(metrics, balance, request.orderIntent.symbol)
        metrics[MetricKey.ORDER_AMOUNT_KRW] = orderAmountMetric(price, request.orderIntent)
        metrics[MetricKey.ASSET_WEIGHT] =
            assetWeightMetric(balance, price, request.orderIntent)
        metrics[MetricKey.GOLD_ETF_ETN_WEIGHT] =
            goldWeightMetric(balance, price, instrumentCell, request.orderIntent)

        val portfolioIdentity = portfolioIdentity(request.portfolioContext, balance)
        val optionalEvidence =
            observedOptionalEvidence(
                requested = plan.optionalComponents,
                metrics = metrics,
                signals = signals,
                disclosureCell = disclosureCell,
            )
        val availableCells = metrics.values.filterIsInstance<MetricCell.Available<MetricValue>>()
        val provenanceRefs =
            (
                availableCells.map(MetricCell.Available<MetricValue>::sourceRef) +
                    (disclosure?.sourceRefs ?: emptyList()) +
                    optionalEvidence.flatMap(OptionalComponentEvidence::sourceRefs)
            ).distinct()
                .sorted()
        require(provenanceRefs.size <= EvaluationBounds.MAX_SOURCE_REFS) {
            "S2.2 provenance exceeds the approved source-reference bound."
        }
        val retrievedAt =
            availableCells
                .maxOfOrNull(MetricCell.Available<MetricValue>::retrievedAt)
                ?: request.evaluationAsOf
        val snapshot =
            MetricSnapshot(
                snapshotSchemaVersion = "s2.2-metric-snapshot-v1",
                evaluationId = request.evaluationId,
                evaluationAsOf = request.evaluationAsOf,
                retrievedAt = retrievedAt,
                actorUserId = request.actorUserId,
                principle =
                    PrincipleSnapshotIdentity(
                        principleId = request.principle.principleId.value,
                        principleVersionId = request.principle.principleVersionId.value,
                        version = request.principle.version,
                        mode = request.principle.mode,
                        rulesHash = principleRulesHash(request.principle),
                    ),
                systemRuleCatalogVersion = request.systemRuleCatalogVersion,
                readinessPolicyVersion = request.readinessPolicyVersion,
                portfolio = portfolioIdentity,
                orderIntent = request.orderIntent,
                metrics = metrics.toMap(),
                provenanceRefs = provenanceRefs,
                requestedOptionalComponents = plan.optionalComponents.sorted(),
                observedOptionalComponentEvidence = optionalEvidence,
                disclosureEvidence =
                    disclosure?.let {
                        DisclosureEvidenceIdentity(
                            completeness = it.completeness,
                            mappingVersion = it.mappingVersion,
                            sourceRefs = it.sourceRefs.sorted(),
                        )
                    },
            )
        return snapshot
    }

    private fun observedOptionalEvidence(
        requested: Set<String>,
        metrics: Map<MetricKey, MetricCell<MetricValue>>,
        signals: SignalMetricBundle,
        disclosureCell: MetricCell<DisclosureRiskSnapshot>,
    ): List<OptionalComponentEvidence> =
        requested
            .sorted()
            .map { componentId ->
                signals.optionalComponents.firstOrNull { it.componentId == componentId }
                    ?: when (componentId) {
                        HMM_COMPONENT ->
                            metricOptionalEvidence(
                                componentId,
                                metrics.getValue(MetricKey.HMM_RISK_OFF_PROBABILITY),
                            )

                        MEAN_REVERSION_COMPONENT ->
                            metricOptionalEvidence(
                                componentId,
                                metrics.getValue(MetricKey.MEAN_REVERSION_Z_SCORE),
                            )

                        NEWS_COMPONENT ->
                            metricOptionalEvidence(
                                componentId,
                                metrics.getValue(MetricKey.NEGATIVE_NEWS_SCORE),
                            )

                        DISCLOSURE_COMPONENT -> disclosureOptionalEvidence(disclosureCell)
                        else ->
                            OptionalComponentEvidence(
                                componentId = componentId,
                                available = false,
                                reasonCode = MetricIssueCode.SOURCE_MISSING.name,
                            )
                    }
            }

    private fun metricOptionalEvidence(
        componentId: String,
        cell: MetricCell<MetricValue>,
    ): OptionalComponentEvidence =
        if (cell is MetricCell.Available) {
            OptionalComponentEvidence(
                componentId = componentId,
                available = true,
                reasonCode = null,
                evidenceVersion = cell.sourceVersion,
                sourceRefs = listOf(cell.sourceRef),
            )
        } else {
            OptionalComponentEvidence(
                componentId = componentId,
                available = false,
                reasonCode = unavailableReason(cell).name,
            )
        }

    private fun disclosureOptionalEvidence(cell: MetricCell<DisclosureRiskSnapshot>): OptionalComponentEvidence =
        if (cell is MetricCell.Available) {
            OptionalComponentEvidence(
                componentId = DISCLOSURE_COMPONENT,
                available = true,
                reasonCode = null,
                evidenceVersion = cell.value.mappingVersion,
                completeness = cell.value.completeness,
                sourceRefs = cell.value.sourceRefs.sorted(),
            )
        } else {
            OptionalComponentEvidence(
                componentId = DISCLOSURE_COMPONENT,
                available = false,
                reasonCode = unavailableReason(cell).name,
            )
        }

    private fun unavailableReason(cell: MetricCell<*>): MetricIssueCode =
        when (cell) {
            is MetricCell.Missing -> cell.reason
            is MetricCell.Stale -> cell.reason
            is MetricCell.Error -> cell.reason
            is MetricCell.Incomplete -> cell.reason
            is MetricCell.Abstained -> cell.reason
            is MetricCell.NotApplicable -> cell.reason
            is MetricCell.Available -> error("Available evidence does not have an unavailable reason.")
        }

    private fun selectedBalancePort(source: PortfolioSource): BalancePort =
        when (source) {
            PortfolioSource.KIS_MOCK -> kisMockBalancePort
            PortfolioSource.INTERNAL_PAPER -> internalPaperBalancePort
        }

    private fun addBalanceMetrics(
        metrics: MutableMap<MetricKey, MetricCell<MetricValue>>,
        balance: MetricCell<BalanceSnapshot>,
        orderSymbol: String,
    ) {
        when (balance) {
            is MetricCell.Available -> {
                val positionQuantity =
                    balance.value.positions
                        .singleOrNull { it.symbol == orderSymbol && it.quantity > 0 }
                        ?.quantity ?: 0L
                metrics[MetricKey.OWNER_POSITION_QUANTITY] =
                    fromAvailable(balance, MetricValue.Whole(positionQuantity, MetricUnit.QUANTITY))
                metrics[MetricKey.PORTFOLIO_EQUITY_KRW] =
                    fromAvailable(
                        balance,
                        MetricValue.Whole(balance.value.portfolioEquityKrw, MetricUnit.KRW),
                    )
            }

            else -> {
                val unavailable = unavailableMetric(balance)
                metrics[MetricKey.OWNER_POSITION_QUANTITY] = unavailable
                metrics[MetricKey.PORTFOLIO_EQUITY_KRW] = unavailable
            }
        }
    }

    private fun orderAmountMetric(
        price: MetricCell<MetricValue>,
        order: OrderIntentSnapshot,
    ): MetricCell<MetricValue> {
        val available = price as? MetricCell.Available ?: return unavailableMetric(price)
        val priceKrw = effectiveUnitPriceKrw(available.value, order) ?: return sourceError()
        return try {
            fromAvailable(
                available,
                MetricValue.Whole(Math.multiplyExact(priceKrw, order.quantity), MetricUnit.KRW),
            )
        } catch (_: ArithmeticException) {
            sourceError()
        }
    }

    private fun assetWeightMetric(
        balance: MetricCell<BalanceSnapshot>,
        price: MetricCell<MetricValue>,
        order: OrderIntentSnapshot,
    ): MetricCell<MetricValue> {
        val availableBalance = balance as? MetricCell.Available ?: return unavailableMetric(balance)
        val availablePrice = price as? MetricCell.Available ?: return unavailableMetric(price)
        val postValue =
            postOrderTargetValue(availableBalance.value, availablePrice.value, order)
                ?: return sourceError()
        return ratioMetric(postValue, availableBalance.value.portfolioEquityKrw, availableBalance, availablePrice)
    }

    private fun goldWeightMetric(
        balance: MetricCell<BalanceSnapshot>,
        price: MetricCell<MetricValue>,
        instrument: MetricCell<InstrumentSnapshot>,
        order: OrderIntentSnapshot,
    ): MetricCell<MetricValue> {
        val availableBalance = balance as? MetricCell.Available ?: return unavailableMetric(balance)
        val availablePrice = price as? MetricCell.Available ?: return unavailableMetric(price)
        val availableInstrument = instrument as? MetricCell.Available ?: return unavailableMetric(instrument)
        val targetPosition =
            availableBalance.value.positions
                .singleOrNull { it.symbol == order.symbol && it.quantity > 0 }
        if (targetPosition != null) {
            check(targetPosition.isGoldEtfEtn == availableInstrument.value.isGoldEtfEtn) {
                "Balance and instrument metadata disagree on gold ETF/ETN classification."
            }
        }
        val currentGold =
            try {
                availableBalance.value.positions
                    .filter { it.quantity > 0 && it.isGoldEtfEtn }
                    .fold(0L) { sum, position -> Math.addExact(sum, position.marketValueKrw) }
            } catch (_: ArithmeticException) {
                return sourceError()
            }
        val targetCurrent = targetPosition?.marketValueKrw ?: 0L
        val targetPost =
            postOrderTargetValue(availableBalance.value, availablePrice.value, order)
                ?: return sourceError()
        val postGold =
            try {
                if (availableInstrument.value.isGoldEtfEtn) {
                    Math.addExact(Math.subtractExact(currentGold, targetCurrent), targetPost)
                } else {
                    currentGold
                }
            } catch (_: ArithmeticException) {
                return sourceError()
            }
        return ratioMetric(
            postGold,
            availableBalance.value.portfolioEquityKrw,
            availableBalance,
            availablePrice,
            availableInstrument,
        )
    }

    private fun postOrderTargetValue(
        balance: BalanceSnapshot,
        price: MetricValue,
        order: OrderIntentSnapshot,
    ): Long? {
        val priceKrw = effectiveUnitPriceKrw(price, order) ?: return null
        return try {
            val amount = Math.multiplyExact(priceKrw, order.quantity)
            val current =
                balance.positions
                    .singleOrNull { it.symbol == order.symbol && it.quantity > 0 }
                    ?.marketValueKrw ?: 0L
            when (order.side) {
                "BUY" -> Math.addExact(current, amount)
                "SELL" -> Math.subtractExact(current, amount).takeIf { it >= 0 }
                else -> null
            }
        } catch (_: ArithmeticException) {
            null
        }
    }

    private fun effectiveUnitPriceKrw(
        currentPrice: MetricValue,
        order: OrderIntentSnapshot,
    ): Long? =
        when (order.orderType) {
            "MARKET" -> (currentPrice as? MetricValue.Whole)?.value
            "LIMIT" ->
                try {
                    order.limitPrice?.longValueExact()
                } catch (_: ArithmeticException) {
                    null
                }

            else -> null
        }?.takeIf { it > 0 }

    private fun ratioMetric(
        numerator: Long,
        denominator: Long,
        vararg sources: MetricCell.Available<*>,
    ): MetricCell<MetricValue> {
        if (denominator <= 0 || numerator < 0) {
            return sourceError()
        }
        return computedAvailable(
            MetricValue.RatioFraction(
                numerator = numerator,
                denominator = denominator,
                declaredScale = PORTFOLIO_RATIO_DECLARED_SCALE,
            ),
            sources.toList(),
        )
    }

    private fun instrumentRiskMetric(cell: MetricCell<InstrumentSnapshot>): MetricCell<MetricValue> =
        when (cell) {
            is MetricCell.Available ->
                if (!cell.value.isEtfEtn) {
                    notApplicable()
                } else if (cell.value.productRiskScore == null) {
                    MetricCell.Missing(MetricIssueCode.SOURCE_MISSING)
                } else {
                    try {
                        fromAvailable(
                            cell,
                            MetricValue.Decimal(
                                cell.value.productRiskScore,
                                COMPUTED_RATIO_SCALE,
                                MetricUnit.RATIO,
                            ),
                        )
                    } catch (_: IllegalArgumentException) {
                        sourceError()
                    }
                }

            else -> unavailableMetric(cell)
        }

    private fun disclosureMetric(cell: MetricCell<DisclosureRiskSnapshot>): MetricCell<MetricValue> =
        when (cell) {
            is MetricCell.Available ->
                try {
                    fromAvailable(
                        cell,
                        MetricValue.Decimal(cell.value.score, COMPUTED_RATIO_SCALE, MetricUnit.RATIO),
                    )
                } catch (_: IllegalArgumentException) {
                    sourceError()
                }

            else -> unavailableMetric(cell)
        }

    private fun portfolioIdentity(
        context: PortfolioContextRef,
        balance: MetricCell<BalanceSnapshot>,
    ): PortfolioSnapshotIdentity =
        if (balance is MetricCell.Available) {
            check(balance.value.source == context.source) { "Balance source crossed the selected portfolio mode." }
            check(balance.value.ownerScopeHash == context.ownerScopeHash) { "Balance owner scope mismatch." }
            PortfolioSnapshotIdentity(
                source = context.source,
                revision = balance.value.revision,
                ownerScopeHash = context.ownerScopeHash,
                positionCount = balance.value.positions.count { it.quantity > 0 },
            )
        } else {
            PortfolioSnapshotIdentity(
                source = context.source,
                revision = "unavailable-${context.source.name.lowercase()}",
                ownerScopeHash = context.ownerScopeHash,
                positionCount = 0,
            )
        }

    private fun validateMetric(
        cell: MetricCell<MetricValue>,
        expectedUnit: MetricUnit,
    ): MetricCell<MetricValue> =
        if (cell is MetricCell.Available && cell.value.unit != expectedUnit) {
            sourceError()
        } else {
            cell
        }

    private fun validateDecimalMetric(
        cell: MetricCell<MetricValue>,
        expectedUnit: MetricUnit,
        minimum: BigDecimal? = null,
        maximum: BigDecimal? = null,
    ): MetricCell<MetricValue> {
        val validated = validateMetric(cell, expectedUnit)
        if (validated !is MetricCell.Available) {
            return validated
        }
        val value = validated.value
        if (
            value.declaredScale > CONTRACT_DECIMAL_SCALE ||
            minimum?.let { value.compareTo(it) < 0 } == true ||
            maximum?.let { value.compareTo(it) > 0 } == true
        ) {
            return sourceError()
        }
        return validated
    }

    private fun validatePositiveWholeMetric(
        cell: MetricCell<MetricValue>,
        expectedUnit: MetricUnit,
    ): MetricCell<MetricValue> {
        val validated = validateMetric(cell, expectedUnit)
        return if (
            validated is MetricCell.Available &&
            (validated.value as? MetricValue.Whole)?.value?.let { it > 0 } != true
        ) {
            sourceError()
        } else {
            validated
        }
    }

    private fun validateNonNegativeWholeMetric(
        cell: MetricCell<MetricValue>,
        expectedUnit: MetricUnit,
    ): MetricCell<MetricValue> {
        val validated = validateMetric(cell, expectedUnit)
        return if (
            validated is MetricCell.Available &&
            (validated.value as? MetricValue.Whole)?.value?.let { it >= 0 } != true
        ) {
            sourceError()
        } else {
            validated
        }
    }

    private fun validateBalance(
        cell: MetricCell<BalanceSnapshot>,
        context: PortfolioContextRef,
    ): MetricCell<BalanceSnapshot> {
        if (cell !is MetricCell.Available) {
            return cell
        }
        // source/owner 교차는 정상적인 unavailable evidence가 아니라 adapter 보안 불변식 위반이다.
        check(cell.value.source == context.source) {
            "Balance source crossed the selected portfolio mode."
        }
        check(cell.value.ownerScopeHash == context.ownerScopeHash) {
            "Balance owner scope mismatch."
        }
        return if (cell.value.portfolioEquityKrw <= 0) {
            MetricCell.Incomplete(MetricIssueCode.SOURCE_INCOMPLETE)
        } else {
            cell
        }
    }

    private fun validateInstrument(
        cell: MetricCell<InstrumentSnapshot>,
        orderSymbol: String,
    ): MetricCell<InstrumentSnapshot> {
        if (cell is MetricCell.Available) {
            // 다른 종목의 metadata를 현재 주문 evidence로 쓰면 안 되므로 business HOLD로 숨기지 않는다.
            check(cell.value.symbol == orderSymbol) {
                "Instrument metadata symbol crossed the requested order symbol."
            }
        }
        return cell
    }

    private fun <T> fromAvailable(
        source: MetricCell.Available<T>,
        value: MetricValue,
    ): MetricCell.Available<MetricValue> =
        MetricCell.Available(
            value = value,
            observedAt = source.observedAt,
            retrievedAt = source.retrievedAt,
            freshUntil = source.freshUntil,
            source = source.source,
            sourceRef = source.sourceRef,
            sourceVersion = source.sourceVersion,
        )

    private fun computedAvailable(
        value: MetricValue,
        sources: List<MetricCell.Available<*>>,
    ): MetricCell.Available<MetricValue> {
        require(sources.isNotEmpty())
        val refs = sources.map { it.sourceRef }.distinct().sorted()
        return MetricCell.Available(
            value = value,
            observedAt = sources.minOf { it.observedAt },
            retrievedAt = sources.maxOf { it.retrievedAt },
            freshUntil = sources.minOf { it.freshUntil },
            source = MetricSource.INTERNAL,
            sourceRef = CanonicalJson.sha256(refs.joinToString("|")),
            sourceVersion = PORTFOLIO_METRIC_VERSION,
        )
    }

    private fun unavailableMetric(cell: MetricCell<*>): MetricCell<MetricValue> =
        when (cell) {
            is MetricCell.Missing -> MetricCell.Missing(cell.reason)
            is MetricCell.Stale -> MetricCell.Stale(cell.observedAt, cell.freshUntil, cell.reason)
            is MetricCell.Error -> MetricCell.Error(cell.reason)
            is MetricCell.Incomplete -> MetricCell.Incomplete(cell.reason)
            is MetricCell.Abstained -> MetricCell.Abstained(cell.reason)
            is MetricCell.NotApplicable -> MetricCell.NotApplicable(cell.reason)
            is MetricCell.Available -> sourceError()
        }

    private fun sourceError(): MetricCell.Error = MetricCell.Error(MetricIssueCode.SOURCE_ERROR)

    private fun notApplicable(): MetricCell.NotApplicable = MetricCell.NotApplicable(MetricIssueCode.NOT_APPLICABLE)

    private fun emptySignals(): SignalMetricBundle =
        SignalMetricBundle(
            hmmRiskOffProbability = notApplicable(),
            meanReversionAbsoluteZScore = notApplicable(),
        )

    companion object {
        private const val CONTRACT_DECIMAL_SCALE = 4
        private const val COMPUTED_RATIO_SCALE = CONTRACT_DECIMAL_SCALE
        private const val PORTFOLIO_RATIO_DECLARED_SCALE = 4
        private const val PORTFOLIO_METRIC_VERSION = "s2.2-portfolio-metrics-v1"
        private const val HMM_COMPONENT = "HMM"
        private const val MEAN_REVERSION_COMPONENT = "MEAN_REVERSION"
        private const val NEWS_COMPONENT = "NEGATIVE_NEWS"
        private const val DISCLOSURE_COMPONENT = "DISCLOSURE"
        private val GENERIC_SIGNAL_COMPONENTS = setOf("LIGHTGBM", "BSM", "GBM")
        private val PRICE_KEYS =
            setOf(
                MetricKey.CURRENT_PRICE_KRW,
                MetricKey.ORDER_AMOUNT_KRW,
                MetricKey.ASSET_WEIGHT,
                MetricKey.GOLD_ETF_ETN_WEIGHT,
            )
        private val BALANCE_KEYS =
            setOf(
                MetricKey.OWNER_POSITION_QUANTITY,
                MetricKey.PORTFOLIO_EQUITY_KRW,
                MetricKey.ASSET_WEIGHT,
                MetricKey.GOLD_ETF_ETN_WEIGHT,
            )
        private val RISK_KEYS =
            setOf(
                MetricKey.DAILY_LOSS_RATE,
                MetricKey.MDD,
                MetricKey.ANNUALIZED_VOLATILITY,
            )
        private val INSTRUMENT_KEYS =
            setOf(MetricKey.GOLD_ETF_ETN_WEIGHT, MetricKey.ETF_ETN_RISK_SCORE)
        private val SIGNAL_KEYS =
            setOf(MetricKey.HMM_RISK_OFF_PROBABILITY, MetricKey.MEAN_REVERSION_Z_SCORE)
    }
}

// 정상 조립과 context-unavailable snapshot이 같은 immutable Principle semantic hash를 공유한다.
internal fun principleRulesHash(principle: ActivePrincipleSnapshot): String =
    CanonicalJson.sha256(
        CanonicalJson.encode(
            principle.rules
                .sortedBy { it.ruleId }
                .map { rule ->
                    mapOf(
                        "ruleId" to rule.ruleId,
                        "ruleType" to rule.ruleType,
                        "metric" to rule.metric,
                        "operator" to rule.operator,
                        "threshold" to rule.threshold,
                        "severity" to rule.severity,
                        "enabled" to rule.enabled,
                        "evidenceRequirement" to rule.evidenceRequirement,
                    )
                },
        ),
    )
