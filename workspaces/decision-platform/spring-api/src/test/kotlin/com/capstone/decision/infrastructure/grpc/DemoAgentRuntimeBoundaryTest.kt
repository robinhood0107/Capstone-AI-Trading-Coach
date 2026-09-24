package com.capstone.decision.infrastructure.grpc

import com.capstone.decision.application.rag.RagAnswerMode
import com.capstone.decision.application.rag.RagV2EffectiveConsent
import com.capstone.decision.application.rag.RagV2RetrievalScope
import com.capstone.decision.application.rag.RagV2VertexEvidence
import com.capstone.decision.application.rag.RagV2VertexGenerationCommand
import com.capstone.decision.application.rag.StrongLlmAnswerBasis
import com.capstone.decision.contract.internal.s49.Completed
import org.junit.jupiter.api.Assertions.assertDoesNotThrow
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test

class DemoAgentRuntimeBoundaryTest {
    @Test
    fun `only public evidence under the in-memory demo marker enters provider`() {
        val input = command()
        assertDoesNotThrow { DemoAgentRuntimeBoundary.requireInput(input) }
        assertThrows(IllegalArgumentException::class.java) {
            DemoAgentRuntimeBoundary.requireInput(input.copy(ownerUserId = "usr_other"))
        }
        assertThrows(IllegalArgumentException::class.java) {
            DemoAgentRuntimeBoundary.requireInput(
                input.copy(evidence = input.evidence.map { it.copy(ownerPrivate = true) }),
            )
        }
    }

    @Test
    fun `demo refuses external grounding and unsupported model-only claims`() {
        val hostBudget = StrongLlmHostBudget()
        assertDoesNotThrow {
            DemoAgentRuntimeBoundary.requireOutput(Completed.getDefaultInstance(), hostBudget)
            DemoAgentRuntimeBoundary.requireBasis(StrongLlmAnswerBasis.EVIDENCE)
        }
        assertThrows(IllegalStateException::class.java) {
            DemoAgentRuntimeBoundary.requireOutput(
                Completed.newBuilder().setGoogleGroundingQueryCount(1).build(),
                hostBudget,
            )
        }
        assertThrows(IllegalStateException::class.java) {
            DemoAgentRuntimeBoundary.requireBasis(StrongLlmAnswerBasis.MODEL_KNOWLEDGE)
        }
    }

    private fun command(): RagV2VertexGenerationCommand =
        RagV2VertexGenerationCommand(
            ownerUserId = DEMO_INTERNAL_OWNER_USER_ID,
            requestId = "req_demo_boundary_0001",
            question = "분산투자란 무엇인가요?",
            answerMode = RagAnswerMode.CONCISE,
            scope = RagV2RetrievalScope("rvs_${"a".repeat(32)}", "demo", "demo", null, "demo", 1),
            consent =
                RagV2EffectiveConsent(
                    consentEventId = "demo",
                    effective = false,
                    policyDigest = "0".repeat(64),
                    processorSetDigest = "0".repeat(64),
                    state = "NOT_REQUIRED",
                ),
            evidence =
                listOf(
                    RagV2VertexEvidence(
                        1,
                        "cit_1",
                        "rag_v2_chk_${"a".repeat(32)}",
                        "분산은 위험 집중을 줄입니다.",
                        "a".repeat(64),
                    ),
                ),
        )
}
