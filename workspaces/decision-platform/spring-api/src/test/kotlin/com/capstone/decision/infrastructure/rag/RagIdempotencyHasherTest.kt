package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagAnswerMode
import com.capstone.decision.application.rag.RagAskCommand
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class RagIdempotencyHasherTest {
    private val properties =
        RagGuardHistoryProperties(
            idempotencyScopeHmacKey = "s".repeat(64),
            requestFingerprintHmacKey = "f".repeat(64),
            providerUsageHmacKey = "u".repeat(64),
            rateLimitHmacKey = "r".repeat(64),
            historyCursorHmacKey = "c".repeat(64),
        )
    private val hasher = RagIdempotencyHasher(properties)

    @Test
    fun `scope and request identities use separate purpose keys and never expose raw values`() {
        val rawKey = listOf("idem", "rag", "private", "0001").joinToString("-")
        val first = hasher.identity("usr_demo_user", rawKey, command())
        val same = hasher.identity("usr_demo_user", rawKey, command())
        val changed =
            hasher.identity(
                "usr_demo_user",
                rawKey,
                command().copy(answerMode = RagAnswerMode.DETAILED),
            )

        assertEquals(first, same)
        assertEquals(first.scopeHmac, changed.scopeHmac)
        assertNotEquals(first.requestFingerprint, changed.requestFingerprint)
        assertNotEquals(first.scopeHmac, first.requestFingerprint)
        assertTrue(first.scopeHmac.matches(Regex("^[0-9a-f]{64}$")))
        assertTrue(first.requestFingerprint.matches(Regex("^[0-9a-f]{64}$")))
        assertTrue(
            listOf(first.scopeHmac, first.requestFingerprint).none {
                it.contains(rawKey) || it.contains("usr_demo_user")
            },
        )
    }

    @Test
    fun `scope binds owner method route and raw key while canonical request ordering stays stable`() {
        val first = hasher.identity("usr_demo_user", "idem-rag-private-0002", command())
        val otherOwner = hasher.identity("usr_demo_admin", "idem-rag-private-0002", command())
        val otherKey = hasher.identity("usr_demo_user", "idem-rag-private-0003", command())
        val reordered =
            hasher.identity(
                "usr_demo_user",
                "idem-rag-private-0002",
                command().copy(
                    relatedSymbols = listOf("132030"),
                    topics = listOf("PRODUCT_RISK", "RISK"),
                ),
            )

        assertNotEquals(first.scopeHmac, otherOwner.scopeHmac)
        assertNotEquals(first.scopeHmac, otherKey.scopeHmac)
        assertEquals(first.requestFingerprint, reordered.requestFingerprint)
    }

    private fun command(): RagAskCommand =
        RagAskCommand(
            question = "금 ETF의 롤오버 위험은 무엇인가요?",
            answerMode = RagAnswerMode.CONCISE,
            relatedSymbols = listOf("132030"),
            topics = listOf("RISK", "PRODUCT_RISK"),
        )
}
