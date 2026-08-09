package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagAskCommand
import com.capstone.decision.application.rag.RagGenerationStatus
import com.capstone.decision.application.rag.RagV2EvaluationContext
import com.capstone.decision.application.rag.RagV2EvaluationPort
import com.capstone.decision.application.rag.RagV2EvaluationResult
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component

/**
 * local BGE process가 아직 기동되지 않은 환경은 corpus를 다른 provider로 대체하지 않는다.
 * 이 fallback은 외부 호출 없이 typed unavailable 결과만 돌려 Spring API가 fail-closed 하게 한다.
 */
@Component
@ConditionalOnProperty(
    name = ["app.rag-v2.grpc.enabled"],
    havingValue = "false",
    matchIfMissing = true,
)
class RagV2UnavailableEvaluationAdapter : RagV2EvaluationPort {
    override fun evaluate(
        command: RagAskCommand,
        context: RagV2EvaluationContext,
    ): RagV2EvaluationResult =
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
}
