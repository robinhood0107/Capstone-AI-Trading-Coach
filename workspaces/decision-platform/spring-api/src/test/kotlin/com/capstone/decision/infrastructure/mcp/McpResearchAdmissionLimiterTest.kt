package com.capstone.decision.infrastructure.mcp

import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.ValueSource
import java.util.concurrent.Executors

class McpResearchAdmissionLimiterTest {
    @ParameterizedTest
    @ValueSource(ints = [2, 10, 50])
    fun `distinct owner question admission remains bounded under concurrent load`(count: Int) {
        val limiter = McpResearchAdmissionLimiter(properties())
        Executors.newVirtualThreadPerTaskExecutor().use { executor ->
            val results =
                (1..count)
                    .map { index ->
                        executor.submit<Boolean> {
                            limiter.acquireSearch(McpCaller("usr_load_$index", "mcp_fixture"))
                            true
                        }
                    }.map { it.get() }
            assertThat(results).allMatch { it }
        }
    }

    @Test
    fun `one client cannot exceed its fifteen minute search budget`() {
        val limiter = McpResearchAdmissionLimiter(properties())
        val caller = McpCaller("usr_demo_user", "mcp_fixture")
        repeat(30) { limiter.acquireSearch(caller) }

        assertThatThrownBy { limiter.acquireSearch(caller) }.isInstanceOf(IllegalArgumentException::class.java)
    }

    private fun properties() =
        RagWebToolProperties(
            receiptHmacKey = "x".repeat(32),
            externalResearchMaxSearches = 30,
            externalResearchMaxReads = 120,
            externalResearchUserParallelReads = 4,
            externalResearchGlobalParallelReads = 8,
        )
}
