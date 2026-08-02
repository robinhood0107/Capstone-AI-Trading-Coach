package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagHistoryCorruptedException
import com.capstone.decision.application.rag.RagHistoryIdentity
import org.junit.jupiter.api.Assertions.assertArrayEquals
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import java.time.Instant

class RagHistoryCryptoTest {
    private val keys =
        InMemoryRagHistoryKekProvider(
            currentVersion = "kek-v2",
            keys =
                mapOf(
                    "kek-v1" to ByteArray(32) { 0x11 },
                    "kek-v2" to ByteArray(32) { 0x22 },
                ),
        )
    private val crypto = RagHistoryCrypto(keys)

    @Test
    fun `history uses random per-answer DEK independent field nonce and exact AAD`() {
        val identity = identity()
        val encrypted =
            crypto.encrypt(
                identity = identity,
                question = "VaR와 ES의 차이는 무엇인가요?",
                answer = "두 지표는 꼬리위험을 서로 다르게 요약합니다. [cit_1]",
            )

        assertEquals("kek-v2", encrypted.kekVersion)
        assertNotEquals(
            encrypted.question.nonce.toList(),
            encrypted.answer.nonce.toList(),
        )
        assertEquals(12, encrypted.question.nonce.size)
        assertEquals(12, encrypted.answer.nonce.size)
        assertEquals(16, encrypted.question.tag.size)
        assertEquals(16, encrypted.answer.tag.size)
        assertFalse(
            encrypted.question.ciphertext
                .toString(Charsets.UTF_8)
                .contains("VaR와 ES"),
        )

        val decrypted = crypto.decrypt(identity, encrypted)
        assertEquals("VaR와 ES의 차이는 무엇인가요?", decrypted.question)
        assertEquals("두 지표는 꼬리위험을 서로 다르게 요약합니다. [cit_1]", decrypted.answer)
        assertArrayEquals(
            "rag-history-v1|rag_ans_${"a".repeat(32)}|usr_demo_user|1785456000000|question"
                .toByteArray(),
            crypto.aad(identity, "question"),
        )
    }

    @Test
    fun `ciphertext tag aad wrapped key and key version tamper all fail closed`() {
        val identity = identity()
        val encrypted = crypto.encrypt(identity, "question", "answer")
        val mutations =
            listOf(
                encrypted.copy(
                    question =
                        encrypted.question.copy(
                            ciphertext = encrypted.question.ciphertext.flipFirst(),
                        ),
                ),
                encrypted.copy(
                    answer = encrypted.answer.copy(tag = encrypted.answer.tag.flipFirst()),
                ),
                encrypted.copy(wrappedDek = encrypted.wrappedDek.flipFirst()),
                encrypted.copy(wrapTag = encrypted.wrapTag.flipFirst()),
                encrypted.copy(kekVersion = "kek-missing"),
            )

        mutations.forEach { mutation ->
            assertThrows(RagHistoryCorruptedException::class.java) {
                crypto.decrypt(identity, mutation)
            }
        }
        assertThrows(RagHistoryCorruptedException::class.java) {
            crypto.decrypt(identity.copy(ownerUserId = "usr_demo_admin"), encrypted)
        }
        assertThrows(RagHistoryCorruptedException::class.java) {
            crypto.decrypt(identity.copy(createdAt = identity.createdAt.plusMillis(1)), encrypted)
        }
    }

    @Test
    fun `v2 answer identity uses the same authenticated encryption boundary as v1 history`() {
        val identity =
            identity().copy(
                answerId = "rag_01EXAMPLEANSWERID",
            )
        val encrypted = crypto.encrypt(identity, "v2 question", "v2 answer")

        val decrypted = crypto.decrypt(identity, encrypted)

        assertEquals("v2 question", decrypted.question)
        assertEquals("v2 answer", decrypted.answer)
        assertArrayEquals(
            "rag-history-v1|rag_01EXAMPLEANSWERID|usr_demo_user|1785456000000|question"
                .toByteArray(),
            crypto.aad(identity, "question"),
        )
    }

    private fun identity(): RagHistoryIdentity =
        RagHistoryIdentity(
            answerId = "rag_ans_${"a".repeat(32)}",
            ownerUserId = "usr_demo_user",
            createdAt = Instant.parse("2026-07-31T00:00:00Z"),
        )

    private fun ByteArray.flipFirst(): ByteArray =
        copyOf().also { bytes ->
            bytes[0] = (bytes[0].toInt() xor 1).toByte()
        }
}
