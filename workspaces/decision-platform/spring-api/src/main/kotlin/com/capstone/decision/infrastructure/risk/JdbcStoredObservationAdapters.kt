package com.capstone.decision.infrastructure.risk

import com.capstone.decision.application.risk.port.BalancePort
import com.capstone.decision.application.risk.port.BalanceSnapshot
import com.capstone.decision.application.risk.port.EvaluationSourceRequest
import com.capstone.decision.application.risk.port.MarginPort
import com.capstone.decision.application.risk.port.PortfolioContextPort
import com.capstone.decision.application.risk.port.PortfolioContextRef
import com.capstone.decision.application.risk.port.PortfolioContextResolution
import com.capstone.decision.application.risk.port.PortfolioContextUnavailableReason
import com.capstone.decision.application.risk.port.PortfolioPosition
import com.capstone.decision.application.risk.port.PricePort
import com.capstone.decision.domain.risk.CanonicalJson
import com.capstone.decision.domain.risk.EvaluationBounds
import com.capstone.decision.domain.risk.MetricCell
import com.capstone.decision.domain.risk.MetricIssueCode
import com.capstone.decision.domain.risk.MetricSource
import com.capstone.decision.domain.risk.MetricUnit
import com.capstone.decision.domain.risk.MetricValue
import com.capstone.decision.domain.risk.PortfolioSource
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Component
import org.springframework.stereotype.Repository
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.time.Duration
import java.time.OffsetDateTime

/**
 * JWT actor와 선택한 source만으로 서버 소유 portfolio context를 정한다.
 * raw account 식별자는 응답·hash·로그 경계로 내보내지 않는다.
 */
@Repository
class JdbcPortfolioContextAdapter(
    private val reader: StoredPortfolioObservationReader,
) : PortfolioContextPort {
    override fun resolve(
        actorUserId: String,
        source: PortfolioSource,
    ): PortfolioContextResolution {
        val contexts =
            when (source) {
                PortfolioSource.KIS_MOCK ->
                    reader
                        .kisRows(actorUserId, limit = 2)
                        .map { row ->
                            PortfolioContextRef(
                                opaqueRef = row.ownerScopeHash,
                                source = source,
                                ownerScopeHash = row.ownerScopeHash,
                            )
                        }

                PortfolioSource.INTERNAL_PAPER ->
                    reader
                        .paperRows(actorUserId, limit = 2)
                        .map { row ->
                            PortfolioContextRef(
                                opaqueRef = row.ownerScopeHash,
                                source = source,
                                ownerScopeHash = row.ownerScopeHash,
                            )
                        }
            }
        return when (contexts.size) {
            0 -> PortfolioContextResolution.Unavailable(PortfolioContextUnavailableReason.MISSING)
            1 -> PortfolioContextResolution.Available(contexts.single())
            else -> PortfolioContextResolution.Unavailable(PortfolioContextUnavailableReason.CONFLICT)
        }
    }
}

/**
 * append-only market quote projection만 읽는다. 이 adapter에는 provider HTTP fallback이나 production seed가 없다.
 */
@Repository
class JdbcMarketQuoteAdapter(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
) : PricePort {
    override fun load(request: EvaluationSourceRequest): MetricCell<MetricValue> {
        val rows =
            jdbc()
                .query(
                    """
                    SELECT price_krw, completeness, observed_at, received_at, source_version, source_ref
                    FROM latest_market_quote_observations
                    WHERE symbol = :symbol
                      AND source = 'KIS_MOCK'
                    LIMIT 1
                    """.trimIndent(),
                    mapOf("symbol" to request.orderIntent.symbol),
                ) { result, _ ->
                    val observedAt = result.getObject("observed_at", OffsetDateTime::class.java).toInstant()
                    StoredQuoteRow(
                        value =
                            MetricCell.Available(
                                value = MetricValue.Whole(result.getLong("price_krw"), MetricUnit.KRW),
                                observedAt = observedAt,
                                retrievedAt = result.getObject("received_at", OffsetDateTime::class.java).toInstant(),
                                freshUntil = observedAt.plus(PRICE_TTL),
                                source = MetricSource.KIS_MOCK,
                                sourceRef = result.getString("source_ref"),
                                sourceVersion = result.getString("source_version"),
                            ),
                        complete = result.getString("completeness") == COMPLETE,
                    )
                }
        val row = rows.singleOrNull() ?: return MetricCell.Missing(MetricIssueCode.SOURCE_MISSING)
        return if (row.complete) {
            row.value
        } else {
            MetricCell.Incomplete(MetricIssueCode.SOURCE_INCOMPLETE)
        }
    }

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: error("Stored quote JDBC access is unavailable without a configured DataSource.")

    private companion object {
        val PRICE_TTL: Duration = Duration.ofSeconds(300)
    }
}

private data class StoredQuoteRow(
    val value: MetricCell.Available<MetricValue>,
    val complete: Boolean,
)

/**
 * KIS_MOCK balance는 sanitized stored observation만 소비하며 account나 provider payload를 노출하지 않는다.
 */
@Repository
class JdbcKisMockBalanceAdapter(
    private val reader: StoredPortfolioObservationReader,
) : BalancePort {
    override val source: PortfolioSource = PortfolioSource.KIS_MOCK

    override fun load(request: EvaluationSourceRequest): MetricCell<BalanceSnapshot> {
        require(request.portfolioContext.source == source)
        val row =
            reader
                .kisRows(request.actorUserId, limit = 2)
                .singleOrNull { it.ownerScopeHash == request.portfolioContext.ownerScopeHash }
                ?: return MetricCell.Missing(MetricIssueCode.BROKERAGE_UNAVAILABLE)
        if (row.completeness != COMPLETE || !row.positionsComplete || row.positionCount != row.positions.size) {
            return MetricCell.Incomplete(MetricIssueCode.SOURCE_INCOMPLETE)
        }
        return row.balanceCell(source, MetricSource.KIS_MOCK)
    }
}

/**
 * INTERNAL_PAPER는 owner-scoped ledger projection을 읽고 KIS_MOCK으로 자동 fallback하지 않는다.
 */
@Repository
class JdbcInternalPaperBalanceAdapter(
    private val reader: StoredPortfolioObservationReader,
) : BalancePort {
    override val source: PortfolioSource = PortfolioSource.INTERNAL_PAPER

    override fun load(request: EvaluationSourceRequest): MetricCell<BalanceSnapshot> {
        require(request.portfolioContext.source == source)
        val row =
            reader
                .paperRows(request.actorUserId, limit = 2)
                .singleOrNull { it.ownerScopeHash == request.portfolioContext.ownerScopeHash }
                ?: return MetricCell.Missing(MetricIssueCode.PAPER_PORTFOLIO_UNAVAILABLE)
        if (row.completeness != COMPLETE || !row.positionsComplete || row.positionCount != row.positions.size) {
            return MetricCell.Incomplete(MetricIssueCode.SOURCE_INCOMPLETE)
        }
        return row.balanceCell(source, MetricSource.INTERNAL_PAPER)
    }
}

/**
 * margin 값은 선택한 portfolio source와 같은 stored row에서만 읽는다.
 * INTERNAL_PAPER schema에 authoritative 값이 없으면 0을 합성하지 않고 typed missing으로 닫는다.
 */
@Repository
class JdbcStoredMarginAdapter(
    private val reader: StoredPortfolioObservationReader,
) : MarginPort {
    override fun load(request: EvaluationSourceRequest): MetricCell<MetricValue> {
        val row =
            when (request.portfolioContext.source) {
                PortfolioSource.KIS_MOCK -> reader.kisRows(request.actorUserId, limit = 2)
                PortfolioSource.INTERNAL_PAPER -> reader.paperRows(request.actorUserId, limit = 2)
            }.singleOrNull { it.ownerScopeHash == request.portfolioContext.ownerScopeHash }
                ?: return MetricCell.Missing(MetricIssueCode.SOURCE_MISSING)
        val marginRequirementKrw =
            row.marginRequirementKrw
                ?: return MetricCell.Missing(MetricIssueCode.SOURCE_MISSING)
        if (row.completeness != COMPLETE) {
            return MetricCell.Incomplete(MetricIssueCode.SOURCE_INCOMPLETE)
        }
        return MetricCell.Available(
            value = MetricValue.Whole(marginRequirementKrw, MetricUnit.KRW),
            observedAt = row.observedAt,
            retrievedAt = row.receivedAt,
            freshUntil = row.observedAt.plus(BALANCE_TTL),
            source =
                when (request.portfolioContext.source) {
                    PortfolioSource.KIS_MOCK -> MetricSource.KIS_MOCK
                    PortfolioSource.INTERNAL_PAPER -> MetricSource.INTERNAL_PAPER
                },
            sourceRef = row.sourceRef,
            sourceVersion = row.sourceVersion,
        )
    }
}

@Component
class StoredPortfolioObservationReader(
    private val actorScopedReadQuery: ActorScopedReadQuery,
    private val objectMapper: ObjectMapper,
) {
    fun kisRows(
        actorUserId: String,
        limit: Int,
    ): List<StoredPortfolioRow> {
        validateQuery(actorUserId, limit)
        return actorScopedReadQuery.query(
            actorUserId = actorUserId,
            sql =
                """
                SELECT balance.account_scope_hash,
                       balance.cash_krw,
                       balance.portfolio_equity_krw,
                       balance.margin_requirement_krw,
                       balance.completeness,
                       balance.position_count,
                       balance.positions_json::text AS positions_json,
                       balance.observed_at,
                       balance.received_at,
                       balance.source_version,
                       balance.source_ref,
                       balance.artifact_hash
                FROM latest_portfolio_balance_observations balance
                WHERE balance.source = 'KIS_MOCK'
                ORDER BY balance.account_scope_hash
                LIMIT ?
                """.trimIndent(),
            binder = { statement -> statement.setInt(1, limit) },
        ) { result ->
            val parsedPositions = parsePositions(result.getString("positions_json"))
            StoredPortfolioRow(
                ownerScopeHash = result.getString("account_scope_hash"),
                cashKrw = result.getLong("cash_krw"),
                portfolioEquityKrw = result.getLong("portfolio_equity_krw"),
                marginRequirementKrw = result.getLong("margin_requirement_krw"),
                completeness = result.getString("completeness"),
                positionCount = result.getInt("position_count"),
                positions = parsedPositions.positions,
                positionsComplete = parsedPositions.complete,
                observedAt = result.getObject("observed_at", OffsetDateTime::class.java).toInstant(),
                receivedAt = result.getObject("received_at", OffsetDateTime::class.java).toInstant(),
                sourceVersion = result.getString("source_version"),
                sourceRef = result.getString("source_ref"),
                revision = result.getString("artifact_hash"),
            )
        }
    }

    fun paperRows(
        actorUserId: String,
        limit: Int,
    ): List<StoredPortfolioRow> {
        validateQuery(actorUserId, limit)
        return actorScopedReadQuery.query(
            actorUserId = actorUserId,
            sql =
                """
                SELECT paper.account_id,
                       paper.cash_krw,
                       paper.portfolio_equity_krw,
                       paper.margin_requirement_krw,
                       paper.positions_json::text AS positions_json,
                       paper.observed_at
                FROM active_paper_portfolio_projection paper
                ORDER BY paper.account_id
                LIMIT ?
                """.trimIndent(),
            binder = { statement -> statement.setInt(1, limit) },
        ) { result ->
            val accountId = result.getString("account_id")
            val ownerScopeHash =
                CanonicalJson.sha256(
                    CanonicalJson.encode(
                        mapOf(
                            "actorUserId" to actorUserId,
                            "paperAccountId" to accountId,
                            "purpose" to PAPER_OWNER_SCOPE_PURPOSE,
                        ),
                    ),
                )
            val positionsJson = result.getString("positions_json")
            val parsedPositions = parsePositions(positionsJson)
            val observedAt = result.getObject("observed_at", OffsetDateTime::class.java).toInstant()
            val cashKrw = result.getLong("cash_krw")
            val equityKrw = result.getLong("portfolio_equity_krw")
            val marginKrw =
                result
                    .getObject("margin_requirement_krw", Long::class.javaObjectType)
            val sourceRef =
                CanonicalJson.sha256(
                    CanonicalJson.encode(
                        mapOf(
                            "cashKrw" to cashKrw,
                            "marginRequirementKrw" to marginKrw,
                            "observedAt" to observedAt,
                            "ownerScopeHash" to ownerScopeHash,
                            "portfolioEquityKrw" to equityKrw,
                            "positionsJson" to positionsJson,
                            "sourceVersion" to PAPER_SOURCE_VERSION,
                        ),
                    ),
                )
            StoredPortfolioRow(
                ownerScopeHash = ownerScopeHash,
                cashKrw = cashKrw,
                portfolioEquityKrw = equityKrw,
                marginRequirementKrw = marginKrw,
                completeness = if (parsedPositions.complete) COMPLETE else "PARTIAL",
                positionCount = parsedPositions.count,
                positions = parsedPositions.positions,
                positionsComplete = parsedPositions.complete,
                observedAt = observedAt,
                receivedAt = observedAt,
                sourceVersion = PAPER_SOURCE_VERSION,
                sourceRef = sourceRef,
                revision = sourceRef,
            )
        }
    }

    private fun parsePositions(value: String): ParsedPositions {
        val root = objectMapper.readTree(value)
        check(root.isArray && root.size() <= EvaluationBounds.MAX_POSITIONS) {
            "Stored portfolio positions violated the bounded read-model contract."
        }
        val rawPositions = root.values().map(::parsePosition).toList()
        val positions = rawPositions.filterNotNull()
        check(positions.map(PortfolioPosition::symbol).distinct().size == positions.size) {
            "Stored portfolio positions contain duplicate symbols."
        }
        return ParsedPositions(
            positions = positions.sortedBy(PortfolioPosition::symbol),
            count = root.size(),
            complete = rawPositions.all { it != null },
        )
    }

    private fun parsePosition(node: JsonNode): PortfolioPosition? {
        check(
            node.isObject &&
                node
                    .properties()
                    .asSequence()
                    .map { it.key }
                    .toSet() ==
                setOf("symbol", "quantity", "marketValueKrw", "isGoldEtfEtn"),
        ) {
            "Stored portfolio position shape is invalid."
        }
        val classification = node.path("isGoldEtfEtn")
        if (!classification.isBoolean) {
            return null
        }
        return PortfolioPosition(
            symbol = node.path("symbol").stringValue(),
            quantity = node.path("quantity").longValue(),
            marketValueKrw = node.path("marketValueKrw").longValue(),
            isGoldEtfEtn = classification.booleanValue(),
        )
    }

    private fun validateQuery(
        actorUserId: String,
        limit: Int,
    ) {
        require(actorUserId.isNotBlank() && actorUserId.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(limit in 1..2)
    }

    private companion object {
        const val PAPER_OWNER_SCOPE_PURPOSE = "s2.3-paper-owner-scope-v1"
        const val PAPER_SOURCE_VERSION = "internal-paper-ledger-v1"
    }
}

data class StoredPortfolioRow(
    val ownerScopeHash: String,
    val cashKrw: Long,
    val portfolioEquityKrw: Long,
    val marginRequirementKrw: Long?,
    val completeness: String,
    val positionCount: Int,
    val positions: List<PortfolioPosition>,
    val positionsComplete: Boolean,
    val observedAt: java.time.Instant,
    val receivedAt: java.time.Instant,
    val sourceVersion: String,
    val sourceRef: String,
    val revision: String,
) {
    init {
        require(ownerScopeHash.matches(Regex(EvaluationBounds.SANITIZED_SHA256_PATTERN)))
        require(cashKrw >= 0 && portfolioEquityKrw >= 0)
        require(marginRequirementKrw == null || marginRequirementKrw >= 0)
        require(completeness in setOf(COMPLETE, "PARTIAL"))
        require(positionCount in 0..EvaluationBounds.MAX_POSITIONS)
        require(!receivedAt.isBefore(observedAt))
        require(sourceVersion.isNotBlank() && sourceVersion.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(sourceRef.matches(Regex(EvaluationBounds.SANITIZED_SHA256_PATTERN)))
        require(revision.isNotBlank() && revision.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
    }

    fun balanceCell(
        source: PortfolioSource,
        metricSource: MetricSource,
    ): MetricCell<BalanceSnapshot> =
        MetricCell.Available(
            value =
                BalanceSnapshot(
                    source = source,
                    revision = revision,
                    ownerScopeHash = ownerScopeHash,
                    cashKrw = cashKrw,
                    portfolioEquityKrw = portfolioEquityKrw,
                    positions = positions,
                ),
            observedAt = observedAt,
            retrievedAt = receivedAt,
            freshUntil = observedAt.plus(BALANCE_TTL),
            source = metricSource,
            sourceRef = sourceRef,
            sourceVersion = sourceVersion,
        )
}

private data class ParsedPositions(
    val positions: List<PortfolioPosition>,
    val count: Int,
    val complete: Boolean,
)

private const val COMPLETE = "COMPLETE"
private val BALANCE_TTL: Duration = Duration.ofSeconds(60)
