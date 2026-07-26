package com.capstone.decision.infrastructure.brokerage

import com.capstone.decision.application.brokerage.BrokerageActor
import com.capstone.decision.application.brokerage.BrokerageDecisionConflictException
import com.capstone.decision.application.brokerage.BrokerageDecisionNotFoundException
import com.capstone.decision.application.brokerage.BrokerageFieldViolation
import com.capstone.decision.application.brokerage.BrokerageIdempotencyConflictException
import com.capstone.decision.application.brokerage.BrokerageOrderNotFoundException
import com.capstone.decision.application.brokerage.BrokerageOrderPersistencePort
import com.capstone.decision.application.brokerage.BrokerageOrderWriteRequest
import com.capstone.decision.application.brokerage.BrokeragePersistenceReplayException
import com.capstone.decision.application.brokerage.BrokerageUnavailableException
import com.capstone.decision.application.brokerage.BrokerageValidationException
import com.capstone.decision.application.brokerage.DecisionExpiredException
import com.capstone.decision.application.brokerage.MockBalancePositionProjection
import com.capstone.decision.application.brokerage.OrderDetailProjection
import com.capstone.decision.application.brokerage.OrderableDecision
import com.capstone.decision.application.brokerage.StoredBrokerageIdempotencyResult
import com.capstone.decision.application.brokerage.StoredMockBalance
import com.capstone.decision.application.risk.KillSwitchBlockedException
import com.capstone.decision.domain.brokerage.TickSizePolicy
import com.capstone.decision.domain.brokerage.TickValidation
import com.capstone.decision.domain.risk.OrderIntentSnapshot
import com.capstone.decision.infrastructure.risk.ActorScopedReadQuery
import org.springframework.beans.factory.ObjectProvider
import org.springframework.dao.DuplicateKeyException
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Transactional
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.UUID

/**
 * S3.1 주문 writer는 Decision/Kill Switch/ledger/idempotency를 같은 PostgreSQL transaction에서 판정한다.
 * provider payload, raw 계좌번호, raw idempotency key는 이 adapter 경계로 들어오지 않는다.
 */
@Repository
class JdbcBrokerageOrderRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val actorScopedReadQuery: ActorScopedReadQuery,
    private val objectMapper: ObjectMapper,
    private val properties: BrokerageProperties,
) : BrokerageOrderPersistencePort {
    override fun findIdempotencyResult(
        scopeHash: String,
        ownerScopeHash: String,
        now: Instant,
    ): StoredBrokerageIdempotencyResult? =
        jdbc()
            .query(
                """
                SELECT request_hash, result_canonical_json, expires_at
                FROM find_mock_order_idempotency_result(
                  :scopeHash,
                  :ownerScopeHash,
                  :now
                )
                """.trimIndent(),
                mapOf(
                    "scopeHash" to scopeHash,
                    "ownerScopeHash" to ownerScopeHash,
                    "now" to now.utc(),
                ),
            ) { result, _ ->
                StoredBrokerageIdempotencyResult(
                    requestHash = result.getString("request_hash"),
                    projectionCanonicalJson = result.getString("result_canonical_json"),
                    expiresAt = result.getObject("expires_at", OffsetDateTime::class.java).toInstant(),
                )
            }.singleOrNull()

    @Transactional
    override fun persist(request: BrokerageOrderWriteRequest) {
        val jdbc = jdbc()
        jdbc.queryForObject(
            "SELECT set_config('app.actor_user_id', :actorUserId, true)",
            mapOf("actorUserId" to request.actor.userId),
            String::class.java,
        )
        jdbc.queryForObject(
            "SELECT set_config('app.requested_decision_id', :decisionId, true)",
            mapOf("decisionId" to request.command.decisionId),
            String::class.java,
        )
        lock(jdbc, "mock-order:idempotency:${request.idempotency.scopeHash}", ADVISORY_LOCK_SEED)
        lock(jdbc, "mock-order:decision:${request.command.decisionId}", ADVISORY_LOCK_SEED)
        findIdempotencyResult(
            request.idempotency.scopeHash,
            request.idempotency.ownerScopeHash,
            request.createdAt,
        )?.let { stored ->
            if (stored.requestHash == request.idempotency.requestHash) {
                throw BrokeragePersistenceReplayException(stored.projectionCanonicalJson)
            }
            throw BrokerageIdempotencyConflictException()
        }

        val decision = readOrderableDecision(jdbc) ?: throw BrokerageDecisionNotFoundException()
        validateDecision(request, decision)
        try {
            insertOrder(jdbc, request, decision)
            insertOrderEvent(jdbc, request)
            insertAudit(jdbc, request, decision)
            insertOutbox(jdbc, request, decision)
        } catch (exception: DuplicateKeyException) {
            throw BrokerageDecisionConflictException()
        }
    }

    @Transactional
    override fun findOrderableDecisionAccountId(
        actorUserId: String,
        decisionId: String,
    ): String? {
        val jdbc = jdbc()
        jdbc.queryForObject(
            "SELECT set_config('app.actor_user_id', :actorUserId, true)",
            mapOf("actorUserId" to actorUserId),
            String::class.java,
        )
        jdbc.queryForObject(
            "SELECT set_config('app.requested_decision_id', :decisionId, true)",
            mapOf("decisionId" to decisionId),
            String::class.java,
        )
        return readOrderableDecision(jdbc)?.let { decision ->
            accountId(decision.portfolioOwnerScopeHash)
        }
    }

    override fun findOwnedProjection(
        actorUserId: String,
        orderId: String,
    ): OrderDetailProjection? =
        actorScopedReadQuery
            .query(
                actorUserId = actorUserId,
                requestedOrderId = orderId,
                sql =
                    """
                    SELECT order_id, account_id, brokerage_mode, status, submitted_at, decision_id
                    FROM mock_order_owner_projection
                    WHERE order_id = ?
                    LIMIT 1
                    """.trimIndent(),
                binder = { statement -> statement.setString(1, orderId) },
            ) { result ->
                OrderDetailProjection(
                    orderId = result.getString("order_id"),
                    accountId = result.getString("account_id"),
                    brokerageMode = result.getString("brokerage_mode"),
                    status = result.getString("status"),
                    submittedAt = result.getObject("submitted_at", OffsetDateTime::class.java).toInstant(),
                    decisionId = result.getString("decision_id"),
                )
            }.singleOrNull()

    @Transactional
    override fun cancelOwnedOrder(
        actor: BrokerageActor,
        orderId: String,
        cancelledAt: Instant,
    ): OrderDetailProjection {
        val jdbc = jdbc()
        jdbc.queryForObject(
            "SELECT set_config('app.actor_user_id', :actorUserId, true)",
            mapOf("actorUserId" to actor.userId),
            String::class.java,
        )
        jdbc.queryForObject(
            "SELECT set_config('app.requested_order_id', :orderId, true)",
            mapOf("orderId" to orderId),
            String::class.java,
        )
        lock(jdbc, "mock-order:cancel:$orderId", ADVISORY_LOCK_SEED)
        val current = readOwnedProjection(jdbc) ?: throw BrokerageOrderNotFoundException()
        if (current.status !in CANCELABLE_STATUSES) {
            throw BrokerageDecisionConflictException()
        }
        insertCancelEvent(jdbc, current, actor, cancelledAt)
        insertCancelAudit(jdbc, current, actor, cancelledAt)
        insertCancelOutbox(jdbc, current, actor, cancelledAt)
        return current.copy(status = "CANCEL_REQUESTED")
    }

    override fun findOwnedBalance(
        actorUserId: String,
        accountId: String,
    ): StoredMockBalance? {
        val prefix = accountId.removePrefix("acct_")
        val rows =
            actorScopedReadQuery.query(
                actorUserId = actorUserId,
                sql =
                    """
                    SELECT account_scope_hash,
                           cash_krw,
                           portfolio_equity_krw,
                           margin_requirement_krw,
                           completeness,
                           position_count,
                           positions_json::text AS positions_json,
                           observed_at,
                           source_version
                    FROM latest_portfolio_balance_observations
                    WHERE source = 'KIS_MOCK'
                      AND account_scope_hash LIKE ?
                    ORDER BY account_scope_hash
                    LIMIT 2
                    """.trimIndent(),
                binder = { statement -> statement.setString(1, "$prefix%") },
            ) { result ->
                val scopeHash = result.getString("account_scope_hash")
                StoredMockBalance(
                    accountId = accountId(scopeHash),
                    accountScopeHash = scopeHash,
                    cashKrw = result.getLong("cash_krw"),
                    portfolioEquityKrw = result.getLong("portfolio_equity_krw"),
                    marginRequirementKrw = result.getLong("margin_requirement_krw"),
                    completeness = result.getString("completeness"),
                    positionCount = result.getInt("position_count"),
                    positions = parseBalancePositions(result.getString("positions_json")),
                    observedAt = result.getObject("observed_at", OffsetDateTime::class.java).toInstant(),
                    sourceVersion = result.getString("source_version"),
                )
            }
        if (rows.size > 1) {
            throw BrokerageUnavailableException("Opaque accountId prefix matched multiple KIS_MOCK accounts.")
        }
        return rows.singleOrNull()
    }

    private fun validateDecision(
        request: BrokerageOrderWriteRequest,
        decision: OrderableDecision,
    ) {
        if (decision.portfolioSource != "KIS_MOCK") {
            throw BrokerageValidationException(listOf(BrokerageFieldViolation("/decisionId", "UNSUPPORTED_PORTFOLIO_SOURCE")))
        }
        if (!decision.canSubmitOrder || decision.outcome !in setOf("ALLOW", "WARN")) {
            throw KillSwitchBlockedException()
        }
        if (!decision.validUntil.isAfter(request.createdAt)) {
            throw DecisionExpiredException()
        }
        if (decision.invalidated) {
            throw KillSwitchBlockedException()
        }
        if (decision.consumedByOrderId != null) {
            throw BrokerageDecisionConflictException()
        }
        if (decision.enforcementAction != "NONE" && !request.command.userAcknowledgement.warningsAccepted) {
            throw KillSwitchBlockedException()
        }
        val pinnedOrder = parsePinnedOrderIntent(decision.snapshotArtifactCanonicalJson)
        if (pinnedOrder != request.command.orderIntent) {
            throw BrokerageValidationException(listOf(BrokerageFieldViolation("/orderIntent", "DECISION_MISMATCH")))
        }
        when (
            val tick =
                TickSizePolicy.validate(
                    orderType = request.command.orderIntent.orderType,
                    priceKrw = request.command.orderIntent.estimatedPrice,
                    context = null,
                )
        ) {
            TickValidation.Valid -> Unit
            TickValidation.Unavailable -> throw BrokerageUnavailableException("LIMIT tick table is not verified for S3.1.")
            is TickValidation.Invalid ->
                throw BrokerageValidationException(listOf(BrokerageFieldViolation("/orderIntent/estimatedPrice", tick.reason)))
        }
    }

    private fun parsePinnedOrderIntent(snapshotArtifactCanonicalJson: String): OrderIntentSnapshot {
        val order = objectMapper.readTree(snapshotArtifactCanonicalJson).path("orderIntent")
        return OrderIntentSnapshot(
            symbol = order.path("symbol").stringValue(),
            side = order.path("side").stringValue(),
            orderType = order.path("orderType").stringValue(),
            quantity = order.path("quantity").textLong(),
            estimatedPrice = order.path("estimatedPrice").textLong(),
            estimatedAmount = order.path("estimatedAmount").textLong(),
            timeframe = order.path("timeframe").stringValue(),
            strategyId = order.path("strategyId").stringValue(),
        )
    }

    private fun JsonNode.textLong(): Long =
        try {
            stringValue().toLong()
        } catch (exception: Exception) {
            throw BrokerageUnavailableException("Stored Decision order intent is not readable.", exception)
        }

    private fun readOrderableDecision(jdbc: NamedParameterJdbcTemplate): OrderableDecision? =
        jdbc
            .query(
                """
                SELECT decision_id, evaluation_id, portfolio_source, outcome, mode,
                       can_submit_order, enforcement_action, valid_until,
                       snapshot_artifact_canonical_json, portfolio_owner_scope_hash, invalidated,
                       invalidation_reason_class, consumed_by_order_id
                FROM read_mock_order_decision()
                """.trimIndent(),
                emptyMap<String, Any>(),
            ) { result, _ ->
                OrderableDecision(
                    decisionId = result.getString("decision_id"),
                    evaluationId = result.getString("evaluation_id"),
                    portfolioSource = result.getString("portfolio_source"),
                    outcome = result.getString("outcome"),
                    mode = result.getString("mode"),
                    canSubmitOrder = result.getBoolean("can_submit_order"),
                    enforcementAction = result.getString("enforcement_action"),
                    validUntil = result.getObject("valid_until", OffsetDateTime::class.java).toInstant(),
                    snapshotArtifactCanonicalJson = result.getString("snapshot_artifact_canonical_json"),
                    portfolioOwnerScopeHash = result.getString("portfolio_owner_scope_hash"),
                    invalidated = result.getBoolean("invalidated"),
                    invalidationReasonClass = result.getString("invalidation_reason_class"),
                    consumedByOrderId = result.getString("consumed_by_order_id"),
                )
            }.singleOrNull()

    private fun readOwnedProjection(jdbc: NamedParameterJdbcTemplate): OrderDetailProjection? =
        jdbc
            .query(
                """
                SELECT order_id, account_id, brokerage_mode, status, submitted_at, decision_id
                FROM read_mock_order_owner_projection()
                """.trimIndent(),
                emptyMap<String, Any>(),
            ) { result, _ ->
                OrderDetailProjection(
                    orderId = result.getString("order_id"),
                    accountId = result.getString("account_id"),
                    brokerageMode = result.getString("brokerage_mode"),
                    status = result.getString("status"),
                    submittedAt = result.getObject("submitted_at", OffsetDateTime::class.java).toInstant(),
                    decisionId = result.getString("decision_id"),
                )
            }.singleOrNull()

    private fun insertOrder(
        jdbc: NamedParameterJdbcTemplate,
        request: BrokerageOrderWriteRequest,
        decision: OrderableDecision,
    ) {
        jdbc.update(
            """
            INSERT INTO orders (
              order_id, user_id, account_id, account_scope_hash, decision_id,
              decision_evaluation_id, brokerage_mode, idempotency_scope_hash,
              idempotency_owner_scope_hash, request_hash, symbol, side, order_type,
              quantity, submitted_price_krw, status, order_intent_json,
              result_canonical_json, acknowledged_by, acknowledged_at, submitted_at,
              created_at, updated_at
            )
            VALUES (
              :orderId, :actorUserId, :accountId, :accountScopeHash, :decisionId,
              :evaluationId, 'KIS_MOCK', :scopeHash, :ownerScopeHash, :requestHash,
              :symbol, :side, :orderType, :quantity, :submittedPriceKrw, 'SUBMITTED',
              CAST(:orderIntentJson AS jsonb), :resultJson, :actorUserId,
              :acknowledgedAt, :submittedAt, :createdAt, :createdAt
            )
            """.trimIndent(),
            parameters(request, decision),
        )
    }

    private fun insertOrderEvent(
        jdbc: NamedParameterJdbcTemplate,
        request: BrokerageOrderWriteRequest,
    ) {
        jdbc.update(
            """
            INSERT INTO order_events (
              order_event_id, order_id, event_type, event_status, payload_json, created_at
            )
            VALUES (
              :eventId, :orderId, 'MOCK_ORDER_SUBMITTED', 'SUBMITTED',
              CAST(:payloadJson AS jsonb), :createdAt
            )
            """.trimIndent(),
            mapOf(
                "eventId" to id("oev"),
                "orderId" to request.orderId,
                "payloadJson" to
                    objectMapper.writeValueAsString(
                        mapOf(
                            "orderId" to request.orderId,
                            "brokerageMode" to "KIS_MOCK",
                            "status" to "SUBMITTED",
                        ),
                    ),
                "createdAt" to request.createdAt.utc(),
            ),
        )
    }

    private fun insertAudit(
        jdbc: NamedParameterJdbcTemplate,
        request: BrokerageOrderWriteRequest,
        decision: OrderableDecision,
    ) {
        jdbc.update(
            """
            INSERT INTO audit_logs (
              audit_log_id, user_id, actor_role, action, target_type, target_id,
              request_id, payload_json, created_at
            )
            VALUES (
              :auditId, :actorUserId, :actorRole, 'MOCK_ORDER_SUBMITTED', 'ORDER',
              :orderId, :requestId, CAST(:payloadJson AS jsonb), :createdAt
            )
            """.trimIndent(),
            referencePayloadParameters(request, decision) +
                mapOf(
                    "auditId" to id("aud"),
                    "actorUserId" to request.actor.userId,
                    "actorRole" to request.actor.role,
                    "requestId" to request.actor.requestId,
                    "createdAt" to request.createdAt.utc(),
                ),
        )
    }

    private fun insertOutbox(
        jdbc: NamedParameterJdbcTemplate,
        request: BrokerageOrderWriteRequest,
        decision: OrderableDecision,
    ) {
        jdbc.update(
            """
            INSERT INTO event_outbox (
              event_id, event_type, aggregate_type, aggregate_id, partition_key,
              payload_json, schema_version, status, retry_count, created_at, updated_at
            )
            VALUES (
              :eventId, 'brokerage.mock-order-submitted.v1', 'ORDER', :orderId,
              :orderId, CAST(:payloadJson AS jsonb), '1.0.0', 'PENDING', 0,
              :createdAt, :createdAt
            )
            """.trimIndent(),
            referencePayloadParameters(request, decision) +
                mapOf(
                    "eventId" to id("evt"),
                    "createdAt" to request.createdAt.utc(),
                ),
        )
    }

    private fun referencePayloadParameters(
        request: BrokerageOrderWriteRequest,
        decision: OrderableDecision,
    ): Map<String, Any> {
        val payload =
            mapOf(
                "orderId" to request.orderId,
                "decisionId" to decision.decisionId,
                "evaluationId" to decision.evaluationId,
                "brokerageMode" to "KIS_MOCK",
                "status" to "SUBMITTED",
                "idempotencyScopeHash" to request.idempotency.scopeHash,
            )
        return mapOf(
            "orderId" to request.orderId,
            "payloadJson" to objectMapper.writeValueAsString(payload),
        )
    }

    private fun insertCancelEvent(
        jdbc: NamedParameterJdbcTemplate,
        order: OrderDetailProjection,
        actor: BrokerageActor,
        cancelledAt: Instant,
    ) {
        jdbc.update(
            """
            INSERT INTO order_events (
              order_event_id, order_id, event_type, event_status, payload_json, created_at
            )
            VALUES (
              :eventId, :orderId, 'MOCK_ORDER_CANCEL_REQUESTED', 'CANCEL_REQUESTED',
              CAST(:payloadJson AS jsonb), :createdAt
            )
            """.trimIndent(),
            cancelPayloadParameters(order, actor, cancelledAt) +
                mapOf(
                    "eventId" to id("oev"),
                    "createdAt" to cancelledAt.utc(),
                ),
        )
    }

    private fun insertCancelAudit(
        jdbc: NamedParameterJdbcTemplate,
        order: OrderDetailProjection,
        actor: BrokerageActor,
        cancelledAt: Instant,
    ) {
        jdbc.update(
            """
            INSERT INTO audit_logs (
              audit_log_id, user_id, actor_role, action, target_type, target_id,
              request_id, payload_json, created_at
            )
            VALUES (
              :auditId, :actorUserId, :actorRole, 'MOCK_ORDER_CANCEL_REQUESTED',
              'ORDER', :orderId, :requestId, CAST(:payloadJson AS jsonb), :createdAt
            )
            """.trimIndent(),
            cancelPayloadParameters(order, actor, cancelledAt) +
                mapOf(
                    "auditId" to id("aud"),
                    "actorUserId" to actor.userId,
                    "actorRole" to actor.role,
                    "requestId" to actor.requestId,
                    "createdAt" to cancelledAt.utc(),
                ),
        )
    }

    private fun insertCancelOutbox(
        jdbc: NamedParameterJdbcTemplate,
        order: OrderDetailProjection,
        actor: BrokerageActor,
        cancelledAt: Instant,
    ) {
        jdbc.update(
            """
            INSERT INTO event_outbox (
              event_id, event_type, aggregate_type, aggregate_id, partition_key,
              payload_json, schema_version, status, retry_count, created_at, updated_at
            )
            VALUES (
              :eventId, 'brokerage.mock-order-cancel-requested.v1', 'ORDER',
              :orderId, :orderId, CAST(:payloadJson AS jsonb), '1.0.0', 'PENDING',
              0, :createdAt, :createdAt
            )
            """.trimIndent(),
            cancelPayloadParameters(order, actor, cancelledAt) +
                mapOf(
                    "eventId" to id("evt"),
                    "createdAt" to cancelledAt.utc(),
                ),
        )
    }

    private fun cancelPayloadParameters(
        order: OrderDetailProjection,
        actor: BrokerageActor,
        cancelledAt: Instant,
    ): Map<String, Any> {
        val payload =
            mapOf(
                "orderId" to order.orderId,
                "decisionId" to order.decisionId,
                "brokerageMode" to "KIS_MOCK",
                "status" to "CANCEL_REQUESTED",
                "requestedByRole" to actor.role,
                "requestedAt" to cancelledAt.toString(),
            )
        return mapOf(
            "orderId" to order.orderId,
            "payloadJson" to objectMapper.writeValueAsString(payload),
        )
    }

    private fun parseBalancePositions(value: String): List<MockBalancePositionProjection> {
        val root = objectMapper.readTree(value)
        if (!root.isArray || root.size() > 1_000) {
            throw BrokerageUnavailableException("Stored balance positions violated the bounded contract.")
        }
        return root
            .values()
            .asSequence()
            .map { node ->
                if (!node.isObject) {
                    throw BrokerageUnavailableException("Stored balance position shape is invalid.")
                }
                val fieldNames =
                    node
                        .properties()
                        .asSequence()
                        .map { it.key }
                        .toSet()
                if (fieldNames != setOf("symbol", "quantity", "marketValueKrw", "isGoldEtfEtn")) {
                    throw BrokerageUnavailableException("Stored balance position shape is invalid.")
                }
                MockBalancePositionProjection(
                    symbol = node.path("symbol").stringValue(),
                    quantity = node.path("quantity").longValue(),
                    marketValueKrw = node.path("marketValueKrw").longValue(),
                    isGoldEtfEtn = node.path("isGoldEtfEtn").booleanValue(),
                )
            }.sortedBy(MockBalancePositionProjection::symbol)
            .toList()
    }

    private fun parameters(
        request: BrokerageOrderWriteRequest,
        decision: OrderableDecision,
    ): Map<String, Any?> =
        mapOf(
            "orderId" to request.orderId,
            "actorUserId" to request.actor.userId,
            "accountId" to accountId(decision.portfolioOwnerScopeHash),
            "accountScopeHash" to decision.portfolioOwnerScopeHash,
            "decisionId" to decision.decisionId,
            "evaluationId" to decision.evaluationId,
            "scopeHash" to request.idempotency.scopeHash,
            "ownerScopeHash" to request.idempotency.ownerScopeHash,
            "requestHash" to request.idempotency.requestHash,
            "symbol" to request.command.orderIntent.symbol,
            "side" to request.command.orderIntent.side,
            "orderType" to request.command.orderIntent.orderType,
            "quantity" to request.command.orderIntent.quantity,
            "submittedPriceKrw" to
                request.command.orderIntent.estimatedPrice
                    .takeIf { request.command.orderIntent.orderType == "LIMIT" },
            "orderIntentJson" to
                objectMapper.writeValueAsString(
                    mapOf(
                        "symbol" to request.command.orderIntent.symbol,
                        "side" to request.command.orderIntent.side,
                        "orderType" to request.command.orderIntent.orderType,
                        "quantity" to request.command.orderIntent.quantity,
                        "estimatedPrice" to request.command.orderIntent.estimatedPrice,
                        "estimatedAmount" to request.command.orderIntent.estimatedAmount,
                        "timeframe" to request.command.orderIntent.timeframe,
                        "strategyId" to request.command.orderIntent.strategyId,
                    ),
                ),
            "resultJson" to request.projectionCanonicalJson,
            "acknowledgedAt" to request.createdAt.utc(),
            "submittedAt" to request.projection.submittedAt.utc(),
            "createdAt" to request.createdAt.utc(),
        )

    private fun lock(
        jdbc: NamedParameterJdbcTemplate,
        lockKey: String,
        seed: Long,
    ) {
        jdbc.queryForObject(
            "SELECT pg_advisory_xact_lock(hashtextextended(:lockKey, :seed))",
            mapOf("lockKey" to lockKey, "seed" to seed),
            Any::class.java,
        )
    }

    private fun accountId(ownerScopeHash: String): String {
        if (!OWNER_SCOPE_HASH.matches(ownerScopeHash)) {
            throw BrokerageUnavailableException("Decision portfolio owner scope is malformed.")
        }
        return "acct_${ownerScopeHash.take(32)}"
    }

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: error("Brokerage JDBC access is unavailable without a configured DataSource.")

    private fun id(prefix: String): String = "${prefix}_${UUID.randomUUID().toString().replace("-", "")}"

    private fun Instant.utc(): OffsetDateTime = OffsetDateTime.ofInstant(this, ZoneOffset.UTC)

    private companion object {
        const val ADVISORY_LOCK_SEED = 3101L
        val CANCELABLE_STATUSES = setOf("SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED")
        val OWNER_SCOPE_HASH = Regex("^[0-9a-f]{64}$")
    }
}
