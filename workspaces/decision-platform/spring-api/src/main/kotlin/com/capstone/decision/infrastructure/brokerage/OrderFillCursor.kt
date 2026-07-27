package com.capstone.decision.infrastructure.brokerage

import com.capstone.decision.application.brokerage.BrokerageActor
import com.capstone.decision.application.brokerage.BrokerageFillMode
import com.capstone.decision.application.brokerage.InvalidOrderFillCursorException
import com.capstone.decision.application.brokerage.OrderFillCursorPort
import com.capstone.decision.application.brokerage.OrderFillCursorPosition
import com.capstone.decision.application.brokerage.OrderFillPageRequest
import com.capstone.decision.application.brokerage.OrderFillRecord
import org.springframework.stereotype.Component
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Clock
import java.time.Instant
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * fill cursor는 기존 brokerage HMAC key를 별도 purpose/version으로 사용해 owner와 조회 조건을 결속한다.
 * payload에는 raw userId나 accountId를 넣지 않고 고정 정렬의 마지막 키만 담는다.
 */
@Component
class OrderFillCursor(
    private val properties: BrokerageProperties,
    private val objectMapper: ObjectMapper,
    private val principleClock: Clock,
) : OrderFillCursorPort {
    override fun encode(
        request: OrderFillPageRequest,
        last: OrderFillRecord,
    ): String {
        require(last.brokerageMode == request.brokerageMode.name)
        require(ORDER_ID.matches(last.orderId))
        require(HASH.matches(last.execRefHash))
        val issuedAt = principleClock.instant().epochSecond
        val payload =
            CursorPayload(
                subjectBinding = binding(SUBJECT_PURPOSE, request.actor.userId),
                accountBinding =
                    binding(
                        ACCOUNT_PURPOSE,
                        "${request.brokerageMode.name}\u0000${request.accountId}",
                    ),
                brokerageMode = request.brokerageMode.name,
                fromInclusive = request.fromInclusive.toString(),
                toExclusive = request.toExclusive.toString(),
                issuedAt = issuedAt,
                expiresAt = Math.addExact(issuedAt, TTL_SECONDS),
                lastFilledAt = last.filledAt.toString(),
                lastOrderId = last.orderId,
                lastExecRefHash = last.execRefHash,
            )
        val payloadPart =
            ENCODER.encodeToString(
                canonical(payload).toByteArray(StandardCharsets.UTF_8),
            )
        val signature = ENCODER.encodeToString(mac(SIGNATURE_PURPOSE, payloadPart))
        return "$payloadPart.$signature".also {
            check(it.length <= MAX_CURSOR_CHARS) { "Generated fill cursor exceeded its bound." }
        }
    }

    override fun decode(
        cursor: String,
        actor: BrokerageActor,
        brokerageMode: BrokerageFillMode,
        accountId: String,
        fromInclusive: Instant,
        toExclusive: Instant,
    ): OrderFillCursorPosition {
        try {
            require(cursor.length in 1..MAX_CURSOR_CHARS)
            require('=' !in cursor)
            val parts = cursor.split('.')
            require(parts.size == 2 && parts.all(String::isNotEmpty))
            val actualSignature = DECODER.decode(parts[1])
            require(actualSignature.size == HMAC_BYTES)
            require(ENCODER.encodeToString(actualSignature) == parts[1])
            require(MessageDigest.isEqual(mac(SIGNATURE_PURPOSE, parts[0]), actualSignature))

            val payloadBytes = DECODER.decode(parts[0])
            require(payloadBytes.isNotEmpty())
            require(ENCODER.encodeToString(payloadBytes) == parts[0])
            val payloadText = String(payloadBytes, StandardCharsets.UTF_8)
            require(payloadText.toByteArray(StandardCharsets.UTF_8).contentEquals(payloadBytes))
            val root = objectMapper.readTree(payloadBytes)
            require(root != null && root.isObject)
            require(root.propertyNames().asSequence().toList() == FIELDS)
            val payload = parse(root)
            require(payloadText == canonical(payload))
            require(payload.brokerageMode == brokerageMode.name)
            require(payload.fromInclusive == fromInclusive.toString())
            require(payload.toExclusive == toExclusive.toString())
            require(
                MessageDigest.isEqual(
                    payload.subjectBinding.toByteArray(StandardCharsets.US_ASCII),
                    binding(SUBJECT_PURPOSE, actor.userId).toByteArray(StandardCharsets.US_ASCII),
                ),
            )
            require(
                MessageDigest.isEqual(
                    payload.accountBinding.toByteArray(StandardCharsets.US_ASCII),
                    binding(
                        ACCOUNT_PURPOSE,
                        "${brokerageMode.name}\u0000$accountId",
                    ).toByteArray(StandardCharsets.US_ASCII),
                ),
            )
            return OrderFillCursorPosition(
                filledAt = Instant.parse(payload.lastFilledAt),
                orderId = payload.lastOrderId,
                execRefHash = payload.lastExecRefHash,
            )
        } catch (_: RuntimeException) {
            throw InvalidOrderFillCursorException()
        }
    }

    private fun parse(root: JsonNode): CursorPayload {
        require(root.path("schemaVersion").isIntegralNumber)
        require(root.path("schemaVersion").intValue() == SCHEMA_VERSION)
        require(root.path("keyVersion").isString)
        require(root.path("keyVersion").stringValue() == KEY_VERSION)
        require(root.path("route").isString)
        require(root.path("route").stringValue() == ROUTE)
        val issuedAt = root.path("issuedAt").longValue()
        val expiresAt = root.path("expiresAt").longValue()
        val now = principleClock.instant().epochSecond
        require(issuedAt <= Math.addExact(now, FUTURE_SKEW_SECONDS))
        require(expiresAt > now)
        require(expiresAt == Math.addExact(issuedAt, TTL_SECONDS))
        val subjectBinding = root.path("subjectBinding").stringValue()
        val accountBinding = root.path("accountBinding").stringValue()
        validateBinding(subjectBinding)
        validateBinding(accountBinding)
        val brokerageMode = root.path("brokerageMode").stringValue()
        require(brokerageMode in BrokerageFillMode.entries.map(Enum<*>::name))
        val fromInclusive = Instant.parse(root.path("fromInclusive").stringValue()).toString()
        val toExclusive = Instant.parse(root.path("toExclusive").stringValue()).toString()
        require(Instant.parse(toExclusive).isAfter(Instant.parse(fromInclusive)))
        val lastFilledAt = Instant.parse(root.path("lastFilledAt").stringValue()).toString()
        val lastOrderId = root.path("lastOrderId").stringValue()
        val lastExecRefHash = root.path("lastExecRefHash").stringValue()
        require(ORDER_ID.matches(lastOrderId))
        require(HASH.matches(lastExecRefHash))
        return CursorPayload(
            subjectBinding = subjectBinding,
            accountBinding = accountBinding,
            brokerageMode = brokerageMode,
            fromInclusive = fromInclusive,
            toExclusive = toExclusive,
            issuedAt = issuedAt,
            expiresAt = expiresAt,
            lastFilledAt = lastFilledAt,
            lastOrderId = lastOrderId,
            lastExecRefHash = lastExecRefHash,
        )
    }

    private fun validateBinding(value: String) {
        require('=' !in value)
        val decoded = DECODER.decode(value)
        require(decoded.size == HMAC_BYTES)
        require(ENCODER.encodeToString(decoded) == value)
    }

    private fun canonical(payload: CursorPayload): String =
        """{"schemaVersion":$SCHEMA_VERSION,"keyVersion":"$KEY_VERSION","route":"$ROUTE","subjectBinding":"${payload.subjectBinding}","accountBinding":"${payload.accountBinding}","brokerageMode":"${payload.brokerageMode}","fromInclusive":"${payload.fromInclusive}","toExclusive":"${payload.toExclusive}","issuedAt":${payload.issuedAt},"expiresAt":${payload.expiresAt},"lastFilledAt":"${payload.lastFilledAt}","lastOrderId":"${payload.lastOrderId}","lastExecRefHash":"${payload.lastExecRefHash}"}"""

    private fun binding(
        purpose: String,
        value: String,
    ): String = ENCODER.encodeToString(mac(purpose, value))

    private fun mac(
        purpose: String,
        value: String,
    ): ByteArray {
        val keyBytes = properties.idempotencyScopeHmacKey.toByteArray(StandardCharsets.UTF_8)
        val valueBytes = value.toByteArray(StandardCharsets.UTF_8)
        return try {
            val mac = Mac.getInstance("HmacSHA256")
            mac.init(SecretKeySpec(keyBytes, "HmacSHA256"))
            mac.update(
                "capstone-brokerage-fill-cursor\u0000$SCHEMA_VERSION\u0000$KEY_VERSION\u0000$purpose\u0000"
                    .toByteArray(StandardCharsets.UTF_8),
            )
            mac.doFinal(valueBytes)
        } finally {
            keyBytes.fill(0)
            valueBytes.fill(0)
        }
    }

    private data class CursorPayload(
        val subjectBinding: String,
        val accountBinding: String,
        val brokerageMode: String,
        val fromInclusive: String,
        val toExclusive: String,
        val issuedAt: Long,
        val expiresAt: Long,
        val lastFilledAt: String,
        val lastOrderId: String,
        val lastExecRefHash: String,
    )

    private companion object {
        const val SCHEMA_VERSION = 1
        const val KEY_VERSION = "v1"
        const val ROUTE = "brokerage-fills"
        const val SUBJECT_PURPOSE = "BROKERAGE_FILL_CURSOR_SUBJECT/v1"
        const val ACCOUNT_PURPOSE = "BROKERAGE_FILL_CURSOR_ACCOUNT/v1"
        const val SIGNATURE_PURPOSE = "BROKERAGE_FILL_CURSOR_SIGNATURE/v1"
        const val TTL_SECONDS = 900L
        const val FUTURE_SKEW_SECONDS = 60L
        const val HMAC_BYTES = 32
        const val MAX_CURSOR_CHARS = 1024
        val ORDER_ID = Regex("^ord_(?:mock|paper)_[0-9a-f]{32}$")
        val HASH = Regex("^[0-9a-f]{64}$")
        val FIELDS =
            listOf(
                "schemaVersion",
                "keyVersion",
                "route",
                "subjectBinding",
                "accountBinding",
                "brokerageMode",
                "fromInclusive",
                "toExclusive",
                "issuedAt",
                "expiresAt",
                "lastFilledAt",
                "lastOrderId",
                "lastExecRefHash",
            )
        val ENCODER: Base64.Encoder = Base64.getUrlEncoder().withoutPadding()
        val DECODER: Base64.Decoder = Base64.getUrlDecoder()
    }
}
