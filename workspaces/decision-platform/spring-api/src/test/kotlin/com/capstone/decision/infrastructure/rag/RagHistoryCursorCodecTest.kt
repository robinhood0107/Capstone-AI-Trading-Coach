package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagHistoryCursorPoint
import com.capstone.decision.application.rag.RagValidationException
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import java.time.Instant

class RagHistoryCursorCodecTest {
    private val properties =
        RagGuardHistoryProperties(
            historySecretDirectory = "/tmp/rag-history-cursor-test",
            idempotencyScopeHmacKey = "i".repeat(64),
            requestFingerprintHmacKey = "f".repeat(64),
            providerUsageHmacKey = "u".repeat(64),
            rateLimitHmacKey = "r".repeat(64),
            historyCursorHmacKey = "c".repeat(64),
        )
    private val codec = RagHistoryCursorCodec(properties)
    private val point =
        RagHistoryCursorPoint(
            createdAt = Instant.parse("2026-07-31T00:00:00Z"),
            answerId = "rag_ans_${"a".repeat(32)}",
        )

    @Test
    fun `cursor binds owner timestamp and answer identity without plaintext owner`() {
        val cursor = codec.encode("usr_demo_user", point)

        assertEquals(point, codec.decode("usr_demo_user", cursor))
        assertFalseContains(cursor, "usr_demo_user")
        assertFalseContains(cursor, point.answerId)
        assertThrows(RagValidationException::class.java) {
            codec.decode("usr_demo_admin", cursor)
        }
    }

    @Test
    fun `cursor rejects tamper malformed and another purpose key`() {
        val cursor = codec.encode("usr_demo_user", point)
        val tampered = cursor.dropLast(1) + if (cursor.last() == 'A') "B" else "A"
        val other =
            RagHistoryCursorCodec(
                properties.copy(historyCursorHmacKey = "x".repeat(64)),
            )

        assertNotEquals(cursor, other.encode("usr_demo_user", point))
        listOf(tampered, "not+a+cursor", "", "x".repeat(513)).forEach { candidate ->
            assertThrows(RagValidationException::class.java) {
                codec.decode("usr_demo_user", candidate)
            }
        }
    }

    private fun assertFalseContains(
        value: String,
        forbidden: String,
    ) {
        assertEquals(false, value.contains(forbidden))
    }
}
