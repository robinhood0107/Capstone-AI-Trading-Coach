package com.capstone.decision.api.automation

import com.capstone.decision.api.brokerage.BrokerageRequestParser
import com.capstone.decision.api.decision.DecisionRequestParser
import com.capstone.decision.application.brokerage.BrokerageActor
import com.capstone.decision.application.brokerage.BrokerageService
import com.capstone.decision.application.decision.DecisionActor
import com.capstone.decision.application.decision.DecisionService
import com.capstone.decision.infrastructure.security.UserSecurityRepository
import io.swagger.v3.oas.annotations.Hidden
import jakarta.servlet.http.HttpServletRequest
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.annotation.Value
import org.springframework.http.HttpStatus
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestHeader
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import tools.jackson.core.JacksonException
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.JsonNode
import tools.jackson.databind.json.JsonMapper
import java.security.MessageDigest
import java.util.UUID

/**
 * 같은 container loopback의 automation process만 기존 Spring Decision/Risk/Brokerage 경계를 호출한다.
 * public OpenAPI와 browser/JWT route에는 포함되지 않고 owner identity는 auth DB에서 다시 확인한다.
 */
@Hidden
@RestController
@RequestMapping("/internal/automation-runtime", produces = [MediaType.APPLICATION_JSON_VALUE])
class AutomationRuntimeBridgeController(
    private val decisionService: DecisionService,
    private val decisionParser: DecisionRequestParser,
    private val brokerageService: BrokerageService,
    private val brokerageParser: BrokerageRequestParser,
    private val users: UserSecurityRepository,
    @Value("\${AUTOMATION_RUNTIME_SHARED_SECRET:}") private val configuredSecret: String,
) {
    private val parser = AutomationRuntimeBridgeParser()
    private val logger = LoggerFactory.getLogger(javaClass)

    @PostMapping("/command", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun command(
        @RequestHeader(name = AUTH_HEADER, required = false) suppliedSecret: String?,
        @RequestBody(required = false) body: String?,
        request: HttpServletRequest,
    ): ResponseEntity<Any> {
        if (!authorized(request.remoteAddr, suppliedSecret)) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(mapOf("status" to "NOT_FOUND"))
        }
        return try {
            val command = parser.parse(body.orEmpty())
            val actor = users.findByUserId(command.userId)
            if (actor == null || actor.status != "ACTIVE") {
                ResponseEntity.status(HttpStatus.NOT_FOUND).body(mapOf("status" to "NOT_FOUND"))
            } else {
                val requestId = "auto-rt-${UUID.randomUUID().toString().replace("-", "")}"
                val result =
                    when (command.operation) {
                        "EVALUATE" ->
                            decisionService.evaluate(
                                actor = DecisionActor(command.userId, actor.role.name, requestId),
                                rawIdempotencyKey = requireNotNull(command.idempotencyKey),
                                command = decisionParser.parseEvaluate(command.payload.toString()),
                            )
                        "BALANCE" ->
                            brokerageService.getOwnedBalance(
                                actor = BrokerageActor(command.userId, actor.role.name, actor.securityVersion, requestId),
                                accountId = brokerageParser.parseAccountId(command.text("accountId")),
                            )
                        "BUYABLE" ->
                            brokerageService.getOwnedBuyable(
                                actor = BrokerageActor(command.userId, actor.role.name, actor.securityVersion, requestId),
                                accountId = brokerageParser.parseAccountId(command.text("accountId")),
                                symbol = command.symbol(),
                                estimatedPrice = command.positiveLong("estimatedPrice"),
                            )
                        "SUBMIT" ->
                            brokerageService.submitMockOrder(
                                actor = BrokerageActor(command.userId, actor.role.name, actor.securityVersion, requestId),
                                rawIdempotencyKey = requireNotNull(command.idempotencyKey),
                                command = brokerageParser.parseSubmit(command.payload.toString()),
                            )
                        "ORDER" ->
                            brokerageService.getOwnedOrder(
                                command.userId,
                                brokerageParser.parseOrderId(command.text("orderId")),
                            )
                        "CANCEL" ->
                            brokerageService.cancelOwnedOrder(
                                actor = BrokerageActor(command.userId, actor.role.name, actor.securityVersion, requestId),
                                orderId = brokerageParser.parseOrderId(command.text("orderId")),
                            )
                        else -> error("unreachable operation")
                    }
                ResponseEntity.ok(mapOf("status" to "OK", "data" to result))
            }
        } catch (error: Exception) {
            // provider body, account value, exception text와 stack trace를 internal response에 반사하지 않는다.
            // 다만 근본 원인의 **클래스 이름**은 남긴다. 바깥 예외만으로는 어느 구간이 닫혔는지
            // 알 수 없어 fail-closed를 진단할 수 없었다. 메시지와 값은 계속 남기지 않는다.
            logger.warn(
                "automation runtime bridge failed closed: {} caused by {}",
                error.javaClass.simpleName,
                generateSequence(error.cause) { it.cause }.lastOrNull()?.javaClass?.simpleName ?: "-",
            )
            ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).body(mapOf("status" to "FAILED"))
        }
    }

    private fun authorized(
        remoteAddress: String?,
        suppliedSecret: String?,
    ): Boolean {
        if (remoteAddress !in setOf("127.0.0.1", "0:0:0:0:0:0:0:1", "::1")) return false
        if (!SECRET.matches(configuredSecret) || suppliedSecret == null) return false
        return MessageDigest.isEqual(configuredSecret.toByteArray(), suppliedSecret.toByteArray())
    }

    private companion object {
        const val AUTH_HEADER = "X-Automation-Runtime-Auth"
        val SECRET = Regex("^[A-Za-z0-9._~:-]{32,256}$")
    }
}

private data class AutomationRuntimeBridgeCommand(
    val operation: String,
    val userId: String,
    val idempotencyKey: String?,
    val payload: JsonNode,
) {
    fun text(name: String): String =
        payload.get(name)?.takeIf(JsonNode::isString)?.stringValue()
            ?: throw IllegalArgumentException("invalid runtime command")

    fun symbol(): String = text("symbol").takeIf { SYMBOL.matches(it) } ?: error("invalid runtime symbol")

    fun positiveLong(name: String): Long =
        payload
            .get(name)
            ?.takeIf { it.isIntegralNumber && it.canConvertToLong() }
            ?.longValue()
            ?.takeIf { it > 0 }
            ?: throw IllegalArgumentException("invalid runtime amount")

    private companion object {
        val SYMBOL = Regex("^[0-9]{6}$")
    }
}

private class AutomationRuntimeBridgeParser {
    private val mapper =
        JsonMapper
            .builder(
                JsonFactory
                    .builder()
                    .streamReadConstraints(
                        StreamReadConstraints
                            .builder()
                            .maxNestingDepth(8)
                            .maxDocumentLength(65_536)
                            .maxTokenCount(160)
                            .maxNumberLength(32)
                            .maxStringLength(16_384)
                            .maxNameLength(64)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).build()

    fun parse(body: String): AutomationRuntimeBridgeCommand {
        val root =
            try {
                mapper.readTree(body)
            } catch (_: JacksonException) {
                null
            }
        require(root != null && root.isObject && root.properties().map { it.key }.toSet() == ROOT_FIELDS)
        val operation =
            root
                .path("operation")
                .takeIf(JsonNode::isString)
                ?.stringValue()
                .orEmpty()
        require(operation in OPERATIONS)
        val userId =
            root
                .path("userId")
                .takeIf(JsonNode::isString)
                ?.stringValue()
                .orEmpty()
        require(USER_ID.matches(userId))
        val payload = root.path("payload")
        require(payload.isObject)
        val idempotencyKey = root.path("idempotencyKey").takeIf(JsonNode::isString)?.stringValue()
        if (operation in setOf("EVALUATE", "SUBMIT")) {
            require(idempotencyKey != null && IDEMPOTENCY.matches(idempotencyKey))
        } else {
            require(idempotencyKey == null)
        }
        return AutomationRuntimeBridgeCommand(operation, userId, idempotencyKey, payload)
    }

    private companion object {
        val ROOT_FIELDS = setOf("operation", "userId", "idempotencyKey", "payload")
        val OPERATIONS = setOf("EVALUATE", "BALANCE", "BUYABLE", "SUBMIT", "ORDER", "CANCEL")
        val USER_ID = Regex("^usr_[A-Za-z0-9_-]{8,96}$")
        val IDEMPOTENCY = Regex("^[A-Za-z0-9._:-]{16,128}$")
    }
}
