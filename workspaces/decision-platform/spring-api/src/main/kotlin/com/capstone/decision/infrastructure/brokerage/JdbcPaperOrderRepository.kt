package com.capstone.decision.infrastructure.brokerage

import com.capstone.decision.application.brokerage.BrokerageDecisionConflictException
import com.capstone.decision.application.brokerage.BrokerageDecisionNotFoundException
import com.capstone.decision.application.brokerage.BrokerageFieldViolation
import com.capstone.decision.application.brokerage.BrokerageIdempotencyConflictException
import com.capstone.decision.application.brokerage.BrokeragePersistenceReplayException
import com.capstone.decision.application.brokerage.BrokerageUnavailableException
import com.capstone.decision.application.brokerage.BrokerageValidationException
import com.capstone.decision.application.brokerage.DecisionExpiredException
import com.capstone.decision.application.brokerage.StoredBrokerageIdempotencyResult
import com.capstone.decision.application.brokerage.paper.PaperBalancePositionProjection
import com.capstone.decision.application.brokerage.paper.PaperDataStaleException
import com.capstone.decision.application.brokerage.paper.PaperOrderContext
import com.capstone.decision.application.brokerage.paper.PaperOrderPersistencePort
import com.capstone.decision.application.brokerage.paper.PaperOrderWriteRequest
import com.capstone.decision.application.brokerage.paper.StoredPaperBalance
import com.capstone.decision.application.risk.KillSwitchBlockedException
import com.capstone.decision.domain.brokerage.PaperFillDecision
import com.capstone.decision.domain.brokerage.PaperPriceObservation
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Transactional
import tools.jackson.databind.ObjectMapper
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.UUID

/**
 * S3.2 paper adapter는 capability-protected 함수만 호출하고 paper table 직접 DML을 하지 않는다.
 * raw idempotency key, provider 응답, 계좌번호는 payload와 로그 어느 경계에도 전달하지 않는다.
 */
@Repository
class JdbcPaperOrderRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val objectMapper: ObjectMapper,
    private val properties: BrokerageProperties,
) : PaperOrderPersistencePort {
    override fun findIdempotencyResult(
        scopeHash: String,
        ownerScopeHash: String,
        now: Instant,
    ): StoredBrokerageIdempotencyResult? =
        jdbc()
            .query(
                """
                SELECT request_hash, result_canonical_json, expires_at
                FROM find_paper_order_idempotency_result(
                  :scopeHash, :ownerScopeHash, :now, :capabilityToken
                )
                """.trimIndent(),
                mapOf(
                    "scopeHash" to scopeHash,
                    "ownerScopeHash" to ownerScopeHash,
                    "now" to now.utc(),
                    "capabilityToken" to properties.databaseCapabilityToken,
                ),
            ) { row, _ ->
                StoredBrokerageIdempotencyResult(
                    requestHash = row.getString("request_hash"),
                    projectionCanonicalJson = row.getString("result_canonical_json"),
                    expiresAt = row.getObject("expires_at", OffsetDateTime::class.java).toInstant(),
                )
            }.singleOrNull()

    override fun findOrderContext(
        actorUserId: String,
        decisionId: String,
    ): PaperOrderContext? =
        jdbc()
            .query(
                """
                SELECT decision_id, evaluation_id, portfolio_source, outcome, mode,
                       can_submit_order, enforcement_action, valid_until,
                       snapshot_artifact_canonical_json, portfolio_owner_scope_hash,
                       invalidated, consumed_by_order_id, account_id, account_status,
                       quote_observation_id, quote_price_krw,
                       quote_previous_close_krw, quote_completeness, quote_observed_at
                FROM read_paper_order_context(
                  :actorUserId, :decisionId, :capabilityToken
                )
                """.trimIndent(),
                mapOf(
                    "actorUserId" to actorUserId,
                    "decisionId" to decisionId,
                    "capabilityToken" to properties.databaseCapabilityToken,
                ),
            ) { row, _ ->
                val quoteId = row.getString("quote_observation_id")
                PaperOrderContext(
                    decisionId = row.getString("decision_id"),
                    evaluationId = row.getString("evaluation_id"),
                    portfolioSource = row.getString("portfolio_source"),
                    outcome = row.getString("outcome"),
                    mode = row.getString("mode"),
                    canSubmitOrder = row.getBoolean("can_submit_order"),
                    enforcementAction = row.getString("enforcement_action"),
                    validUntil = row.getObject("valid_until", OffsetDateTime::class.java).toInstant(),
                    snapshotArtifactCanonicalJson = row.getString("snapshot_artifact_canonical_json"),
                    portfolioOwnerScopeHash = row.getString("portfolio_owner_scope_hash"),
                    invalidated = row.getBoolean("invalidated"),
                    consumedByOrderId = row.getString("consumed_by_order_id"),
                    accountId = row.getString("account_id"),
                    accountStatus = row.getString("account_status"),
                    quote =
                        quoteId?.let {
                            PaperPriceObservation(
                                observationId = it,
                                lastPriceKrw = row.getLongOrNull("quote_price_krw"),
                                previousCloseKrw = row.getLongOrNull("quote_previous_close_krw"),
                                completeness = row.getString("quote_completeness"),
                                observedAt =
                                    row
                                        .getObject("quote_observed_at", OffsetDateTime::class.java)
                                        .toInstant(),
                            )
                        },
                )
            }.singleOrNull()

    @Transactional
    override fun persist(request: PaperOrderWriteRequest) {
        val result =
            jdbc()
                .query(
                    """
                    SELECT operation_outcome, projection_canonical_json
                    FROM create_paper_order(
                      CAST(:payloadJson AS jsonb),
                      :capabilityToken
                    )
                    """.trimIndent(),
                    mapOf(
                        "payloadJson" to createOrderPayload(request),
                        "capabilityToken" to properties.databaseCapabilityToken,
                    ),
                ) { row, _ ->
                    CreateOrderFunctionResult(
                        outcome = row.getString("operation_outcome"),
                        projectionCanonicalJson = row.getString("projection_canonical_json"),
                    )
                }.singleOrNull()
                ?: throw BrokerageUnavailableException("Paper order function returned no result.")
        when (result.outcome) {
            "CREATED" -> Unit
            "REPLAY" ->
                throw BrokeragePersistenceReplayException(
                    requireNotNull(result.projectionCanonicalJson),
                )
            "IDEMPOTENCY_CONFLICT" -> throw BrokerageIdempotencyConflictException()
            "DECISION_NOT_FOUND" -> throw BrokerageDecisionNotFoundException()
            "DECISION_EXPIRED" -> throw DecisionExpiredException()
            "DECISION_CONFLICT" -> throw BrokerageDecisionConflictException()
            "RISK_BLOCKED" -> throw KillSwitchBlockedException()
            "DATA_STALE" -> throw PaperDataStaleException()
            "VALIDATION_ERROR" ->
                throw BrokerageValidationException(
                    listOf(BrokerageFieldViolation("/orderIntent", "DATABASE_CONTRACT_REJECTED")),
                )
            "ACTOR_UNAUTHORIZED", "BROKERAGE_UNAVAILABLE" ->
                throw BrokerageUnavailableException("Paper database security boundary rejected the request.")
            else -> throw BrokerageUnavailableException("Paper order function returned an unknown outcome.")
        }
    }

    override fun findOwnedBalance(
        actorUserId: String,
        accountId: String,
    ): StoredPaperBalance? =
        jdbc()
            .query(
                """
                SELECT account_id, cash_krw, total_equity_krw,
                       positions_json::text AS positions_json, as_of
                FROM read_paper_balance_projection(
                  :actorUserId, :accountId, :capabilityToken
                )
                """.trimIndent(),
                mapOf(
                    "actorUserId" to actorUserId,
                    "accountId" to accountId,
                    "capabilityToken" to properties.databaseCapabilityToken,
                ),
            ) { row, _ ->
                StoredPaperBalance(
                    accountId = row.getString("account_id"),
                    cashKrw = row.getLong("cash_krw"),
                    totalEquityKrw = row.getLong("total_equity_krw"),
                    positions = parsePositions(row.getString("positions_json")),
                    asOf = row.getObject("as_of", OffsetDateTime::class.java).toInstant(),
                )
            }.singleOrNull()

    private fun createOrderPayload(request: PaperOrderWriteRequest): String {
        val intent = request.command.orderIntent
        val quote = requireNotNull(request.context.quote)
        val fill = request.fillDecision as? PaperFillDecision.Filled
        return objectMapper.writeValueAsString(
            mapOf<String, Any?>(
                "actorUserId" to request.actor.userId,
                "actorRole" to request.actor.role,
                "securityVersion" to request.actor.securityVersion,
                "requestId" to request.actor.requestId,
                "decisionId" to request.command.decisionId,
                "orderId" to request.orderId,
                "observedKillSwitchGeneration" to request.observedKillSwitchGeneration,
                "idempotencyScopeHash" to request.idempotency.scopeHash,
                "idempotencyOwnerScopeHash" to request.idempotency.ownerScopeHash,
                "requestHash" to request.idempotency.requestHash,
                "accountId" to requireNotNull(request.context.accountId),
                "accountScopeHash" to request.context.portfolioOwnerScopeHash,
                "quoteObservationId" to quote.observationId,
                "symbol" to intent.symbol,
                "side" to intent.side,
                "orderType" to intent.orderType,
                "quantity" to intent.quantity,
                "submittedPriceKrw" to intent.estimatedPrice.takeIf { intent.orderType == "LIMIT" },
                "orderIntent" to
                    mapOf(
                        "symbol" to intent.symbol,
                        "side" to intent.side,
                        "orderType" to intent.orderType,
                        "quantity" to intent.quantity.toString(),
                        "estimatedPrice" to intent.estimatedPrice.toString(),
                        "estimatedAmount" to intent.estimatedAmount.toString(),
                        "timeframe" to intent.timeframe,
                        "strategyId" to intent.strategyId,
                    ),
                "resultCanonicalJson" to request.projectionCanonicalJson,
                "warningsAccepted" to request.command.userAcknowledgement.warningsAccepted,
                "status" to request.projection.status,
                "fillPriceKrw" to fill?.priceKrw,
                "fillAmountKrw" to fill?.amountKrw,
                "priceBasis" to
                    (fill?.priceBasis?.name ?: priceBasis(quote)),
                "slippageBps" to (fill?.slippageBps ?: 0),
                "feeModel" to fill?.feeModel?.name,
                "quoteObservedAt" to quote.observedAt.toString(),
                "priceMaxAgeSeconds" to request.priceMaxAgeSeconds,
                "submittedAt" to request.projection.submittedAt.toString(),
                "createdAt" to request.createdAt.toString(),
                "orderEventId" to id("oev"),
                "paperEventId" to fill?.let { id("pev") },
                "auditLogId" to id("aud"),
                "outboxEventId" to id("evt"),
            ),
        )
    }

    private fun priceBasis(quote: PaperPriceObservation): String = if (quote.lastPriceKrw != null) "LAST_QUOTE" else "PREVIOUS_CLOSE"

    private fun parsePositions(value: String): List<PaperBalancePositionProjection> {
        val root = objectMapper.readTree(value)
        if (!root.isArray || root.size() > 1_000) {
            throw BrokerageUnavailableException("Paper positions violated the bounded contract.")
        }
        return root
            .values()
            .asSequence()
            .map { node ->
                PaperBalancePositionProjection(
                    symbol = node.path("symbol").stringValue(),
                    quantity = node.path("quantity").longValue(),
                    marketValueKrw = node.path("marketValueKrw").longValue(),
                    averagePriceKrw = node.path("averagePriceKrw").longValue(),
                )
            }.sortedBy(PaperBalancePositionProjection::symbol)
            .toList()
    }

    private fun java.sql.ResultSet.getLongOrNull(column: String): Long? = getObject(column)?.let { getLong(column) }

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: error("Paper brokerage JDBC access is unavailable without a configured DataSource.")

    private fun id(prefix: String): String = "${prefix}_${UUID.randomUUID().toString().replace("-", "")}"

    private fun Instant.utc(): OffsetDateTime = OffsetDateTime.ofInstant(this, ZoneOffset.UTC)

    private data class CreateOrderFunctionResult(
        val outcome: String,
        val projectionCanonicalJson: String?,
    )
}
