package com.capstone.decision.infrastructure.grpc

import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test

class StrongLlmHostBudgetTest {
    @Test
    fun `Google discovery and grounded final allow exactly two matching provider permits`() {
        val budget = StrongLlmHostBudget()

        budget.permitProvider("GOOGLE_DISCOVERY")
        budget.permitProvider("GROUNDED_FINAL")
        budget.verifyCompleted(2, "VERTEX_GOOGLE")

        assertThat(budget.providerCalls).isEqualTo(2)
        assertThatThrownBy { budget.permitProvider("GROUNDED_FINAL") }
            .hasMessage("STRONG_LLM_HOST_PROVIDER_AFTER_FINAL")
    }

    @Test
    fun `SearXNG route rejects a fourth tool round before provider permit`() {
        val budget = StrongLlmHostBudget()

        repeat(3) { budget.permitProvider("SEARXNG_TOOL") }

        assertThatThrownBy { budget.permitProvider("SEARXNG_TOOL") }
            .hasMessage("STRONG_LLM_HOST_TOOL_ROUND_BUDGET_EXHAUSTED")
        assertThat(budget.providerCalls).isEqualTo(3)
    }

    @Test
    fun `SearXNG search and read physical caps are host enforced`() {
        val budget = StrongLlmHostBudget()
        budget.permitProvider("SEARXNG_TOOL")

        repeat(3) { budget.permitSearch() }
        repeat(8) { budget.permitRead() }

        assertThatThrownBy { budget.permitSearch() }
            .hasMessage("STRONG_LLM_HOST_SEARCH_BUDGET_EXHAUSTED")
        assertThatThrownBy { budget.permitRead() }
            .hasMessage("STRONG_LLM_HOST_READ_BUDGET_EXHAUSTED")
        assertThat(budget.searchCalls).isEqualTo(3)
        assertThat(budget.readCalls).isEqualTo(8)
    }

    @Test
    fun `completion rejects Python provider count and backend drift`() {
        val google = StrongLlmHostBudget().apply { permitProvider("GOOGLE_DISCOVERY") }
        val fallback = StrongLlmHostBudget().apply { permitProvider("FINAL") }

        assertThatThrownBy { google.verifyCompleted(2, "VERTEX_GOOGLE") }
            .hasMessage("STRONG_LLM_HOST_PROVIDER_COUNT_MISMATCH")
        assertThatThrownBy { fallback.verifyCompleted(1, "VERTEX_GOOGLE") }
            .hasMessage("STRONG_LLM_HOST_SEARCH_BACKEND_MISMATCH")
    }

    @Test
    fun `tools cannot run on Google route`() {
        val budget = StrongLlmHostBudget().apply { permitProvider("GOOGLE_DISCOVERY") }

        assertThatThrownBy { budget.permitSearch() }
            .hasMessage("STRONG_LLM_HOST_TOOL_ROUTE_INVALID")
        assertThatThrownBy { budget.permitRead() }
            .hasMessage("STRONG_LLM_HOST_TOOL_ROUTE_INVALID")
    }
}
