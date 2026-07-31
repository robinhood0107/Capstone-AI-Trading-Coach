package com.capstone.decision.api.rag

import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.application.rag.RagAnswerProjection
import com.capstone.decision.application.rag.RagGenerationStatus
import com.capstone.decision.application.rag.RagGuardHistoryUnavailableException
import com.capstone.decision.application.rag.RagPublicCitation
import org.junit.jupiter.api.Assertions.assertSame
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import tools.jackson.databind.json.JsonMapper

class RagPublicResponseBudgetTest {
    private val budget = RagPublicResponseBudget(JsonMapper.builder().build())

    @Test
    fun `small public response remains inside the exact 32 KiB budget`() {
        val response =
            ApiResponseFactory.success(
                requestId = "req_budget_small",
                data =
                    RagAnswerProjection(
                        requestId = "req_budget_small",
                        answerId = "rag_ans_${"1".repeat(32)}",
                        generationStatus = RagGenerationStatus.RETRIEVAL_ONLY,
                        answer = null,
                        citationCoverage = 0.0,
                        retrievalFailure = false,
                        citations = emptyList(),
                        guardrailFlags = listOf("FIXTURE_ONLY"),
                    ),
            )

        assertSame(response, budget.requireWithin(response))
    }

    @Test
    fun `JSON escaping cannot expand a bounded answer beyond 32 KiB`() {
        val citation =
            RagPublicCitation(
                citationId = "cit_1",
                sourceId = "src_project_response_budget_001",
                title = "\\".repeat(300),
                sectionTitle = "\\".repeat(512),
                canonicalUrl = "https://example.com/" + "a".repeat(2_000),
            )
        val response =
            ApiResponseFactory.success(
                requestId = "req_budget_large",
                data =
                    RagAnswerProjection(
                        requestId = "req_budget_large",
                        answerId = "rag_ans_${"2".repeat(32)}",
                        generationStatus = RagGenerationStatus.ANSWERED,
                        answer = "\\".repeat(8_192),
                        citationCoverage = 1.0,
                        retrievalFailure = false,
                        citations =
                            (1..5).map { index ->
                                citation.copy(citationId = "cit_$index")
                            },
                        guardrailFlags = emptyList(),
                    ),
            )

        assertThrows<RagGuardHistoryUnavailableException> {
            budget.requireWithin(response)
        }
    }
}
