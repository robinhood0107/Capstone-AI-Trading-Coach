package com.capstone.decision.application.rag

import com.capstone.decision.application.security.ActorRlsScopePort
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.RowMapper
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.transaction.PlatformTransactionManager
import org.springframework.transaction.TransactionDefinition
import org.springframework.transaction.TransactionStatus
import org.springframework.transaction.support.SimpleTransactionStatus
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
        val transactionManager = TrackingTransactionManager()

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
        every { evaluation.evaluate(command, capture(context)) } answers {
            assertThat(transactionManager.active).isFalse()
            retrievalOnly(scope)
        }
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

        val answer =
            service(provider, crypto, evaluation, transactionManager = transactionManager)
                .ask("usr_demo_user", REQUEST_ID, command)

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
        verify(exactly = 1) {
            jdbc.query(
                match { it.contains("issue_rag_v2_retrieval_scope_v2") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2RetrievalScope>>(),
            )
        }
        verify(exactly = 0) {
            jdbc.query(
                match { it.contains("issue_rag_v2_retrieval_scope_v3") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2RetrievalScope>>(),
            )
        }
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

    @Test
    fun `scope-selected voyage retrieval requires effective consent and permits one packet-gated query attempt`() {
        val jdbc = mockk<NamedParameterJdbcTemplate>()
        val provider = mockk<ObjectProvider<NamedParameterJdbcTemplate>>()
        val crypto = mockk<RagHistoryCryptoPort>()
        val evaluation = mockk<RagV2EvaluationPort>()
        val scope = scope(profile = "voyage_context_4_1024_v1")
        val command = command()
        val context = slot<RagV2EvaluationContext>()
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
        every {
            jdbc.query(
                match { it.contains("read_rag_v2_immutable_effective_consent") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2RuntimeService.RagV2StoredEffectiveConsent>>(),
            )
        } returns
            listOf(
                RagV2RuntimeService.RagV2StoredEffectiveConsent(
                    consentEventId = "rce_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    action = "GRANT",
                    policyDigest = "a".repeat(64),
                    processorSetDigest = "b".repeat(64),
                ),
            )
        every {
            jdbc.queryForObject(
                match { it.contains("authorize_s4_9_runtime_voyage_query") },
                any<Map<String, *>>(),
                String::class.java,
            )
        } returns "s49_vqa_${"c".repeat(32)}"
        every { evaluation.evaluate(command, capture(context)) } returns
            retrievalOnly(scope).copy(
                providerPhysicalAttempts = 1,
                voyagePhysicalCalls = 1,
            )
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
                any<Map<String, *>>(),
                String::class.java,
            )
        } returns
            """
            [{"citationKind":"PUBLIC_WEB","citationId":"cit_1","sourceId":"src_exact_001","title":"Canonical title","canonicalUrl":"https://example.org/canonical","locator":{"section":"Canonical section"}}]
            """.trimIndent()

        val answer = service(provider, crypto, evaluation).ask("usr_demo_user", REQUEST_ID, command)

        assertThat(answer.generationStatus).isEqualTo(RagGenerationStatus.RETRIEVAL_ONLY)
        verify(exactly = 1) { evaluation.evaluate(command, any()) }
        assertThat(context.captured.externalQueryConsentGranted).isTrue()
        verify(exactly = 1) {
            jdbc.queryForObject(
                match { it.contains("authorize_s4_9_runtime_voyage_query") },
                match<Map<String, *>> {
                    it["scopeClaimId"] == scope.scopeClaimId &&
                        (it["questionSha256"] as? String)?.matches(Regex("^[0-9a-f]{64}$")) == true &&
                        it["questionSha256"] != command.question
                },
                String::class.java,
            )
        }
    }

    @Test
    fun `scope-selected voyage retrieval stops before loopback evaluation when effective consent is revoked`() {
        val jdbc = mockk<NamedParameterJdbcTemplate>()
        val provider = mockk<ObjectProvider<NamedParameterJdbcTemplate>>()
        val crypto = mockk<RagHistoryCryptoPort>(relaxed = true)
        val evaluation = mockk<RagV2EvaluationPort>(relaxed = true)
        val scope = scope(profile = "voyage_context_4_1024_v1")
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
        every {
            jdbc.query(
                match { it.contains("read_rag_v2_immutable_effective_consent") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2RuntimeService.RagV2StoredEffectiveConsent>>(),
            )
        } returns
            listOf(
                RagV2RuntimeService.RagV2StoredEffectiveConsent(
                    consentEventId = "rce_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    action = "REVOKE",
                    policyDigest = "a".repeat(64),
                    processorSetDigest = "b".repeat(64),
                ),
            )

        assertThatThrownBy {
            service(provider, crypto, evaluation).ask("usr_demo_user", REQUEST_ID, command)
        }.isInstanceOf(RagV2ExternalConsentRequiredException::class.java)

        verify(exactly = 0) { evaluation.evaluate(any(), any()) }
        verify(exactly = 0) { crypto.encrypt(any(), any(), any()) }
    }

    @Test
    fun `MCP insufficient retrieval creates an empty research context for bounded web evidence`() {
        val jdbc = mockk<NamedParameterJdbcTemplate>()
        val provider = mockk<ObjectProvider<NamedParameterJdbcTemplate>>()
        val crypto = mockk<RagHistoryCryptoPort>(relaxed = true)
        val evaluation = mockk<RagV2EvaluationPort>()
        val vertexEvidence = mockk<RagV2VertexEvidencePort>()
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
                match { it.contains("issue_s4_9_mcp_retrieval_scope") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2RetrievalScope>>(),
            )
        } returns listOf(scope)
        every { evaluation.evaluate(command, any()) } returns
            RagV2EvaluationResult(
                generationStatus = RagGenerationStatus.RETRIEVAL_FAILURE,
                answer = null,
                citations = emptyList(),
                citationCoverage = 0.0,
                retrievalFailure = true,
                guardrailFlags = emptyList(),
                failureCode = "RAG_INSUFFICIENT_EVIDENCE",
                exact30GenerationId = "",
                oa112GenerationId = "",
                ownerGenerationId = null,
                embeddingProfileId = "",
                policyVersion = 0,
                providerPhysicalAttempts = 0,
                externalProviderCandidate = false,
            )

        val result =
            service(provider, crypto, evaluation, vertexEvidence = vertexEvidence).searchEvidence(
                "usr_demo_user",
                REQUEST_ID,
                command,
                includeOwner = false,
            )

        assertThat(result.scope).isEqualTo(scope)
        assertThat(result.citations).isEmpty()
        assertThat(result.evidence).isEmpty()
        verify(exactly = 0) { vertexEvidence.resolve(any(), any(), any(), any()) }
    }

    @Test
    fun `MCP non-empty failure still exposes only the validated content-free leaf`() {
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
                match { it.contains("issue_s4_9_mcp_retrieval_scope") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2RetrievalScope>>(),
            )
        } returns listOf(scope)
        every { evaluation.evaluate(command, any()) } returns
            insufficientEvaluation().copy(failureCode = "RAG_QUERY_PROVIDER_UNAVAILABLE")

        assertThatThrownBy {
            service(provider, crypto, evaluation).searchEvidence(
                "usr_demo_user",
                REQUEST_ID,
                command,
                includeOwner = false,
            )
        }.isInstanceOf(RagV2McpSearchUnavailableException::class.java)
            .hasMessage("S4_9_MCP_RAG_SEARCH_RAG_QUERY_PROVIDER_UNAVAILABLE")
            .message()
            .doesNotContain(command.question)
    }

    @Test
    fun `enabled Strong LLM answers timeless education when retrieval has no evidence`() {
        val jdbc = mockk<NamedParameterJdbcTemplate>()
        val provider = mockk<ObjectProvider<NamedParameterJdbcTemplate>>()
        val crypto = mockk<RagHistoryCryptoPort>()
        val evaluation = mockk<RagV2EvaluationPort>()
        val vertexEvidence = mockk<RagV2VertexEvidencePort>()
        val vertexGeneration = mockk<RagV2VertexGenerationPort>()
        val generatedCommand = slot<RagV2VertexGenerationCommand>()
        val scope = scope(profile = "voyage_context_4_1024_v1")
        val command = command()
        val createdAt = Instant.parse("2026-08-14T12:00:00Z")

        every { provider.getIfAvailable() } returns jdbc
        every { vertexGeneration.isActivationEnabled() } returns true
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
                match { it.contains("read_rag_v2_vertex_prepared_scope") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2PreparedScope>>(),
            )
        } returns listOf(RagV2PreparedScope(scope, createdAt.plusSeconds(300)))
        every {
            jdbc.query(
                match { it.contains("read_rag_v2_immutable_effective_consent") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2RuntimeService.RagV2StoredEffectiveConsent>>(),
            )
        } returns
            listOf(
                RagV2RuntimeService.RagV2StoredEffectiveConsent(
                    consentEventId = "rce_${"a".repeat(32)}",
                    action = "GRANT",
                    policyDigest = "a".repeat(64),
                    processorSetDigest = "b".repeat(64),
                ),
            )
        every {
            jdbc.queryForObject(
                match { it.contains("authorize_s4_9_runtime_voyage_query") },
                any<Map<String, *>>(),
                String::class.java,
            )
        } returns "s49_vqa_${"c".repeat(32)}"
        every { evaluation.evaluate(command, any()) } returns
            insufficientEvaluation(providerAttempts = 1).copy(voyagePhysicalCalls = 1)
        every { vertexGeneration.generate(capture(generatedCommand)) } returns
            RagV2VertexGenerationResult(
                generationStatus = RagGenerationStatus.ANSWERED,
                answer = "분산투자는 서로 다른 위험 요인을 함께 구성하는 일반적인 위험 관리 개념입니다.",
                citationIds = emptyList(),
                failureCode = "",
                answerBasis = StrongLlmAnswerBasis.MODEL_KNOWLEDGE,
                validationStatus = StrongLlmValidationStatus.VALID,
                citationCoverage = 0.0,
            )
        every {
            jdbc.queryForObject(
                "SELECT transaction_timestamp()",
                emptyMap<String, Any>(),
                OffsetDateTime::class.java,
            )
        } returns OffsetDateTime.ofInstant(createdAt, ZoneOffset.UTC)
        every {
            crypto.encrypt(any(), command.question, any())
        } returns
            encrypted().copy(
                answer = RagEncryptedFieldPayload(ByteArray(12), byteArrayOf(1), ByteArray(16)),
            )
        every {
            jdbc.queryForObject(
                match { it.contains("persist_s4_9_strong_llm_history") },
                any<Map<String, *>>(),
                String::class.java,
            )
        } returns "[]"

        val answer =
            service(
                provider,
                crypto,
                evaluation,
                vertexEvidence = vertexEvidence,
                vertexGeneration = vertexGeneration,
            ).ask("usr_demo_user", REQUEST_ID, command, scope.scopeClaimId)

        assertThat(answer.generationStatus).isEqualTo(RagGenerationStatus.ANSWERED)
        assertThat(answer.citations).isEmpty()
        assertThat(answer.guardrailFlags).containsExactly("MODEL_KNOWLEDGE_ONLY")
        assertThat(generatedCommand.captured.evidence).isEmpty()
        verify(exactly = 0) { vertexEvidence.resolve(any(), any(), any(), any()) }
    }

    @Test
    fun `empty MCP context revalidates the exact DB scope without fabricating evidence`() {
        val jdbc = mockk<NamedParameterJdbcTemplate>()
        val provider = mockk<ObjectProvider<NamedParameterJdbcTemplate>>()
        val vertexEvidence = mockk<RagV2VertexEvidencePort>()
        val scope = scope(profile = "voyage_context_4_1024_v1")

        every { provider.getIfAvailable() } returns jdbc
        every { jdbc.queryForObject(match { it.contains("set_config") }, any<Map<String, *>>(), String::class.java) } returns ""
        every {
            jdbc.query(
                match { it.contains("read_rag_v2_vertex_prepared_scope_v2") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2PreparedScope>>(),
            )
        } returns listOf(RagV2PreparedScope(scope, Instant.parse("2026-08-14T12:05:00Z")))

        service(
            provider,
            mockk(relaxed = true),
            mockk(relaxed = true),
            vertexEvidence = vertexEvidence,
        ).requireResearchEvidenceCurrent(
            ownerUserId = "usr_demo_user",
            requestId = REQUEST_ID,
            scope = scope,
            topics = listOf("FINANCIAL_ENGINEERING"),
            citations = emptyList(),
            expectedEvidence = emptyList(),
        )

        verify(exactly = 1) {
            jdbc.query(
                match { it.contains("read_rag_v2_vertex_prepared_scope_v2") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2PreparedScope>>(),
            )
        }
        verify(exactly = 0) { vertexEvidence.resolve(any(), any(), any(), any()) }
    }

    @Test
    fun `Vertex preparation returns one stable content-free scope and HMAC without evaluation or generation`() {
        val jdbc = mockk<NamedParameterJdbcTemplate>()
        val provider = mockk<ObjectProvider<NamedParameterJdbcTemplate>>()
        val crypto = mockk<RagHistoryCryptoPort>(relaxed = true)
        val evaluation = mockk<RagV2EvaluationPort>(relaxed = true)
        val vertexGeneration = mockk<RagV2VertexGenerationPort>()
        val fingerprint = mockk<RagV2VertexQuestionFingerprintPort>()
        val scope = scope()
        val command = command()
        val expiresAt = Instant.parse("2026-08-03T10:32:00Z")

        every { provider.getIfAvailable() } returns jdbc
        every { vertexGeneration.isActivationEnabled() } returns true
        every { fingerprint.fingerprint("usr_demo_user", command) } returns "f".repeat(64)
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
                match { it.contains("read_rag_v2_immutable_effective_consent") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2RuntimeService.RagV2StoredEffectiveConsent>>(),
            )
        } returns
            listOf(
                RagV2RuntimeService.RagV2StoredEffectiveConsent(
                    consentEventId = "rce_${"a".repeat(32)}",
                    action = "GRANT",
                    policyDigest = "a".repeat(64),
                    processorSetDigest = "b".repeat(64),
                ),
            )
        every {
            jdbc.query(
                match { it.contains("issue_rag_v2_retrieval_scope") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2RetrievalScope>>(),
            )
        } returns listOf(scope)
        every {
            jdbc.query(
                match { it.contains("read_rag_v2_vertex_prepared_scope") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2PreparedScope>>(),
            )
        } returns listOf(RagV2PreparedScope(scope, expiresAt))

        val preparation =
            service(
                provider = provider,
                crypto = crypto,
                evaluation = evaluation,
                vertexGeneration = vertexGeneration,
                vertexQuestionFingerprint = fingerprint,
            ).prepareVertexGeneration("usr_demo_user", REQUEST_ID, command)

        assertThat(preparation.scopeClaimId).isEqualTo(scope.scopeClaimId)
        assertThat(preparation.questionFingerprintHmac).isEqualTo("f".repeat(64))
        assertThat(preparation.expiresAt).isEqualTo(expiresAt)
        assertThat(preparation.scopeTtlSeconds).isEqualTo(300)
        assertThat(preparation.rawQuestionStored).isFalse()
        assertThat(preparation.rawEvidenceStored).isFalse()
        verify(exactly = 1) {
            jdbc.query(
                match { it.contains("issue_rag_v2_retrieval_scope_v3") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2RetrievalScope>>(),
            )
        }
        verify(exactly = 0) { evaluation.evaluate(any(), any()) }
        verify(exactly = 0) { crypto.encrypt(any(), any(), any()) }
    }

    @Test
    fun `enabled Vertex resumes the prepared scope rather than issuing a new random scope`() {
        val jdbc = mockk<NamedParameterJdbcTemplate>()
        val provider = mockk<ObjectProvider<NamedParameterJdbcTemplate>>()
        val crypto = mockk<RagHistoryCryptoPort>(relaxed = true)
        val evaluation = mockk<RagV2EvaluationPort>()
        val vertexGeneration = mockk<RagV2VertexGenerationPort>()
        val scope = scope()
        val command = command()

        every { provider.getIfAvailable() } returns jdbc
        every { vertexGeneration.isActivationEnabled() } returns true
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
                match { it.contains("read_rag_v2_vertex_prepared_scope") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2PreparedScope>>(),
            )
        } returns listOf(RagV2PreparedScope(scope, Instant.parse("2026-08-03T10:32:00Z")))
        every { evaluation.evaluate(command, any()) } returns unavailableEvaluation()

        val answer =
            service(
                provider = provider,
                crypto = crypto,
                evaluation = evaluation,
                vertexGeneration = vertexGeneration,
            ).ask(
                ownerUserId = "usr_demo_user",
                requestId = REQUEST_ID,
                command = command,
                vertexScopeClaimId = scope.scopeClaimId,
            )

        assertThat(answer.generationStatus).isEqualTo(RagGenerationStatus.GENERATION_UNAVAILABLE)
        verify(exactly = 1) { evaluation.evaluate(command, any()) }
        verify(exactly = 0) {
            jdbc.query(
                match { it.contains("issue_rag_v2_retrieval_scope") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2RetrievalScope>>(),
            )
        }
    }

    @Test
    fun `disabled Vertex rejects a prepared scope without issuing retrieval or calling evaluation`() {
        val jdbc = mockk<NamedParameterJdbcTemplate>()
        val provider = mockk<ObjectProvider<NamedParameterJdbcTemplate>>()
        val crypto = mockk<RagHistoryCryptoPort>(relaxed = true)
        val evaluation = mockk<RagV2EvaluationPort>(relaxed = true)

        every { provider.getIfAvailable() } returns jdbc
        every {
            jdbc.query(
                match { it.contains("read_rag_v2_corpus_status") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2CorpusStatus>>(),
            )
        } returns listOf(RagV2CorpusStatus("FULL_READY", "immutable-v2-1", "ABSENT", 100, null))
        every { jdbc.queryForObject(match { it.contains("set_config") }, any<Map<String, *>>(), String::class.java) } returns ""

        assertThatThrownBy {
            service(provider, crypto, evaluation).ask(
                ownerUserId = "usr_demo_user",
                requestId = REQUEST_ID,
                command = command(),
                vertexScopeClaimId = scope().scopeClaimId,
            )
        }.isInstanceOf(RagV2VertexPreparationUnavailableException::class.java)

        verify(exactly = 0) {
            jdbc.query(
                match { it.contains("issue_rag_v2_retrieval_scope") || it.contains("read_rag_v2_vertex_prepared_scope") },
                any<Map<String, *>>(),
                any<RowMapper<RagV2RetrievalScope>>(),
            )
        }
        verify(exactly = 0) { evaluation.evaluate(any(), any()) }
    }

    private fun service(
        provider: ObjectProvider<NamedParameterJdbcTemplate>,
        crypto: RagHistoryCryptoPort,
        evaluation: RagV2EvaluationPort,
        vertexEvidence: RagV2VertexEvidencePort = mockk(relaxed = true),
        vertexGeneration: RagV2VertexGenerationPort =
            mockk {
                every { isActivationEnabled() } returns false
            },
        vertexQuestionFingerprint: RagV2VertexQuestionFingerprintPort = mockk(relaxed = true),
        transactionManager: PlatformTransactionManager = TrackingTransactionManager(),
    ): RagV2RuntimeService {
        val transactionManagerProvider = mockk<ObjectProvider<PlatformTransactionManager>>()
        every { transactionManagerProvider.getIfAvailable() } returns transactionManager
        return RagV2RuntimeService(
            jdbcProvider = provider,
            cursorPort = mockk(relaxed = true),
            cryptoPort = crypto,
            evaluationPort = evaluation,
            vertexEvidencePort = vertexEvidence,
            vertexGenerationPort = vertexGeneration,
            vertexQuestionFingerprintPort = vertexQuestionFingerprint,
            // 자동 저술 bean이 없는 배포와 같은 상태다. 이 테스트가 보는 것은 운영자 패킷 경로다.
            vertexActivationAuthorProvider = mockk { every { getIfAvailable() } returns null },
            objectMapper = JsonMapper.builder().build(),
            transactionManagerProvider = transactionManagerProvider,
            actorRlsScope = mockk<ActorRlsScopePort>(relaxed = true),
            // 설정 bean이 없는 배포와 같은 상태다. 그때 corpus status의 설정 필드는 전부 null이고
            // 화면은 배포 기본값이 쓰이고 있다고 읽는다.
            strongLlmSettingsProvider = mockk { every { getIfAvailable() } returns null },
        )
    }

    private class TrackingTransactionManager : PlatformTransactionManager {
        var active = false
            private set

        override fun getTransaction(definition: TransactionDefinition?): TransactionStatus {
            check(!active) { "nested transaction is not expected in this service boundary" }
            active = true
            return SimpleTransactionStatus()
        }

        override fun commit(status: TransactionStatus) {
            active = false
        }

        override fun rollback(status: TransactionStatus) {
            active = false
        }
    }

    private fun command(): RagAskCommand =
        RagAskCommand(
            question = "공개 근거와 개인 문서 근거를 비교해 주세요.",
            answerMode = RagAnswerMode.CONCISE,
            relatedSymbols = listOf("005930"),
            topics = listOf("FINANCIAL_ENGINEERING"),
        )

    private fun scope(profile: String = "bge_m3_local_1024_v1"): RagV2RetrievalScope =
        RagV2RetrievalScope(
            scopeClaimId = "rvs_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            exact30GenerationId = EXACT_GENERATION,
            oa112GenerationId = OA_GENERATION,
            ownerGenerationId = null,
            embeddingProfileId = profile,
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

    private fun unavailableEvaluation(): RagV2EvaluationResult =
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

    private fun insufficientEvaluation(providerAttempts: Int = 0): RagV2EvaluationResult =
        RagV2EvaluationResult(
            generationStatus = RagGenerationStatus.RETRIEVAL_FAILURE,
            answer = null,
            citations = emptyList(),
            citationCoverage = 0.0,
            retrievalFailure = true,
            guardrailFlags = emptyList(),
            failureCode = "RAG_INSUFFICIENT_EVIDENCE",
            exact30GenerationId = "",
            oa112GenerationId = "",
            ownerGenerationId = null,
            embeddingProfileId = "",
            policyVersion = 0,
            providerPhysicalAttempts = providerAttempts,
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
