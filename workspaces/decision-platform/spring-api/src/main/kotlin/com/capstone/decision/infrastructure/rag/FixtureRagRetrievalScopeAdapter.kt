package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagCitation
import com.capstone.decision.application.rag.RagRetrievalScope
import com.capstone.decision.application.rag.RagRetrievalScopePort
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component

/**
 * S4.4 compatibility mode에서는 gRPC/DB scope를 생성하지 않고 retrieval-only 결과만 허용한다.
 */
@Component
@ConditionalOnProperty(
    name = ["app.rag.grpc.enabled"],
    havingValue = "false",
    matchIfMissing = true,
)
class FixtureRagRetrievalScopeAdapter : RagRetrievalScopePort {
    override fun issue(
        ownerUserId: String,
        sessionId: String,
        topics: List<String>,
    ): RagRetrievalScope =
        RagRetrievalScope(
            scopeClaimId = FIXTURE_SCOPE,
            policyId = "bge_only_v1",
            policyVersion = 1,
            activeGenerationId = FIXTURE_GENERATION,
            embeddingProfileId = "bge_m3_local_1024_v1",
        )

    override fun requireAuthorized(
        ownerUserId: String,
        sessionId: String,
        scope: RagRetrievalScope,
        citations: List<RagCitation>,
    ) {
        // compatibility adapter에서 citation이 생기면 DB scope 없이 공개되므로 즉시 fail-closed한다.
        require(citations.isEmpty())
        require(scope.scopeClaimId == FIXTURE_SCOPE && scope.activeGenerationId == FIXTURE_GENERATION)
    }

    private companion object {
        const val FIXTURE_SCOPE = "rag_scope_00000000000000000000000000000000"
        const val FIXTURE_GENERATION = "rag_gen_00000000000000000000000000000000"
    }
}
