package com.capstone.decision.application.risk.port

import com.capstone.decision.domain.risk.EvaluationBounds
import com.capstone.decision.domain.risk.MetricCell
import com.capstone.decision.domain.risk.MetricValue
import com.capstone.decision.domain.risk.OrderIntentSnapshot
import com.capstone.decision.domain.risk.PortfolioSource
import java.math.BigDecimal
import java.time.Instant

data class EvaluationSourceRequest(
    val actorUserId: String,
    val portfolioContext: PortfolioContextRef,
    val orderIntent: OrderIntentSnapshot,
    val evaluationAsOf: Instant,
    val evaluationId: String = "unavailable",
    val decisionId: String = "unavailable",
)

data class PortfolioPosition(
    val symbol: String,
    val quantity: Long,
    val marketValueKrw: Long,
    val isGoldEtfEtn: Boolean,
) {
    init {
        require(symbol.isNotBlank() && symbol.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(quantity >= 0)
        require(marketValueKrw >= 0)
    }
}

data class BalanceSnapshot(
    val source: PortfolioSource,
    val revision: String,
    val ownerScopeHash: String,
    val cashKrw: Long,
    val portfolioEquityKrw: Long,
    val positions: List<PortfolioPosition>,
) {
    init {
        require(revision.isNotBlank() && revision.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(OWNER_SCOPE_HASH.matches(ownerScopeHash))
        require(cashKrw >= 0)
        require(portfolioEquityKrw >= 0)
        require(positions.size <= EvaluationBounds.MAX_POSITIONS)
        require(positions.map(PortfolioPosition::symbol).distinct().size == positions.size)
    }

    private companion object {
        val OWNER_SCOPE_HASH = Regex(EvaluationBounds.SANITIZED_SHA256_PATTERN)
    }
}

data class RiskMetricBundle(
    val dailyLossRate: MetricCell<MetricValue>,
    val maxDrawdown: MetricCell<MetricValue>,
    val annualizedVolatility: MetricCell<MetricValue>,
)

data class InstrumentSnapshot(
    val symbol: String,
    val isEtfEtn: Boolean,
    val isGoldEtfEtn: Boolean,
    val productRiskScore: BigDecimal?,
    val catalogVersion: String,
) {
    init {
        require(symbol.isNotBlank() && symbol.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(catalogVersion.isNotBlank() && catalogVersion.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(productRiskScore == null || productRiskScore in BigDecimal.ZERO..BigDecimal.ONE)
        require(!isGoldEtfEtn || isEtfEtn) {
            "Gold ETF/ETN metadata must also be classified as ETF/ETN."
        }
    }
}

data class DisclosureEventEvidence(
    val eventCode: String,
    val state: String,
) {
    init {
        require(eventCode.isNotBlank() && eventCode.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(state.isNotBlank() && state.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
    }
}

data class DisclosureRiskSnapshot(
    val score: BigDecimal,
    val mappingVersion: String,
    val completeness: String = "COMPLETE",
    val events: List<DisclosureEventEvidence>,
    val warnings: List<String>,
    val sourceRefs: List<String>,
) {
    init {
        require(score in BigDecimal.ZERO..BigDecimal.ONE)
        require(mappingVersion.isNotBlank() && mappingVersion.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(completeness in setOf("COMPLETE", "EMPTY"))
        require(events.size <= EvaluationBounds.MAX_DISCLOSURE_EVENTS)
        require(events.map(DisclosureEventEvidence::eventCode).distinct().size == events.size)
        require(warnings.size <= EvaluationBounds.MAX_WARNINGS)
        require(
            warnings.all {
                it.isNotBlank() && it.length <= EvaluationBounds.MAX_SAFE_MESSAGE_CHARS
            },
        )
        require(sourceRefs.size <= EvaluationBounds.MAX_SOURCE_REFS)
        require(sourceRefs.distinct().size == sourceRefs.size)
        require(sourceRefs.all(SOURCE_REF::matches))
    }

    private companion object {
        val SOURCE_REF = Regex(EvaluationBounds.SANITIZED_SHA256_PATTERN)
    }
}

data class SignalMetricBundle(
    val hmmRiskOffProbability: MetricCell<MetricValue>,
    val meanReversionAbsoluteZScore: MetricCell<MetricValue>,
    val optionalComponents: List<OptionalComponentEvidence> = emptyList(),
) {
    init {
        require(optionalComponents.size <= EvaluationBounds.MAX_ABSTENTIONS)
        require(optionalComponents.map(OptionalComponentEvidence::componentId).distinct().size == optionalComponents.size)
        // HMM/mean-reversion/news/disclosure는 전용 typed field/port만 신뢰해 상충 evidence의 우선순위를 만들지 않는다.
        require(optionalComponents.all { it.componentId in GENERIC_COMPONENT_IDS })
    }

    private companion object {
        val GENERIC_COMPONENT_IDS = setOf("LIGHTGBM", "BSM", "GBM")
    }
}

typealias OptionalComponentEvidence = com.capstone.decision.domain.risk.OptionalComponentEvidence

interface PricePort {
    fun load(request: EvaluationSourceRequest): MetricCell<MetricValue>
}

interface BalancePort {
    val source: PortfolioSource

    fun load(request: EvaluationSourceRequest): MetricCell<BalanceSnapshot>
}

interface MarginPort {
    fun load(request: EvaluationSourceRequest): MetricCell<MetricValue>
}

interface OrderMetricPort {
    fun loadDailyOrderCount(request: EvaluationSourceRequest): MetricCell<MetricValue>
}

interface RiskSnapshotPort {
    fun load(request: EvaluationSourceRequest): RiskMetricBundle
}

interface InstrumentCatalogPort {
    fun load(request: EvaluationSourceRequest): MetricCell<InstrumentSnapshot>
}

interface NewsEvidencePort {
    fun loadNegativeScore(request: EvaluationSourceRequest): MetricCell<MetricValue>
}

interface DisclosureRiskPort {
    fun load(request: EvaluationSourceRequest): MetricCell<DisclosureRiskSnapshot>
}

interface SignalPort {
    fun load(request: EvaluationSourceRequest): SignalMetricBundle
}
