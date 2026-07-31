package com.capstone.decision.application.rag

import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.runs
import io.mockk.verify
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.springframework.beans.factory.ObjectProvider
import java.time.Clock
import java.time.Instant

class RagGuardHistoryServiceTest {
    @Test
    fun `invalid adapter result fails the pending claim before any provider`() {
        val evaluation = mockk<RagEvaluationPort>()
        val persistence = mockk<RagGuardHistoryPersistencePort>()
        val rateLimit = mockk<RagRateLimitPort>()
        val idempotency = mockk<RagIdempotencyPort>()
        val identity =
            RagIdempotencyIdentity(
                scopeHmac = "a".repeat(64),
                requestFingerprint = "b".repeat(64),
            )
        val command =
            RagAskCommand(
                question = "공개 근거를 설명해 주세요",
                answerMode = RagAnswerMode.CONCISE,
                relatedSymbols = emptyList(),
                topics = listOf("RISK"),
            )
        every { rateLimit.acquire("usr_demo_user") } just runs
        every { idempotency.identity("usr_demo_user", "idem-rag-service-0001", command) } returns identity
        every { persistence.claim("usr_demo_user", identity, 120) } returns RagClaimDecision.Claimed
        every { persistence.failBeforeProvider("usr_demo_user", identity) } just runs
        every { evaluation.evaluate(command) } returns
            RagEvaluationResult(
                generationStatus = RagGenerationStatus.RETRIEVAL_ONLY,
                answer = null,
                citations = emptyList(),
                citationCoverage = 0.0,
                retrievalFailure = false,
                guardrailFlags = listOf("FIXTURE_ONLY"),
                providerPhysicalAttempts = 1,
                externalProviderCandidate = false,
            )
        val service =
            service(
                evaluation = evaluation,
                persistence = persistence,
                rateLimit = rateLimit,
                idempotency = idempotency,
            )

        assertThrows<RagGuardHistoryUnavailableException> {
            service.ask(
                ownerUserId = "usr_demo_user",
                requestId = "req_service_boundary",
                rawIdempotencyKey = "idem-rag-service-0001",
                command = command,
            )
        }
        verify(exactly = 1) {
            persistence.failBeforeProvider("usr_demo_user", identity)
        }
    }

    @Test
    fun `partial citation access loss fails closed before history plaintext decrypt`() {
        val persistence = mockk<RagGuardHistoryPersistencePort>()
        val crypto = mockk<RagHistoryCryptoPort>()
        val identity =
            RagHistoryIdentity(
                answerId = "rag_ans_${"a".repeat(32)}",
                ownerUserId = "usr_demo_user",
                createdAt = Instant.parse("2026-07-31T00:00:00Z"),
            )
        val encrypted =
            RagEncryptedHistoryPayload(
                kekVersion = "kek-v1",
                wrapNonce = ByteArray(12),
                wrappedDek = ByteArray(32),
                wrapTag = ByteArray(16),
                question =
                    RagEncryptedFieldPayload(
                        nonce = ByteArray(12),
                        ciphertext = byteArrayOf(1),
                        tag = ByteArray(16),
                    ),
                answer =
                    RagEncryptedFieldPayload(
                        nonce = ByteArray(12),
                        ciphertext = byteArrayOf(2),
                        tag = ByteArray(16),
                    ),
            )
        every { persistence.findHistory(identity.ownerUserId, identity.answerId) } returns
            RagStoredEncryptedHistory(
                identity = identity,
                answerMode = RagAnswerMode.CONCISE,
                generationStatus = RagGenerationStatus.ANSWERED,
                citationCoverage = 1.0,
                retrievalFailure = false,
                guardrailFlags = emptyList(),
                citationCount = 2,
                encrypted = encrypted,
                expiresAt = Instant.parse("2026-08-30T00:00:00Z"),
                helpful = null,
            )
        every { persistence.findCitations(identity.ownerUserId, identity.answerId) } returns
            listOf(
                RagCitation(
                    citationId = "cit_1",
                    sourceId = "src_project_example_001",
                    sourceRevisionId = "src_rev_${"b".repeat(32)}",
                    chunkRevisionId = "rag_chk_${"c".repeat(32)}",
                    generationId = "rag_gen_active",
                    title = "공개 근거",
                    sectionTitle = "근거",
                    canonicalUrl = "https://example.com/evidence",
                ),
            )
        val service = service(persistence = persistence, crypto = crypto)

        assertThrows<RagHistoryCorruptedException> {
            service.getHistory(identity.ownerUserId, identity.answerId)
        }
        verify(exactly = 0) { crypto.decrypt(any(), any()) }
    }

    private fun service(
        evaluation: RagEvaluationPort = mockk(relaxed = true),
        persistence: RagGuardHistoryPersistencePort = mockk(relaxed = true),
        crypto: RagHistoryCryptoPort = mockk(relaxed = true),
        rateLimit: RagRateLimitPort = mockk(relaxed = true),
        idempotency: RagIdempotencyPort = mockk(relaxed = true),
    ): RagGuardHistoryService {
        val policy =
            mockk<RagGuardHistoryPolicy> {
                every { claimTtlSeconds } returns 120
            }
        return RagGuardHistoryService(
            evaluationPort = evaluation,
            persistencePort = persistence,
            cryptoPort = crypto,
            rateLimitPort = rateLimit,
            cursorPort = mockk(relaxed = true),
            idempotencyPort = idempotency,
            policy = policy,
            clockProvider = mockk<ObjectProvider<Clock>>(relaxed = true),
        )
    }
}
