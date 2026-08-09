package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagGenerationStatus
import com.capstone.decision.application.rag.RagV2VertexGenerationCommand
import com.capstone.decision.application.rag.RagV2VertexGenerationPort
import com.capstone.decision.application.rag.RagV2VertexGenerationResult
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component

/**
 * local BGE retrieval은 Vertex activation 없이도 계속 usable해야 한다. 따라서 기본 adapter는 outbound를
 * 만들지 않고 service가 retrieval-only history로 수렴하도록 explicit disabled state만 제공한다.
 */
@Component
@ConditionalOnProperty(
    name = ["app.rag-v2.vertex.enabled"],
    havingValue = "false",
    matchIfMissing = true,
)
class RagV2UnavailableVertexGenerationAdapter : RagV2VertexGenerationPort {
    override fun isActivationEnabled(): Boolean = false

    override fun generate(command: RagV2VertexGenerationCommand): RagV2VertexGenerationResult =
        RagV2VertexGenerationResult(
            generationStatus = RagGenerationStatus.GENERATION_UNAVAILABLE,
            answer = null,
            citationIds = emptyList(),
            failureCode = "GENERATION_UNAVAILABLE",
        )
}
