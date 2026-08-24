package com.capstone.decision.infrastructure.mcp

import com.capstone.decision.application.rag.RagV2RetrievalScope
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import java.time.Clock
import java.time.Instant
import java.time.ZoneId
import java.util.concurrent.Executors

class McpResearchContextRegistryTest {
    @Test
    fun `parallel reservations cannot exceed the context search or read budget`() {
        val registry =
            McpResearchContextRegistry(
                RagWebToolProperties(enabled = true, receiptHmacKey = "h".repeat(32)),
            )
        val (context, _) =
            registry.create(
                ownerUserId = "usr_demo_user",
                oauthClientId = "mcp_demo_client",
                question = "test",
                answerMode = "DETAILED",
                topics = listOf("RISK"),
                requestId = "req_mcp_context_budget_0001",
                retrievalScope =
                    RagV2RetrievalScope(
                        scopeClaimId = "rvs_${"a".repeat(32)}",
                        exact30GenerationId = "rgr_${"b".repeat(32)}",
                        oa112GenerationId = "rgr_${"c".repeat(32)}",
                        ownerGenerationId = null,
                        embeddingProfileId = "voyage_context_4_1024_v1",
                        policyVersion = 1,
                    ),
                retrievalCitations = emptyList(),
                evidence = emptyList(),
            )
        registry.addSearchableUrls(context, listOf("https://example.com/evidence"))

        val searchSuccesses = parallelSuccessCount(50) { registry.reserveSearch(context, "DETAILED", 3) }
        val readSuccesses =
            parallelSuccessCount(50) {
                registry.reserveRead(context, "DETAILED", 8, "https://example.com/evidence")
            }

        assertThat(searchSuccesses).isEqualTo(3)
        assertThat(readSuccesses).isEqualTo(8)
        assertThat(context.searchCount).isEqualTo(3)
        assertThat(context.readCount).isEqualTo(8)
    }

    @Test
    fun `tool mode is bound to the original context and active contexts are capped`() {
        val registry =
            McpResearchContextRegistry(
                RagWebToolProperties(
                    enabled = true,
                    receiptHmacKey = "h".repeat(32),
                    externalResearchMaxContextsPerCaller = 2,
                    externalResearchMaxTotalContexts = 2,
                ),
            )
        val first = createContext(registry, "req_mcp_context_mode_0001", "CONCISE")

        assertThatThrownBy { registry.reserveSearch(first, "DETAILED", 2) }
            .isInstanceOf(IllegalArgumentException::class.java)
        createContext(registry, "req_mcp_context_mode_0002", "CONCISE")
        assertThatThrownBy { createContext(registry, "req_mcp_context_mode_0003", "CONCISE") }
            .isInstanceOf(IllegalArgumentException::class.java)
    }

    @Test
    fun `expired and explicitly closed contexts close matching facade sessions`() {
        val clock = MutableClock(Instant.parse("2026-08-24T00:00:00Z"))
        val closed = mutableListOf<String>()
        val registry =
            McpResearchContextRegistry(
                RagWebToolProperties(enabled = true, receiptHmacKey = "h".repeat(32)),
                clock,
            )
        registry.bindCloseListener(closed::add)
        val first = createContext(registry, "req_mcp_context_expiry_0001", "CONCISE")
        clock.current = clock.current.plusSeconds(901)

        assertThatThrownBy { registry.requireById(first.id, "usr_demo_user", "mcp_demo_client") }
            .isInstanceOf(IllegalArgumentException::class.java)
        assertThat(closed).containsExactly(first.id)

        val second = createContext(registry, "req_mcp_context_close_0002", "CONCISE")
        registry.close(second.id)
        assertThat(closed).containsExactly(first.id, second.id)
    }

    private fun createContext(
        registry: McpResearchContextRegistry,
        requestId: String,
        answerMode: String,
    ): McpResearchContext =
        registry
            .create(
                ownerUserId = "usr_demo_user",
                oauthClientId = "mcp_demo_client",
                question = "test",
                answerMode = answerMode,
                topics = listOf("RISK"),
                requestId = requestId,
                retrievalScope =
                    RagV2RetrievalScope(
                        scopeClaimId = "rvs_${"a".repeat(32)}",
                        exact30GenerationId = "rgr_${"b".repeat(32)}",
                        oa112GenerationId = "rgr_${"c".repeat(32)}",
                        ownerGenerationId = null,
                        embeddingProfileId = "voyage_context_4_1024_v1",
                        policyVersion = 1,
                    ),
                retrievalCitations = emptyList(),
                evidence = emptyList(),
            ).first

    private fun parallelSuccessCount(
        count: Int,
        action: () -> Unit,
    ): Int =
        Executors.newVirtualThreadPerTaskExecutor().use { executor ->
            (1..count)
                .map { executor.submit<Boolean> { runCatching(action).isSuccess } }
                .count { it.get() }
        }

    private class MutableClock(
        var current: Instant,
    ) : Clock() {
        override fun getZone(): ZoneId = ZoneId.of("UTC")

        override fun withZone(zone: ZoneId): Clock = this

        override fun instant(): Instant = current
    }
}
