package com.capstone.decision.infrastructure.brokerage

import com.capstone.decision.application.brokerage.BrokerageActor
import com.capstone.decision.application.brokerage.BrokerageFillMode
import com.capstone.decision.application.brokerage.InvalidOrderFillCursorException
import com.capstone.decision.application.brokerage.OrderFillPageRequest
import com.capstone.decision.application.brokerage.OrderFillRecord
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import tools.jackson.databind.json.JsonMapper
import java.nio.charset.StandardCharsets
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import java.util.Base64

class OrderFillCursorTest {
    private val now = Instant.parse("2030-01-02T03:04:05Z")
    private val properties =
        BrokerageProperties(
            idempotencyScopeHmacKey = "b".repeat(64),
        )
    private val mapper = JsonMapper.builder().build()

    @Test
    fun `cursor binds owner account mode range and stable last key without raw identifiers`() {
        val request = request()
        val cursor = codec(now).encode(request, last())
        val payload =
            String(
                Base64.getUrlDecoder().decode(cursor.substringBefore('.')),
                StandardCharsets.UTF_8,
            )

        assertFalse(payload.contains(request.actor.userId))
        assertFalse(payload.contains(request.accountId))
        assertEquals(
            last().let { Triple(it.filledAt, it.orderId, it.execRefHash) },
            codec(now)
                .decode(
                    cursor = cursor,
                    actor = request.actor,
                    brokerageMode = request.brokerageMode,
                    accountId = request.accountId,
                    fromInclusive = request.fromInclusive,
                    toExclusive = request.toExclusive,
                ).let { Triple(it.filledAt, it.orderId, it.execRefHash) },
        )
    }

    @Test
    fun `cursor rejects tampering cross owner cross account changed range and expiry`() {
        val request = request()
        val cursor = codec(now).encode(request, last())
        val decode: (BrokerageActor, String, Instant, String) -> Unit = { actor, accountId, from, token ->
            codec(now).decode(
                cursor = token,
                actor = actor,
                brokerageMode = request.brokerageMode,
                accountId = accountId,
                fromInclusive = from,
                toExclusive = request.toExclusive,
            )
        }

        assertThrows<InvalidOrderFillCursorException> {
            decode(request.actor, request.accountId, request.fromInclusive, cursor.dropLast(1) + "A")
        }
        assertThrows<InvalidOrderFillCursorException> {
            decode(request.actor.copy(userId = "usr_other"), request.accountId, request.fromInclusive, cursor)
        }
        assertThrows<InvalidOrderFillCursorException> {
            decode(request.actor, "acct_${"b".repeat(32)}", request.fromInclusive, cursor)
        }
        assertThrows<InvalidOrderFillCursorException> {
            decode(request.actor, request.accountId, request.fromInclusive.plusSeconds(1), cursor)
        }
        assertThrows<InvalidOrderFillCursorException> {
            codec(now.plusSeconds(901)).decode(
                cursor,
                request.actor,
                request.brokerageMode,
                request.accountId,
                request.fromInclusive,
                request.toExclusive,
            )
        }
    }

    private fun request(): OrderFillPageRequest =
        OrderFillPageRequest(
            actor =
                BrokerageActor(
                    userId = "usr_sensitive_subject",
                    role = "USER",
                    securityVersion = 1,
                    requestId = "req-fill-cursor",
                ),
            brokerageMode = BrokerageFillMode.KIS_MOCK,
            accountId = "acct_${"a".repeat(32)}",
            fromInclusive = Instant.parse("2030-01-01T15:00:00Z"),
            toExclusive = Instant.parse("2030-01-02T15:00:00Z"),
            cursor = null,
        )

    private fun last(): OrderFillRecord =
        OrderFillRecord(
            orderId = "ord_mock_${"c".repeat(32)}",
            brokerageMode = "KIS_MOCK",
            symbol = "005930",
            side = "BUY",
            fillQuantity = 1,
            fillPriceKrw = 70_000,
            fillAmountKrw = 70_000,
            filledAt = Instant.parse("2030-01-02T03:04:00Z"),
            execRefHash = "d".repeat(64),
        )

    private fun codec(clockAt: Instant): OrderFillCursor =
        OrderFillCursor(
            properties = properties,
            objectMapper = mapper,
            principleClock = Clock.fixed(clockAt, ZoneOffset.UTC),
        )
}
