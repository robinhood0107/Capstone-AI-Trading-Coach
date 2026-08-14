package com.capstone.decision.infrastructure.mcp

import com.capstone.decision.application.rag.RagHistoryCryptoPort
import com.capstone.decision.application.rag.RagV2RetrievalScope
import com.capstone.decision.application.rag.RagV2VertexEvidence
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.transaction.PlatformTransactionManager
import org.springframework.transaction.TransactionDefinition
import org.springframework.transaction.TransactionStatus
import org.springframework.transaction.support.SimpleTransactionStatus
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset

class McpAnswerValidationReceiptRegistryTest {
    @Test
    fun `validation receipts bind the exact evidence snapshot and cap active entries`() {
        val jdbc = mockk<NamedParameterJdbcTemplate>()
        val recorded = mutableListOf<Map<String, *>>()
        every { jdbc.queryForObject(any<String>(), any<Map<String, *>>(), String::class.java) } returns "usr_demo_user"
        every { jdbc.queryForObject(any<String>(), any<Map<String, *>>(), Boolean::class.java) } answers {
            recorded += secondArg<Map<String, *>>()
            true
        }
        val properties =
            RagWebToolProperties(
                enabled = true,
                receiptHmacKey = "h".repeat(32),
                externalResearchMaxContextsPerCaller = 2,
                externalResearchMaxTotalContexts = 2,
            )
        val registry =
            McpAnswerValidationReceiptRegistry(
                properties,
                jdbc,
                NoOpTransactionManager(),
                mockk<RagHistoryCryptoPort>(),
                Clock.fixed(Instant.parse("2026-08-14T00:00:00Z"), ZoneOffset.UTC),
            )
        val caller = McpCaller("usr_demo_user", "mcp_demo_client")
        val context = context()
        val evidence = context.evidence.toList()

        registry.issue(caller, context, evidence, "draft one", "VALID")
        context.evidence.clear()
        registry.issue(caller, context, evidence, "draft two", "VALID")
        assertThatThrownBy { registry.issue(caller, context, evidence, "draft three", "VALID") }
            .isInstanceOf(IllegalArgumentException::class.java)

        assertThat(recorded.map { it["sourceSetHash"] }).containsOnly(recorded.first()["sourceSetHash"])
        verify(exactly = 2) { jdbc.queryForObject(any<String>(), any<Map<String, *>>(), Boolean::class.java) }
    }

    private fun context(): McpResearchContext =
        McpResearchContext(
            id = "s49_ctx_${"a".repeat(32)}",
            ownerUserId = "usr_demo_user",
            oauthClientId = "mcp_demo_client",
            question = "question",
            answerMode = "CONCISE",
            topics = listOf("RISK"),
            requestId = "req_mcp_validation_0001",
            retrievalScope =
                RagV2RetrievalScope(
                    scopeClaimId = "rvs_${"b".repeat(32)}",
                    exact30GenerationId = "rgr_${"c".repeat(32)}",
                    oa112GenerationId = "rgr_${"d".repeat(32)}",
                    ownerGenerationId = null,
                    embeddingProfileId = "voyage_context_4_1024_v1",
                    policyVersion = 1,
                ),
            retrievalCitations = emptyList(),
            retrievalEvidence = emptyList(),
            evidence =
                mutableListOf(
                    RagV2VertexEvidence(
                        1,
                        "cit_1",
                        "rag_v2_chk_${"e".repeat(32)}",
                        "evidence",
                        "f".repeat(64),
                    ),
                ),
            searchableUrls = mutableSetOf(),
            expiresAt = Instant.parse("2026-08-14T00:15:00Z"),
        )

    private class NoOpTransactionManager : PlatformTransactionManager {
        override fun getTransaction(definition: TransactionDefinition?): TransactionStatus = SimpleTransactionStatus()

        override fun commit(status: TransactionStatus) = Unit

        override fun rollback(status: TransactionStatus) = Unit
    }
}
