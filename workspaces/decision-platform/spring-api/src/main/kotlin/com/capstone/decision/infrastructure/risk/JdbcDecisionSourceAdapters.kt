package com.capstone.decision.infrastructure.risk

import com.capstone.decision.application.risk.port.EvaluationSourceRequest
import com.capstone.decision.application.risk.port.InstrumentCatalogPort
import com.capstone.decision.application.risk.port.InstrumentSnapshot
import com.capstone.decision.application.risk.port.OrderMetricPort
import com.capstone.decision.application.risk.port.PortfolioSourceRequest
import com.capstone.decision.application.risk.port.RiskMetricBundle
import com.capstone.decision.application.risk.port.RiskSnapshotPort
import com.capstone.decision.domain.risk.FreshnessState
import com.capstone.decision.domain.risk.MetricCell
import com.capstone.decision.domain.risk.MetricIssueCode
import com.capstone.decision.domain.risk.MetricSource
import com.capstone.decision.domain.risk.MetricUnit
import com.capstone.decision.domain.risk.MetricValue
import com.capstone.decision.domain.risk.PreviousTradingDayFreshnessPolicy
import com.capstone.decision.domain.risk.TradingCalendarUnavailableException
import com.capstone.decision.domain.risk.TradingSessionPort
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import java.math.BigDecimal
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId

/**
 * S1.1 sanitized instrument catalog의 latest projection만 한 행 읽는다.
 * catalog version은 다음 observation이 append될 때까지 유효하며 미래 observedAt은 readiness에서 HOLD가 된다.
 */
@Repository
class JdbcInstrumentCatalogAdapter(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
) : InstrumentCatalogPort {
    override fun load(request: EvaluationSourceRequest): MetricCell<InstrumentSnapshot> {
        val rows =
            jdbc()
                .query(
                    """
                    SELECT symbol,
                           is_etf_etn,
                           is_gold_etf_etn,
                           product_risk_score,
                           catalog_version,
                           observed_at,
                           received_at,
                           completeness,
                           source_version,
                           source_ref
                    FROM latest_instrument_catalog_observations
                    WHERE symbol = :symbol
                    LIMIT 1
                    """.trimIndent(),
                    mapOf("symbol" to request.orderIntent.symbol),
                ) { result, _ ->
                    StoredInstrumentRow(
                        symbol = result.getString("symbol"),
                        isEtfEtn = result.getBoolean("is_etf_etn"),
                        isGoldEtfEtn = result.getBoolean("is_gold_etf_etn"),
                        productRiskScore = result.getBigDecimal("product_risk_score"),
                        catalogVersion = result.getString("catalog_version"),
                        observedAt = result.getObject("observed_at", OffsetDateTime::class.java).toInstant(),
                        receivedAt = result.getObject("received_at", OffsetDateTime::class.java).toInstant(),
                        completeness = result.getString("completeness"),
                        sourceVersion = result.getString("source_version"),
                        sourceRef = result.getString("source_ref"),
                    )
                }
        val row = rows.singleOrNull() ?: return MetricCell.Missing(MetricIssueCode.SOURCE_MISSING)
        if (row.completeness != COMPLETE) {
            return MetricCell.Incomplete(MetricIssueCode.SOURCE_INCOMPLETE)
        }
        return MetricCell.Available(
            value =
                InstrumentSnapshot(
                    symbol = row.symbol,
                    isEtfEtn = row.isEtfEtn,
                    isGoldEtfEtn = row.isGoldEtfEtn,
                    productRiskScore = row.productRiskScore,
                    catalogVersion = row.catalogVersion,
                ),
            observedAt = row.observedAt,
            retrievedAt = row.receivedAt,
            freshUntil = CATALOG_VALID_UNTIL_SUPERSEDED,
            source = MetricSource.INSTRUMENT_CATALOG,
            sourceRef = row.sourceRef,
            sourceVersion = row.sourceVersion,
        )
    }

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: error("Stored instrument catalog JDBC access is unavailable without a configured DataSource.")
}

/**
 * deterministic risk observation은 owner/source scope latest 한 행만 읽고 canonical previous-session freshness를 적용한다.
 */
@Repository
class JdbcDeterministicRiskAdapter(
    private val actorScopedReadQuery: ActorScopedReadQuery,
    tradingSessionPort: TradingSessionPort,
) : RiskSnapshotPort {
    private val freshnessPolicy = PreviousTradingDayFreshnessPolicy(tradingSessionPort)

    override fun load(request: EvaluationSourceRequest): RiskMetricBundle =
        loadStored(
            actorUserId = request.actorUserId,
            ownerScopeHash = request.portfolioContext.ownerScopeHash,
            portfolioSource = request.portfolioContext.source.name,
            evaluationAsOf = request.evaluationAsOf,
        )

    override fun loadPortfolio(request: PortfolioSourceRequest): RiskMetricBundle =
        loadStored(
            actorUserId = request.actorUserId,
            ownerScopeHash = request.portfolioContext.ownerScopeHash,
            portfolioSource = request.portfolioContext.source.name,
            evaluationAsOf = request.evaluationAsOf,
        )

    private fun loadStored(
        actorUserId: String,
        ownerScopeHash: String,
        portfolioSource: String,
        evaluationAsOf: Instant,
    ): RiskMetricBundle {
        val rows =
            actorScopedReadQuery.query(
                actorUserId = actorUserId,
                sql =
                    """
                    SELECT daily_loss_rate,
                           max_drawdown,
                           annualized_volatility,
                           completeness,
                           observed_at,
                           received_at,
                           source_version,
                           source_ref
                    FROM latest_deterministic_risk_observations
                    WHERE owner_scope_hash = ?
                      AND portfolio_source = ?
                    LIMIT 2
                    """.trimIndent(),
                binder = { statement ->
                    statement.setString(1, ownerScopeHash)
                    statement.setString(2, portfolioSource)
                },
            ) { result ->
                StoredRiskRow(
                    dailyLossRate = result.getBigDecimal("daily_loss_rate"),
                    maxDrawdown = result.getBigDecimal("max_drawdown"),
                    annualizedVolatility = result.getBigDecimal("annualized_volatility"),
                    completeness = result.getString("completeness"),
                    observedAt = result.getObject("observed_at", OffsetDateTime::class.java).toInstant(),
                    receivedAt = result.getObject("received_at", OffsetDateTime::class.java).toInstant(),
                    sourceVersion = result.getString("source_version"),
                    sourceRef = result.getString("source_ref"),
                )
            }
        val row = rows.singleOrNull() ?: return unavailableRisk(MetricCell.Missing(MetricIssueCode.SOURCE_MISSING))
        if (
            row.completeness != COMPLETE ||
            row.dailyLossRate == null ||
            row.maxDrawdown == null ||
            row.annualizedVolatility == null
        ) {
            return unavailableRisk(MetricCell.Incomplete(MetricIssueCode.SOURCE_INCOMPLETE))
        }
        val assessment =
            try {
                freshnessPolicy.assess(row.observedAt, evaluationAsOf)
            } catch (_: TradingCalendarUnavailableException) {
                return unavailableRisk(MetricCell.Missing(MetricIssueCode.SOURCE_MISSING))
            }
        val unavailable =
            when (assessment.state) {
                FreshnessState.FRESH -> null
                FreshnessState.STALE ->
                    MetricCell.Stale(
                        row.observedAt,
                        assessment.freshUntil,
                        MetricIssueCode.SOURCE_STALE,
                    )

                FreshnessState.FUTURE ->
                    MetricCell.Stale(
                        row.observedAt,
                        assessment.freshUntil,
                        MetricIssueCode.SOURCE_FUTURE_TIMESTAMP,
                    )
            }
        if (unavailable != null) {
            return unavailableRisk(unavailable)
        }
        return RiskMetricBundle(
            dailyLossRate = row.available(row.dailyLossRate, assessment.freshUntil),
            maxDrawdown = row.available(row.maxDrawdown, assessment.freshUntil),
            annualizedVolatility = row.available(row.annualizedVolatility, assessment.freshUntil),
        )
    }
}

/**
 * authoritative daily ledger가 evaluationAsOf까지 완결된 latest row일 때만 0을 포함한 count를 Available로 반환한다.
 */
@Repository
class JdbcDailyOrderCountAdapter(
    private val actorScopedReadQuery: ActorScopedReadQuery,
) : OrderMetricPort {
    override fun loadDailyOrderCount(request: EvaluationSourceRequest): MetricCell<MetricValue> {
        val evaluationDate = request.evaluationAsOf.atZone(SEOUL).toLocalDate()
        val rows =
            actorScopedReadQuery.query(
                actorUserId = request.actorUserId,
                sql =
                    """
                    SELECT order_count,
                           covered_through,
                           completeness,
                           observed_at,
                           received_at,
                           source_version,
                           source_ref
                    FROM latest_daily_order_count_observations
                    WHERE owner_scope_hash = ?
                      AND portfolio_source = ?
                      AND trading_date = ?
                    LIMIT 2
                    """.trimIndent(),
                binder = { statement ->
                    statement.setString(1, request.portfolioContext.ownerScopeHash)
                    statement.setString(2, request.portfolioContext.source.name)
                    statement.setObject(3, evaluationDate)
                },
            ) { result ->
                StoredOrderCountRow(
                    orderCount = result.getObject("order_count", Int::class.javaObjectType),
                    coveredThrough = result.getObject("covered_through", OffsetDateTime::class.java).toInstant(),
                    completeness = result.getString("completeness"),
                    observedAt = result.getObject("observed_at", OffsetDateTime::class.java).toInstant(),
                    receivedAt = result.getObject("received_at", OffsetDateTime::class.java).toInstant(),
                    sourceVersion = result.getString("source_version"),
                    sourceRef = result.getString("source_ref"),
                )
            }
        val row = rows.singleOrNull() ?: return MetricCell.Missing(MetricIssueCode.SOURCE_MISSING)
        if (
            row.completeness != COMPLETE ||
            row.orderCount == null ||
            // 저장된 관측은 판단보다 반드시 조금 앞선다. `coveredThrough >= evaluationAsOf`를 그대로
            // 요구하면 만족할 방법이 없다 — writer가 `coveredThrough <= observedAt`을, readiness가
            // `observedAt <= evaluationAsOf`를 요구하므로 셋이 동시에 성립하려면 세 시각이 정확히
            // 같아야 하고, 그건 고정 시계에서만 가능하다. 실제 시계에서는 이 규칙이 켜져 있는 한
            // 모든 주문이 HOLD로 닫혔다. 다른 지표와 같은 방식으로 유효 창 안의 지연만 허용한다.
            row.coveredThrough.isBefore(request.evaluationAsOf.minus(ORDER_COUNT_COVERAGE_LAG))
        ) {
            return MetricCell.Incomplete(MetricIssueCode.SOURCE_INCOMPLETE)
        }
        return MetricCell.Available(
            value = MetricValue.Whole(row.orderCount.toLong(), MetricUnit.COUNT),
            observedAt = row.observedAt,
            retrievedAt = row.receivedAt,
            // coveredThrough는 point-in-time completeness 증거이고 Decision의 최대 재사용 창은 별도 고정 계약이다.
            freshUntil = request.evaluationAsOf.plus(ORDER_COUNT_DECISION_VALIDITY),
            source = MetricSource.INTERNAL,
            sourceRef = row.sourceRef,
            sourceVersion = row.sourceVersion,
        )
    }
}

private data class StoredInstrumentRow(
    val symbol: String,
    val isEtfEtn: Boolean,
    val isGoldEtfEtn: Boolean,
    val productRiskScore: BigDecimal?,
    val catalogVersion: String,
    val observedAt: Instant,
    val receivedAt: Instant,
    val completeness: String,
    val sourceVersion: String,
    val sourceRef: String,
)

private val ORDER_COUNT_DECISION_VALIDITY: Duration = Duration.ofMinutes(10)

// 일일 주문 수 관측이 판단 시각보다 뒤처져도 되는 한계. 이 창을 넘긴 관측은 계속 INCOMPLETE다.
// 이 배포에서 주문을 내는 주체는 자동운용뿐이고 세션당 논리주문이 1건으로 묶여 있으므로, 이
// 창 안에서 놓칠 수 있는 주문 수는 유계다. 판단의 재사용 창과 같은 값을 쓴다.
private val ORDER_COUNT_COVERAGE_LAG: Duration = ORDER_COUNT_DECISION_VALIDITY

private data class StoredRiskRow(
    val dailyLossRate: BigDecimal?,
    val maxDrawdown: BigDecimal?,
    val annualizedVolatility: BigDecimal?,
    val completeness: String,
    val observedAt: Instant,
    val receivedAt: Instant,
    val sourceVersion: String,
    val sourceRef: String,
) {
    fun available(
        value: BigDecimal,
        freshUntil: Instant,
    ): MetricCell.Available<MetricValue> {
        val normalized = value.stripTrailingZeros()
        return MetricCell.Available(
            value =
                MetricValue.Decimal(
                    value = value,
                    declaredScale = normalized.scale().coerceAtLeast(0),
                    unit = MetricUnit.RATIO,
                ),
            observedAt = observedAt,
            retrievedAt = receivedAt,
            freshUntil = freshUntil,
            source = MetricSource.RISK_SNAPSHOT,
            sourceRef = sourceRef,
            sourceVersion = sourceVersion,
        )
    }
}

private data class StoredOrderCountRow(
    val orderCount: Int?,
    val coveredThrough: Instant,
    val completeness: String,
    val observedAt: Instant,
    val receivedAt: Instant,
    val sourceVersion: String,
    val sourceRef: String,
)

private fun unavailableRisk(cell: MetricCell<MetricValue>): RiskMetricBundle =
    RiskMetricBundle(
        dailyLossRate = cell,
        maxDrawdown = cell,
        annualizedVolatility = cell,
    )

private const val COMPLETE = "COMPLETE"
private val CATALOG_VALID_UNTIL_SUPERSEDED: Instant = Instant.parse("9999-12-31T23:59:59Z")
private val SEOUL: ZoneId = ZoneId.of("Asia/Seoul")
