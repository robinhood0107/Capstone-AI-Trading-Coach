package com.capstone.decision.infrastructure.brokerage

import com.capstone.decision.application.brokerage.BrokerageActor
import com.capstone.decision.application.brokerage.BrokerageFillMode
import com.capstone.decision.application.brokerage.BrokerageOrderNotFoundException
import com.capstone.decision.application.brokerage.BrokerageUnavailableException
import com.capstone.decision.application.brokerage.ExpectedOrderFillState
import com.capstone.decision.application.brokerage.OrderFillApplyRequest
import com.capstone.decision.application.brokerage.OrderFillLogicDivergenceException
import com.capstone.decision.application.brokerage.OrderFillPageRequest
import com.capstone.decision.application.brokerage.OrderFillPersistencePort
import com.capstone.decision.application.brokerage.OrderFillReconciliationProjection
import com.capstone.decision.application.brokerage.OrderFillRecord
import com.capstone.decision.application.brokerage.ReconciliationProjection
import com.capstone.decision.application.brokerage.StoredFillObservation
import com.capstone.decision.application.brokerage.StoredOrderFillState
import com.capstone.decision.application.security.AuthenticatedActorRef
import com.capstone.decision.domain.brokerage.FillExecutionType
import com.capstone.decision.domain.brokerage.OrderFillState
import com.capstone.decision.domain.brokerage.OrderFillStatus
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityIssuer
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import org.springframework.beans.factory.ObjectProvider
import org.springframework.dao.DataAccessException
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.math.BigInteger
import java.sql.SQLException
import java.time.Instant
import java.time.OffsetDateTime
import java.util.UUID

/**
 * S3.3 JDBC adapter는 raw observation 테이블을 직접 읽지 않고 capability-protected definer 함수만 호출한다.
 * actor role/security_version은 모든 write/read 경계에서 DB 현재값과 다시 대조된다.
 */
@Repository
class JdbcOrderFillRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val objectMapper: ObjectMapper,
    private val actorCapabilityIssuer: ActorCapabilityIssuer,
) : OrderFillPersistencePort {
    override fun acquireReconciliationLock(
        actor: BrokerageActor,
        orderId: String,
    ) {
        val payloadJson = actorOrderPayload(actor, orderId)
        val capability = capability(actor.userId, "LOCK_ORDER_FILL", "ORDER", orderId, payloadJson, admin = true)
        val outcome =
            jdbc().queryForObject(
                """
                SELECT acquire_order_fill_reconciliation_lock_authorized_v2(
                  :capability,
                  :payloadJson
                )
                """.trimIndent(),
                mapOf(
                    "payloadJson" to payloadJson,
                    "capability" to capability,
                ),
                String::class.java,
            )
        when (outcome) {
            "LOCKED" -> Unit
            "ORDER_NOT_FOUND" -> throw BrokerageOrderNotFoundException()
            "ACTOR_UNAUTHORIZED", "VALIDATION_ERROR", null ->
                throw BrokerageUnavailableException("Order fill database lock rejected the request.")
            else -> throw BrokerageUnavailableException("Order fill database lock returned an unknown outcome.")
        }
    }

    override fun readReconciliationState(
        actor: BrokerageActor,
        orderId: String,
        reconciledAt: Instant,
    ): StoredOrderFillState {
        val payloadJson = readPayload(actor, orderId, reconciledAt)
        val capability =
            capability(actor.userId, "READ_ORDER_FILL_STATE", "ORDER", orderId, payloadJson, admin = true)
        val row =
            jdbc()
                .query(
                    """
                    SELECT operation_outcome, state_json
                    FROM read_order_reconciliation_state_authorized_v2(
                      :capability,
                      :payloadJson
                    )
                    """.trimIndent(),
                    mapOf(
                        "payloadJson" to payloadJson,
                        "capability" to capability,
                    ),
                ) { result, _ ->
                    ReadStateResult(
                        outcome = result.getString("operation_outcome"),
                        stateJson = result.getString("state_json"),
                    )
                }.singleOrNull()
                ?: throw BrokerageUnavailableException("Order fill state function returned no result.")
        return when (row.outcome) {
            "READY" -> parseState(requireNotNull(row.stateJson))
            "ORDER_NOT_FOUND" -> throw BrokerageOrderNotFoundException()
            "ACTOR_UNAUTHORIZED", "VALIDATION_ERROR" ->
                throw BrokerageUnavailableException("Order fill state security boundary rejected the request.")
            else -> throw BrokerageUnavailableException("Order fill state function returned an unknown outcome.")
        }
    }

    override fun applyStoredFills(request: OrderFillApplyRequest): OrderFillReconciliationProjection {
        val payloadJson = applyPayload(request)
        val capability =
            capability(request.actor.userId, "APPLY_ORDER_FILLS", "ORDER", request.orderId, payloadJson, admin = true)
        val result =
            try {
                jdbc()
                    .query(
                        """
                        SELECT operation_outcome, order_id, brokerage_mode, status,
                               filled_quantity, leaves_quantity, unfilled_terminated_quantity,
                               average_fill_price_krw, reconciliation_status, reconciled_at,
                               applied_event_count, has_more
                        FROM apply_stored_order_fills_authorized_v2(
                          :capability,
                          :payloadJson
                        )
                        """.trimIndent(),
                        mapOf(
                            "payloadJson" to payloadJson,
                            "capability" to capability,
                        ),
                    ) { row, _ ->
                        ApplyResult(
                            outcome = row.getString("operation_outcome"),
                            orderId = row.getString("order_id"),
                            brokerageMode = row.getString("brokerage_mode"),
                            status = row.getString("status"),
                            filledQuantity = row.getLongOrNull("filled_quantity"),
                            leavesQuantity = row.getLongOrNull("leaves_quantity"),
                            unfilledTerminatedQuantity = row.getLongOrNull("unfilled_terminated_quantity"),
                            averageFillPriceKrw = row.getLongOrNull("average_fill_price_krw"),
                            reconciliationStatus = row.getString("reconciliation_status"),
                            reconciledAt = row.getObject("reconciled_at", OffsetDateTime::class.java)?.toInstant(),
                            appliedEventCount = row.getIntOrNull("applied_event_count"),
                            hasMore = row.getBooleanOrNull("has_more"),
                        )
                    }.singleOrNull()
            } catch (exception: DataAccessException) {
                if (exception.sqlState() == "P0001") {
                    throw OrderFillLogicDivergenceException()
                }
                throw exception
            } ?: throw BrokerageUnavailableException("Order fill apply function returned no result.")
        return when (result.outcome) {
            "APPLIED" ->
                OrderFillReconciliationProjection(
                    orderId = requireNotNull(result.orderId),
                    brokerageMode = requireNotNull(result.brokerageMode),
                    status = requireNotNull(result.status),
                    filledQuantity = requireNotNull(result.filledQuantity),
                    leavesQuantity = requireNotNull(result.leavesQuantity),
                    unfilledTerminatedQuantity = requireNotNull(result.unfilledTerminatedQuantity),
                    averageFillPriceKrw = result.averageFillPriceKrw,
                    reconciliation =
                        ReconciliationProjection(
                            status = requireNotNull(result.reconciliationStatus),
                            checkedAt = result.reconciledAt,
                        ),
                    appliedEventCount = requireNotNull(result.appliedEventCount),
                    hasMore = requireNotNull(result.hasMore),
                )
            "ORDER_NOT_FOUND" -> throw BrokerageOrderNotFoundException()
            "ACTOR_UNAUTHORIZED", "VALIDATION_ERROR" ->
                throw BrokerageUnavailableException("Order fill apply security boundary rejected the request.")
            else -> throw BrokerageUnavailableException("Order fill apply function returned an unknown outcome.")
        }
    }

    override fun readOwnedFills(request: OrderFillPageRequest): List<OrderFillRecord> {
        val payloadJson = pagePayload(request)
        val capability =
            capability(request.actor.userId, "READ_ORDER_FILLS", "ACCOUNT", request.accountId, payloadJson)
        val row =
            jdbc()
                .query(
                    """
                    SELECT operation_outcome, page_json
                    FROM read_owned_order_fills_authorized_v2(
                      :capability,
                      :payloadJson
                    )
                    """.trimIndent(),
                    mapOf(
                        "payloadJson" to payloadJson,
                        "capability" to capability,
                    ),
                ) { result, _ ->
                    FillPageResult(
                        outcome = result.getString("operation_outcome"),
                        pageJson = result.getString("page_json"),
                    )
                }.singleOrNull()
                ?: throw BrokerageUnavailableException("Order fill page function returned no result.")
        return when (row.outcome) {
            "READY" -> parseFillPage(requireNotNull(row.pageJson))
            "ACCOUNT_NOT_FOUND" -> throw BrokerageOrderNotFoundException()
            "ACTOR_UNAUTHORIZED", "VALIDATION_ERROR" ->
                throw BrokerageUnavailableException("Order fill page security boundary rejected the request.")
            else -> throw BrokerageUnavailableException("Order fill page function returned an unknown outcome.")
        }
    }

    private fun actorOrderPayload(
        actor: BrokerageActor,
        orderId: String,
    ): String =
        objectMapper.writeValueAsString(
            mapOf(
                "actorUserId" to actor.userId,
                "actorRole" to actor.role,
                "securityVersion" to actor.securityVersion,
                "orderId" to orderId,
            ),
        )

    private fun applyPayload(request: OrderFillApplyRequest): String =
        objectMapper.writeValueAsString(
            mapOf(
                "actorUserId" to request.actor.userId,
                "actorRole" to request.actor.role,
                "securityVersion" to request.actor.securityVersion,
                "requestId" to request.actor.requestId,
                "orderId" to request.orderId,
                "reconciledAt" to request.reconciledAt.toString(),
                "auditLogId" to id("aud"),
                "outboxEventId" to id("evt"),
                "expectedFinal" to expectedFinal(request.expectedFinal),
            ),
        )

    private fun readPayload(
        actor: BrokerageActor,
        orderId: String,
        reconciledAt: Instant,
    ): String =
        objectMapper.writeValueAsString(
            mapOf(
                "actorUserId" to actor.userId,
                "actorRole" to actor.role,
                "securityVersion" to actor.securityVersion,
                "orderId" to orderId,
                "reconciledAt" to reconciledAt.toString(),
            ),
        )

    private fun expectedFinal(value: ExpectedOrderFillState): Map<String, Any?> =
        mapOf(
            "status" to value.status,
            "filledQuantity" to value.filledQuantity,
            "leavesQuantity" to value.leavesQuantity,
            "unfilledTerminatedQuantity" to value.unfilledTerminatedQuantity,
            "fillNotionalKrw" to value.fillNotionalKrw,
            "averageFillPriceKrw" to value.averageFillPriceKrw,
            "reconciliationStatus" to value.reconciliationStatus,
            "appliedEventCount" to value.appliedEventCount,
            "hasMore" to value.hasMore,
        )

    private fun pagePayload(request: OrderFillPageRequest): String =
        objectMapper.writeValueAsString(
            mapOf(
                "actorUserId" to request.actor.userId,
                "actorRole" to request.actor.role,
                "securityVersion" to request.actor.securityVersion,
                "accountId" to request.accountId,
                "brokerageMode" to request.brokerageMode.name,
                "fromInclusive" to request.fromInclusive.toString(),
                "toExclusive" to request.toExclusive.toString(),
                "lastFilledAt" to (request.cursor?.filledAt?.toString() ?: ""),
                "lastOrderId" to (request.cursor?.orderId ?: ""),
                "lastExecRefHash" to (request.cursor?.execRefHash ?: ""),
            ),
        )

    private fun parseState(value: String): StoredOrderFillState {
        val root = objectMapper.readTree(value)
        val observationsNode = root.path("observations")
        if (!root.isObject || !observationsNode.isArray || observationsNode.size() > 200) {
            throw BrokerageUnavailableException("Stored order fill state violated its bound.")
        }
        val state =
            OrderFillState(
                quantity = root.requiredLong("quantity"),
                filledQuantity = root.requiredLong("filledQuantity"),
                leavesQuantity = root.requiredLong("leavesQuantity"),
                unfilledTerminatedQuantity = root.requiredLong("unfilledTerminatedQuantity"),
                fillNotionalKrw = root.requiredBigInteger("fillNotionalKrw"),
                averageFillPriceKrw = root.longOrNull("averageFillPriceKrw"),
                status = OrderFillStatus.valueOf(root.requiredText("status")),
            )
        return StoredOrderFillState(
            orderId = root.requiredText("orderId"),
            brokerageMode = BrokerageFillMode.valueOf(root.requiredText("brokerageMode")),
            orderState = state,
            reconciliationStatus = root.requiredText("reconciliationStatus"),
            observationCount = root.requiredLong("observationCount"),
            observedFillQuantity = root.requiredLong("observedFillQuantity"),
            recomputedAverageFillPriceKrw = root.longOrNull("recomputedAverageFillPriceKrw"),
            providerFinalAverageFillPriceKrw = root.longOrNull("providerFinalAverageFillPriceKrw"),
            observations =
                observationsNode
                    .values()
                    .asSequence()
                    .map { observation ->
                        StoredFillObservation(
                            observationId = observation.requiredText("observationId"),
                            execRefHash = observation.requiredText("providerExecRefHash"),
                            execType = FillExecutionType.valueOf(observation.requiredText("execType")),
                            fillQuantity = observation.requiredLong("fillQuantity"),
                            fillPriceKrw = observation.longOrNull("fillPriceKrw"),
                            cumulativeQuantity = observation.requiredLong("cumulativeQuantity"),
                            leavesQuantity = observation.requiredLong("leavesQuantity"),
                            averageFillPriceKrw = observation.longOrNull("averageFillPriceKrw"),
                            observedAt = parseDatabaseInstant(observation.requiredText("observedAt")),
                        )
                    }.toList(),
            hasMore = root.path("hasMore").booleanValue(),
        )
    }

    private fun parseFillPage(value: String): List<OrderFillRecord> {
        val root = objectMapper.readTree(value)
        if (!root.isArray || root.size() > 51) {
            throw BrokerageUnavailableException("Stored order fill page violated its bound.")
        }
        return root
            .values()
            .asSequence()
            .map { item ->
                OrderFillRecord(
                    orderId = item.requiredText("orderId"),
                    brokerageMode = item.requiredText("brokerageMode"),
                    symbol = item.requiredText("symbol"),
                    side = item.requiredText("side"),
                    fillQuantity = item.requiredLong("fillQuantity"),
                    fillPriceKrw = item.requiredLong("fillPriceKrw"),
                    fillAmountKrw = item.requiredLong("fillAmountKrw"),
                    filledAt = parseDatabaseInstant(item.requiredText("filledAt")),
                    execRefHash = item.requiredText("execRefHash"),
                )
            }.toList()
    }

    private fun JsonNode.requiredText(field: String): String =
        path(field).takeIf(JsonNode::isString)?.stringValue()
            ?: throw BrokerageUnavailableException("Stored order fill text field is invalid.")

    private fun JsonNode.requiredLong(field: String): Long =
        path(field).takeIf(JsonNode::isIntegralNumber)?.longValue()
            ?: throw BrokerageUnavailableException("Stored order fill numeric field is invalid.")

    private fun JsonNode.requiredBigInteger(field: String): BigInteger =
        path(field).takeIf(JsonNode::isIntegralNumber)?.bigIntegerValue()
            ?: throw BrokerageUnavailableException("Stored order fill exact numeric field is invalid.")

    private fun JsonNode.longOrNull(field: String): Long? {
        val node = path(field)
        return when {
            node.isNull -> null
            node.isIntegralNumber -> node.longValue()
            else -> throw BrokerageUnavailableException("Stored order fill optional numeric field is invalid.")
        }
    }

    private fun parseDatabaseInstant(value: String): Instant =
        try {
            OffsetDateTime.parse(value.replaceFirst(' ', 'T')).toInstant()
        } catch (exception: Exception) {
            throw BrokerageUnavailableException("Stored order fill timestamp is invalid.", exception)
        }

    private fun java.sql.ResultSet.getLongOrNull(column: String): Long? = getObject(column)?.let { getLong(column) }

    private fun java.sql.ResultSet.getIntOrNull(column: String): Int? = getObject(column)?.let { getInt(column) }

    private fun java.sql.ResultSet.getBooleanOrNull(column: String): Boolean? = getObject(column)?.let { getBoolean(column) }

    private fun DataAccessException.sqlState(): String? {
        var current: Throwable? = this
        while (current != null) {
            if (current is SQLException) {
                return current.sqlState
            }
            current = current.cause
        }
        return null
    }

    private fun capability(
        actorUserId: String,
        operation: String,
        targetKind: String,
        targetId: String,
        payloadJson: String,
        admin: Boolean = false,
    ): String =
        actorCapabilityIssuer.issue(
            AuthenticatedActorRef.current(actorUserId),
            ActorCapabilityBinding.request(
                operation,
                targetKind,
                targetId,
                if (admin) ActorCapabilityRolePolicy.ADMIN_ONLY else ActorCapabilityRolePolicy.OWNER,
                payloadJson,
            ),
        )

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: error("Order fill JDBC access is unavailable without a configured DataSource.")

    private fun id(prefix: String): String = "${prefix}_${UUID.randomUUID().toString().replace("-", "")}"

    private data class ReadStateResult(
        val outcome: String,
        val stateJson: String?,
    )

    private data class ApplyResult(
        val outcome: String,
        val orderId: String?,
        val brokerageMode: String?,
        val status: String?,
        val filledQuantity: Long?,
        val leavesQuantity: Long?,
        val unfilledTerminatedQuantity: Long?,
        val averageFillPriceKrw: Long?,
        val reconciliationStatus: String?,
        val reconciledAt: Instant?,
        val appliedEventCount: Int?,
        val hasMore: Boolean?,
    )

    private data class FillPageResult(
        val outcome: String,
        val pageJson: String?,
    )
}
