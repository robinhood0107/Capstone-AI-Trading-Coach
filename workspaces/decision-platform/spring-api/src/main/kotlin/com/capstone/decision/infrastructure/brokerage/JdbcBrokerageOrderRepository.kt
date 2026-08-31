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
import com.capstone.decision.application.brokerage.BrokerageProviderOutcomeRequest
import com.capstone.decision.application.brokerage.BrokerageUnavailableException
import com.capstone.decision.application.brokerage.BrokerageValidationException
import com.capstone.decision.application.brokerage.DecisionExpiredException
import com.capstone.decision.application.brokerage.MockBalancePositionProjection
import com.capstone.decision.application.brokerage.OrderDetailProjection
import com.capstone.decision.application.brokerage.OrderableDecision
import com.capstone.decision.application.brokerage.StoredBrokerageIdempotencyResult
import com.capstone.decision.application.brokerage.StoredMockBalance
import com.capstone.decision.application.risk.KillSwitchBlockedException
import com.capstone.decision.application.security.AuthenticatedActorRef
import com.capstone.decision.domain.brokerage.TickSizePolicy
import com.capstone.decision.domain.brokerage.TickTableContext
import com.capstone.decision.domain.brokerage.TickTableVerification
import com.capstone.decision.domain.brokerage.TickValidation
import com.capstone.decision.domain.risk.OrderIntentSnapshot
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityIssuer
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import org.springframework.beans.factory.ObjectProvider
import org.springframework.beans.factory.annotation.Value
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Transactional
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.time.Instant
import java.time.OffsetDateTime
import java.util.UUID

/**
 * S3.1 주문 writer는 capability-protected DB 함수만 호출해 Decision/Kill Switch/evidence를 원자 판정한다.
 * raw DB capability, provider payload, 계좌번호, raw idempotency key는 영속·로그 경계에 남기지 않는다.
 */
@Repository
class JdbcBrokerageOrderRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val objectMapper: ObjectMapper,
    private val actorCapabilityIssuer: ActorCapabilityIssuer,
    @Value("\${BROKERAGE_TICK_TABLE_VERIFICATION:}") private val declaredTickTable: String,
) : BrokerageOrderPersistencePort {
    override fun findIdempotencyResult(
        actorUserId: String,
        scopeHash: String,
        ownerScopeHash: String,
        now: Instant,
    ): StoredBrokerageIdempotencyResult? {
        val nowText = now.toString()
        val capability =
            actorCapabilityIssuer.issue(
                AuthenticatedActorRef.current(actorUserId),
                ActorCapabilityBinding.request(
                    "READ_MOCK_IDEMPOTENCY",
                    "BROKERAGE_IDEMPOTENCY",
                    scopeHash,
                    ActorCapabilityRolePolicy.OWNER,
                    scopeHash,
                    ownerScopeHash,
                    nowText,
                ),
            )
        return jdbc()
            .query(
                """
                SELECT request_hash, result_canonical_json, expires_at
                FROM find_mock_order_idempotency_result_authorized_v2(
                  :capability,
                  :actorUserId,
                  :scopeHash,
                  :ownerScopeHash,
                  :nowText
                )
                """.trimIndent(),
                mapOf(
                    "capability" to capability,
                    "actorUserId" to actorUserId,
                    "scopeHash" to scopeHash,
                    "ownerScopeHash" to ownerScopeHash,
                    "nowText" to nowText,
                ),
            ) { result, _ ->
                StoredBrokerageIdempotencyResult(
                    requestHash = result.getString("request_hash"),
                    projectionCanonicalJson = result.getString("result_canonical_json"),
                    expiresAt = result.getObject("expires_at", OffsetDateTime::class.java).toInstant(),
                )
            }.singleOrNull()
    }

    @Transactional
    override fun persist(request: BrokerageOrderWriteRequest) {
        val jdbc = jdbc()
        val decision =
            readOrderableDecision(
                jdbc = jdbc,
                actorUserId = request.actor.userId,
                decisionId = request.command.decisionId,
            ) ?: throw BrokerageDecisionNotFoundException()
        validateDecision(request, decision)
        val payloadJson = createOrderPayload(request, decision)
        val capability = capability(request.actor.userId, "CREATE_MOCK_ORDER", "ORDER", request.orderId, payloadJson)
        val result =
            jdbc
                .query(
                    """
                    SELECT operation_outcome, projection_canonical_json
                    FROM create_mock_order_authorized_v2(
                      :capability,
                      :payloadJson
                    )
                    """.trimIndent(),
                    mapOf(
                        "payloadJson" to payloadJson,
                        "capability" to capability,
                    ),
                ) { row, _ ->
                    CreateOrderFunctionResult(
                        outcome = row.getString("operation_outcome"),
                        projectionCanonicalJson = row.getString("projection_canonical_json"),
                    )
                }.singleOrNull()
                ?: throw BrokerageUnavailableException("Brokerage order function returned no result.")
        when (result.outcome) {
            "CREATED" -> Unit
            "REPLAY" ->
                throw BrokeragePersistenceReplayException(
                    requireNotNull(result.projectionCanonicalJson) {
                        "Brokerage replay result is missing its projection."
                    },
                )
            "IDEMPOTENCY_CONFLICT" -> throw BrokerageIdempotencyConflictException()
            "DECISION_NOT_FOUND" -> throw BrokerageDecisionNotFoundException()
            "DECISION_EXPIRED" -> throw DecisionExpiredException()
            "DECISION_CONFLICT" -> throw BrokerageDecisionConflictException()
            "RISK_BLOCKED" -> throw KillSwitchBlockedException()
            "VALIDATION_ERROR" ->
                throw BrokerageValidationException(
                    listOf(BrokerageFieldViolation("/orderIntent", "DATABASE_CONTRACT_REJECTED")),
                )
            "ACTOR_UNAUTHORIZED", "BROKERAGE_UNAVAILABLE" ->
                throw BrokerageUnavailableException("Brokerage database security boundary rejected the request.")
            else -> throw BrokerageUnavailableException("Brokerage order function returned an unknown outcome.")
        }
    }

    @Transactional
    override fun recordProviderOutcome(request: BrokerageProviderOutcomeRequest): OrderDetailProjection {
        val payloadJson = providerOutcomePayload(request)
        val capability =
            capability(request.actor.userId, "RECORD_MOCK_PROVIDER_OUTCOME", "ORDER", request.orderId, payloadJson)
        val result =
            jdbc()
                .query(
                    """
                    SELECT operation_outcome, order_id, account_id, brokerage_mode,
                           status, submitted_at, decision_id
                    FROM record_mock_order_provider_outcome_authorized_v2(
                      :capability,
                      :payloadJson
                    )
                    """.trimIndent(),
                    mapOf(
                        "payloadJson" to payloadJson,
                        "capability" to capability,
                    ),
                ) { row, _ ->
                    ProviderOutcomeFunctionResult(
                        outcome = row.getString("operation_outcome"),
                        orderId = row.getString("order_id"),
                        accountId = row.getString("account_id"),
                        brokerageMode = row.getString("brokerage_mode"),
                        status = row.getString("status"),
                        submittedAt = row.getObject("submitted_at", OffsetDateTime::class.java)?.toInstant(),
                        decisionId = row.getString("decision_id"),
                    )
                }.singleOrNull()
                ?: throw BrokerageUnavailableException("Brokerage provider outcome returned no result.")
        if (result.outcome != "APPLIED") {
            throw BrokerageUnavailableException("Brokerage provider outcome was rejected.")
        }
        return OrderDetailProjection(
            orderId = requireNotNull(result.orderId),
            accountId = requireNotNull(result.accountId),
            brokerageMode = requireNotNull(result.brokerageMode),
            status = requireNotNull(result.status),
            submittedAt = requireNotNull(result.submittedAt),
            decisionId = requireNotNull(result.decisionId),
        )
    }

    override fun findOrderableDecisionAccountId(
        actorUserId: String,
        decisionId: String,
    ): String? =
        readOrderableDecision(
            jdbc = jdbc(),
            actorUserId = actorUserId,
            decisionId = decisionId,
        )?.let { decision -> accountId(decision.portfolioOwnerScopeHash) }

    override fun findOwnedProjection(
        actorUserId: String,
        orderId: String,
    ): OrderDetailProjection? =
        readOwnedProjection(
            jdbc = jdbc(),
            actorUserId = actorUserId,
            orderId = orderId,
        )

    @Transactional
    override fun cancelOwnedOrder(
        actor: BrokerageActor,
        orderId: String,
        cancelledAt: Instant,
    ): OrderDetailProjection {
        val payloadJson = cancelOrderPayload(actor, orderId, cancelledAt)
        val capability = capability(actor.userId, "CANCEL_MOCK_ORDER", "ORDER", orderId, payloadJson)
        val result =
            jdbc()
                .query(
                    """
                    SELECT operation_outcome, order_id, account_id, brokerage_mode,
                           status, submitted_at, decision_id
                    FROM request_mock_order_cancel_authorized_v2(
                      :capability,
                      :payloadJson
                    )
                    """.trimIndent(),
                    mapOf(
                        "payloadJson" to payloadJson,
                        "capability" to capability,
                    ),
                ) { row, _ ->
                    CancelOrderFunctionResult(
                        outcome = row.getString("operation_outcome"),
                        orderId = row.getString("order_id"),
                        accountId = row.getString("account_id"),
                        brokerageMode = row.getString("brokerage_mode"),
                        status = row.getString("status"),
                        submittedAt = row.getObject("submitted_at", OffsetDateTime::class.java)?.toInstant(),
                        decisionId = row.getString("decision_id"),
                    )
                }.singleOrNull()
                ?: throw BrokerageUnavailableException("Brokerage cancel function returned no result.")
        return when (result.outcome) {
            "CANCEL_REQUESTED", "CANCELLED" ->
                OrderDetailProjection(
                    orderId = requireNotNull(result.orderId),
                    accountId = requireNotNull(result.accountId),
                    brokerageMode = requireNotNull(result.brokerageMode),
                    status = requireNotNull(result.status),
                    submittedAt = requireNotNull(result.submittedAt),
                    decisionId = requireNotNull(result.decisionId),
                )
            "ORDER_NOT_FOUND" -> throw BrokerageOrderNotFoundException()
            "ORDER_CONFLICT" -> throw BrokerageDecisionConflictException()
            "ACTOR_UNAUTHORIZED", "VALIDATION_ERROR" ->
                throw BrokerageUnavailableException("Brokerage database security boundary rejected the cancel request.")
            else -> throw BrokerageUnavailableException("Brokerage cancel function returned an unknown outcome.")
        }
    }

    override fun findOwnedBalance(
        actorUserId: String,
        accountId: String,
    ): StoredMockBalance? {
        val prefix = accountId.removePrefix("acct_")
        val capability = targetCapability(actorUserId, "READ_MOCK_BALANCE", "ACCOUNT", accountId)
        val rows =
            jdbc().query(
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
                FROM read_mock_balance_projection_authorized_v2(
                  :capability, :actorUserId, :accountId, :accountPrefix
                )
                """.trimIndent(),
                mapOf(
                    "capability" to capability,
                    "actorUserId" to actorUserId,
                    "accountId" to accountId,
                    "accountPrefix" to prefix,
                ),
            ) { result, _ ->
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
            throw BrokerageValidationException(
                listOf(BrokerageFieldViolation("/decisionId", "UNSUPPORTED_PORTFOLIO_SOURCE")),
            )
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
            throw BrokerageValidationException(
                listOf(BrokerageFieldViolation("/orderIntent", "DECISION_MISMATCH")),
            )
        }
        when (
            val tick =
                TickSizePolicy.validate(
                    orderType = request.command.orderIntent.orderType,
                    priceKrw = request.command.orderIntent.estimatedPrice,
                    context = tickTableContext(request.command.orderIntent.symbol),
                )
        ) {
            TickValidation.Valid -> Unit
            TickValidation.Unavailable ->
                throw BrokerageUnavailableException("LIMIT tick table is not verified for S3.1.")
            is TickValidation.Invalid ->
                throw BrokerageValidationException(
                    listOf(BrokerageFieldViolation("/orderIntent/estimatedPrice", tick.reason)),
                )
        }
    }

    /**
     * 호가단위 검증 근거를 배포가 붙인 선언과 종목 계약목록 관측에서 만든다.
     *
     * 지금까지 이 자리에 `null`이 들어가 있어 `TickSizePolicy`가 언제나 `Unavailable`을 냈고,
     * 그래서 LIMIT 주문이 조건 없이 거부됐다. 자동운용은 LIMIT만 내므로 플랫폼을 통한 주문이
     * 구조적으로 불가능했다. `TickTableContext`는 테스트에서만 만들어지고 있었다.
     *
     * 그렇다고 검증을 추론으로 만들지는 않는다. 계약목록 관측은 ETF/ETN 구분을 말할 뿐 KRX
     * 호가표를 대조했다는 사실을 말하지 않는다. 그래서 대조 사실은 배포가 명시적으로 붙인다 -
     * `BROKERAGE_TICK_TABLE_VERIFICATION`이 정확히 그 표의 이름일 때만 인정한다. 값이 없으면
     * 예전 그대로 닫힌다. 붙인 뒤에도 해당 종목의 계약목록 관측이 `COMPLETE`가 아니면 ETF/ETN
     * 구분을 알 수 없으므로 역시 닫는다. `TickSizePolicy.tickSize`가 구현한 표가 그 개정 표다.
     */
    private fun tickTableContext(symbol: String): TickTableContext? {
        if (declaredTickTable != TickTableVerification.KRX_CASH_EQUITY_202312_ETP_UPDATE.name) {
            return null
        }
        return jdbc()
            .query(
                """
                SELECT is_etf_etn
                FROM latest_instrument_catalog_observations
                WHERE symbol = :symbol
                  AND completeness = 'COMPLETE'
                LIMIT 1
                """.trimIndent(),
                mapOf("symbol" to symbol),
            ) { result, _ -> result.getBoolean("is_etf_etn") }
            .singleOrNull()
            ?.let { isEtfEtn ->
                TickTableContext(
                    isEtfEtn = isEtfEtn,
                    verification = TickTableVerification.KRX_CASH_EQUITY_202312_ETP_UPDATE,
                )
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

    private fun readOrderableDecision(
        jdbc: NamedParameterJdbcTemplate,
        actorUserId: String,
        decisionId: String,
    ): OrderableDecision? =
        jdbc
            .query(
                """
                SELECT decision_id, evaluation_id, portfolio_source, outcome, mode,
                       can_submit_order, enforcement_action, valid_until,
                       snapshot_artifact_canonical_json, portfolio_owner_scope_hash,
                       invalidated, invalidation_reason_class, consumed_by_order_id
                FROM read_mock_order_decision_authorized_v2(
                  :capability,
                  :actorUserId,
                  :decisionId
                )
                """.trimIndent(),
                mapOf(
                    "capability" to
                        targetCapability(actorUserId, "READ_MOCK_ORDER_DECISION", "DECISION", decisionId),
                    "actorUserId" to actorUserId,
                    "decisionId" to decisionId,
                ),
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

    private fun readOwnedProjection(
        jdbc: NamedParameterJdbcTemplate,
        actorUserId: String,
        orderId: String,
    ): OrderDetailProjection? =
        jdbc
            .query(
                """
                SELECT order_id, account_id, brokerage_mode, status, submitted_at, decision_id
                FROM read_mock_order_owner_projection_authorized_v2(
                  :capability,
                  :actorUserId,
                  :orderId
                )
                """.trimIndent(),
                mapOf(
                    "capability" to targetCapability(actorUserId, "READ_MOCK_ORDER", "ORDER", orderId),
                    "actorUserId" to actorUserId,
                    "orderId" to orderId,
                ),
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

    private fun createOrderPayload(
        request: BrokerageOrderWriteRequest,
        decision: OrderableDecision,
    ): String {
        val intent = request.command.orderIntent
        return objectMapper.writeValueAsString(
            mapOf(
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
                "accountId" to accountId(decision.portfolioOwnerScopeHash),
                "accountScopeHash" to decision.portfolioOwnerScopeHash,
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
                "submittedAt" to request.projection.submittedAt.toString(),
                "createdAt" to request.createdAt.toString(),
                "orderEventId" to id("oev"),
                "auditLogId" to id("aud"),
                "outboxEventId" to id("evt"),
            ),
        )
    }

    private fun cancelOrderPayload(
        actor: BrokerageActor,
        orderId: String,
        cancelledAt: Instant,
    ): String =
        objectMapper.writeValueAsString(
            mapOf(
                "actorUserId" to actor.userId,
                "actorRole" to actor.role,
                "securityVersion" to actor.securityVersion,
                "requestId" to actor.requestId,
                "orderId" to orderId,
                "cancelledAt" to cancelledAt.toString(),
                "orderEventId" to id("oev"),
                "auditLogId" to id("aud"),
                "outboxEventId" to id("evt"),
            ),
        )

    private fun providerOutcomePayload(request: BrokerageProviderOutcomeRequest): String =
        objectMapper.writeValueAsString(
            mapOf(
                "actorUserId" to request.actor.userId,
                "actorRole" to request.actor.role,
                "securityVersion" to request.actor.securityVersion,
                "requestId" to request.actor.requestId,
                "orderId" to request.orderId,
                "status" to request.status,
                "providerOrderRefHash" to request.providerOrderRefHash,
                "trId" to request.trId,
                "receivedAt" to request.receivedAt.toString(),
                "orderEventId" to id("oev"),
            ),
        )

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

    private fun accountId(ownerScopeHash: String): String {
        if (!OWNER_SCOPE_HASH.matches(ownerScopeHash)) {
            throw BrokerageUnavailableException("Decision portfolio owner scope is malformed.")
        }
        return "acct_${ownerScopeHash.take(32)}"
    }

    private fun targetCapability(
        actorUserId: String,
        operation: String,
        targetKind: String,
        targetId: String,
    ): String =
        actorCapabilityIssuer.issue(
            AuthenticatedActorRef.current(actorUserId),
            ActorCapabilityBinding.target(
                operation,
                targetKind,
                targetId,
                ActorCapabilityRolePolicy.OWNER,
            ),
        )

    private fun capability(
        actorUserId: String,
        operation: String,
        targetKind: String,
        targetId: String,
        payloadJson: String,
    ): String =
        actorCapabilityIssuer.issue(
            AuthenticatedActorRef.current(actorUserId),
            ActorCapabilityBinding.request(
                operation,
                targetKind,
                targetId,
                ActorCapabilityRolePolicy.OWNER,
                payloadJson,
            ),
        )

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: error("Brokerage JDBC access is unavailable without a configured DataSource.")

    private fun id(prefix: String): String = "${prefix}_${UUID.randomUUID().toString().replace("-", "")}"

    private data class CreateOrderFunctionResult(
        val outcome: String,
        val projectionCanonicalJson: String?,
    )

    private data class CancelOrderFunctionResult(
        val outcome: String,
        val orderId: String?,
        val accountId: String?,
        val brokerageMode: String?,
        val status: String?,
        val submittedAt: Instant?,
        val decisionId: String?,
    )

    private data class ProviderOutcomeFunctionResult(
        val outcome: String,
        val orderId: String?,
        val accountId: String?,
        val brokerageMode: String?,
        val status: String?,
        val submittedAt: Instant?,
        val decisionId: String?,
    )

    private companion object {
        val OWNER_SCOPE_HASH = Regex("^[0-9a-f]{64}$")
    }
}
