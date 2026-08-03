package com.capstone.decision.application.rag

import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.RowMapper
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import tools.jackson.databind.json.JsonMapper
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset

class RagV2RuntimeServiceTest {
    @Test
    fun `retrieval-only uses opaque scope and persists only citation identities before returning DB canonical metadata`() {
        val jdbc = mockk<NamedParameterJdbcTemplate>()
        val provider = mockk<ObjectProvider<NamedParameterJdbcTemplate>>()
        val crypto = mockk<RagHistoryCryptoPort>()
        val evaluation = mockk<RagV2EvaluationPort>()
        val scope = scope()
        val command = command()
        val context = slot<RagV2EvaluationContext>()
        val persistedParams = slot<Map<String, *>>()
        val createdAt = Instant.parse("2026-08-03T10:30:00Z")
        val encrypted = encrypted()

        every { provider.getIfAvailable() } returns jdbc
        every {
            jdbc.query(
                match { it.contains("read_rag_v2_corpus_status") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2CorpusStatus>>(),
            )
        } returns listOf(RagV2CorpusStatus("FULL_READY", "immutable-v2-1", "ABSENT", 100, null))
        every { jdbc.queryForObject(match { it.contains("set_config") }, any<Map<String, *>>(), String::class.java) } returns ""
        every {
            jdbc.query(
                match { it.contains("issue_rag_v2_retrieval_scope") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2RetrievalScope>>(),
            )
        } returns listOf(scope)
        every { evaluation.evaluate(command, capture(context)) } returns retrievalOnly(scope)
        every {
            jdbc.queryForObject(
                "SELECT transaction_timestamp()",
                emptyMap<String, Any>(),
                OffsetDateTime::class.java,
            )
        } returns OffsetDateTime.ofInstant(createdAt, ZoneOffset.UTC)
        every { crypto.encrypt(any(), command.question, "") } returns encrypted
        every {
            jdbc.queryForObject(
                match { it.contains("persist_rag_v2_immutable_retrieval_history") },
                capture(persistedParams),
                String::class.java,
            )
        } returns
            """
            [{
              "citationKind":"PUBLIC_WEB",
              "citationId":"cit_1",
              "sourceId":"src_exact_001",
              "title":"Canonical title",
              "canonicalUrl":"https://example.org/canonical",
              "locator":{"section":"Canonical section"}
            }]
            """.trimIndent()

        val answer = service(provider, crypto, evaluation).ask("usr_demo_user", REQUEST_ID, command)

        assertThat(context.captured.requestId).isEqualTo(REQUEST_ID)
        assertThat(context.captured.ownerScopeClaim).isEqualTo(scope.scopeClaimId)
        assertThat(answer.answerId).startsWith("rag_")
        assertThat(answer.generationStatus).isEqualTo(RagGenerationStatus.RETRIEVAL_ONLY)
        assertThat(answer.answer).isNull()
        val canonicalTitle =
            answer.citations
                .single()
                .path("title")
                .stringValue()
        val canonicalUrl =
            answer.citations
                .single()
                .path("canonicalUrl")
                .stringValue()
        assertThat(canonicalTitle).isEqualTo("Canonical title")
        assertThat(canonicalUrl).isEqualTo("https://example.org/canonical")
        assertThat(persistedParams.captured["citations"].toString())
            .doesNotContain("untrusted-title")
            .doesNotContain("/tmp/owner-document.pdf")
            .doesNotContain("https://untrusted.example.invalid")
        assertThat(persistedParams.captured["citations"].toString())
            .contains("citationId")
            .contains("sourceRevisionId")
            .contains("chunkRevisionId")
            .contains("generationId")
        verify(exactly = 1) { crypto.encrypt(any(), command.question, "") }
    }

    @Test
    fun `generation unavailable returns no history and does not create a provider fallback`() {
        val jdbc = mockk<NamedParameterJdbcTemplate>()
        val provider = mockk<ObjectProvider<NamedParameterJdbcTemplate>>()
        val crypto = mockk<RagHistoryCryptoPort>(relaxed = true)
        val evaluation = mockk<RagV2EvaluationPort>()
        val scope = scope()
        val command = command()

        every { provider.getIfAvailable() } returns jdbc
        every {
            jdbc.query(
                match { it.contains("read_rag_v2_corpus_status") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2CorpusStatus>>(),
            )
        } returns listOf(RagV2CorpusStatus("FULL_READY", "immutable-v2-1", "ABSENT", 100, null))
        every { jdbc.queryForObject(match { it.contains("set_config") }, any<Map<String, *>>(), String::class.java) } returns ""
        every {
            jdbc.query(
                match { it.contains("issue_rag_v2_retrieval_scope") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2RetrievalScope>>(),
            )
        } returns listOf(scope)
        every { evaluation.evaluate(command, any()) } returns
            RagV2EvaluationResult(
                generationStatus = RagGenerationStatus.GENERATION_UNAVAILABLE,
                answer = null,
                citations = emptyList(),
                citationCoverage = 0.0,
                retrievalFailure = false,
                guardrailFlags = listOf("GENERATION_UNAVAILABLE"),
                failureCode = "GENERATION_UNAVAILABLE",
                exact30GenerationId = "",
                oa112GenerationId = "",
                ownerGenerationId = null,
                embeddingProfileId = "",
                policyVersion = 0,
                providerPhysicalAttempts = 0,
                externalProviderCandidate = false,
            )

        val answer = service(provider, crypto, evaluation).ask("usr_demo_user", REQUEST_ID, command)

        assertThat(answer.answerId).isNull()
        assertThat(answer.generationStatus).isEqualTo(RagGenerationStatus.GENERATION_UNAVAILABLE)
        assertThat(answer.citations).isEmpty()
        assertThat(answer.guardrailFlags).containsExactly("GENERATION_UNAVAILABLE")
        verify(exactly = 0) { crypto.encrypt(any(), any(), any()) }
        verify(exactly = 0) {
            jdbc.queryForObject(
                match { it.contains("persist_rag_v2_immutable_retrieval_history") },
                any<Map<String, *>>(),
                String::class.java,
            )
        }
    }

    private fun service(
        provider: ObjectProvider<NamedParameterJdbcTemplate>,
        crypto: RagHistoryCryptoPort,
        evaluation: RagV2EvaluationPort,
    ): RagV2RuntimeService =
        RagV2RuntimeService(
            jdbcProvider = provider,
            cursorPort = mockk(relaxed = true),
            cryptoPort = crypto,
            evaluationPort = evaluation,
            objectMapper = JsonMapper.builder().build(),
        )

    private fun command(): RagAskCommand =
        RagAskCommand(
            question = "공개 근거와 개인 문서 근거를 비교해 주세요.",
            answerMode = RagAnswerMode.CONCISE,
            relatedSymbols = listOf("005930"),
            topics = listOf("FINANCIAL_ENGINEERING"),
        )

    private fun scope(): RagV2RetrievalScope =
        RagV2RetrievalScope(
            scopeClaimId = "rvs_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            exact30GenerationId = EXACT_GENERATION,
            oa112GenerationId = OA_GENERATION,
            ownerGenerationId = null,
            embeddingProfileId = "bge_m3_local_1024_v1",
            policyVersion = 1,
        )

    private fun retrievalOnly(scope: RagV2RetrievalScope): RagV2EvaluationResult =
        RagV2EvaluationResult(
            generationStatus = RagGenerationStatus.RETRIEVAL_ONLY,
            answer = null,
            citations =
                listOf(
                    RagV2RetrievedCitation(
                        citationId = "cit_1",
                        sourceId = "src_exact_001",
                        sourceRevisionId = "srv_exact_001",
                        chunkRevisionId = "rag_v2_chk_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        generationId = scope.exact30GenerationId,
                        citationKind = "PUBLIC_WEB",
                        title = "untrusted-title /tmp/owner-document.pdf",
                        canonicalUrl = "https://untrusted.example.invalid",
                        documentId = null,
                        displayName = null,
                        locator = mapOf("section" to "untrusted"),
                    ),
                ),
            citationCoverage = 1.0,
            retrievalFailure = false,
            guardrailFlags = emptyList(),
            failureCode = "",
            exact30GenerationId = scope.exact30GenerationId,
            oa112GenerationId = scope.oa112GenerationId,
            ownerGenerationId = scope.ownerGenerationId,
            embeddingProfileId = scope.embeddingProfileId,
            policyVersion = scope.policyVersion,
            providerPhysicalAttempts = 0,
            externalProviderCandidate = false,
        )

    private fun encrypted(): RagEncryptedHistoryPayload =
        RagEncryptedHistoryPayload(
            kekVersion = "kek-v1",
            wrapNonce = ByteArray(12),
            wrappedDek = ByteArray(32),
            wrapTag = ByteArray(16),
            question = RagEncryptedFieldPayload(ByteArray(12), byteArrayOf(1), ByteArray(16)),
            answer = RagEncryptedFieldPayload(ByteArray(12), byteArrayOf(), ByteArray(16)),
        )

    private companion object {
        const val REQUEST_ID = "req_v2_runtime_service_0000001"
        const val EXACT_GENERATION = "rgr_11111111111111111111111111111111"
        const val OA_GENERATION = "rgr_22222222222222222222222222222222"
    }
}
