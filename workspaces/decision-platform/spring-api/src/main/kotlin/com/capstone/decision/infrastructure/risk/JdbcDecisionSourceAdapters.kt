package com.capstone.decision.infrastructure.risk

import com.capstone.decision.application.risk.port.EvaluationSourceRequest
import com.capstone.decision.application.risk.port.InstrumentCatalogPort
import com.capstone.decision.application.risk.port.InstrumentSnapshot
import com.capstone.decision.application.risk.port.OrderMetricPort
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

    override fun load(request: EvaluationSourceRequest): RiskMetricBundle {
        val rows =
            actorScopedReadQuery.query(
                actorUserId = request.actorUserId,
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
                    statement.setString(1, request.portfolioContext.ownerScopeHash)
                    statement.setString(2, request.portfolioContext.source.name)
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
                freshnessPolicy.assess(row.observedAt, request.evaluationAsOf)
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
            row.coveredThrough.isBefore(request.evaluationAsOf)
        ) {
            return MetricCell.Incomplete(MetricIssueCode.SOURCE_INCOMPLETE)
        }
        return MetricCell.Available(
            value = MetricValue.Whole(row.orderCount.toLong(), MetricUnit.COUNT),
            observedAt = row.observedAt,
            retrievedAt = row.receivedAt,
            freshUntil = row.coveredThrough,
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
