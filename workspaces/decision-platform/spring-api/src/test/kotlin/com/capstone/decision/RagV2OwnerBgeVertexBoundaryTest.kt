package com.capstone.decision

import com.capstone.decision.application.rag.RagV2RetrievalScope
import com.capstone.decision.application.rag.RagV2RetrievedCitation
import com.capstone.decision.application.rag.requiresRetrievalOnlyForOwnerBgeEvidence
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test

class RagV2OwnerBgeVertexBoundaryTest {
    @Test
    fun `only owner BGE evidence present in top five blocks Vertex input`() {
        val ownerCitation = citation(documentId = "doc_owner_vertex_boundary_001")
        val publicCitation = citation(documentId = null)

        assertThat(
            requiresRetrievalOnlyForOwnerBgeEvidence(
                scope = scope(ownerProfile = "bge_m3_local_1024_v1"),
                citations = listOf(publicCitation, ownerCitation),
            ),
        ).isTrue()
        assertThat(
            requiresRetrievalOnlyForOwnerBgeEvidence(
                scope = scope(ownerProfile = "bge_m3_local_1024_v1"),
                citations = listOf(publicCitation),
            ),
        ).isFalse()
        assertThat(
            requiresRetrievalOnlyForOwnerBgeEvidence(
                scope = scope(ownerProfile = "voyage_context_4_1024_v1"),
                citations = listOf(ownerCitation),
            ),
        ).isFalse()
    }

    private fun scope(ownerProfile: String) =
        RagV2RetrievalScope(
            scopeClaimId = "rvs_${"a".repeat(32)}",
            exact30GenerationId = "rgr_${"1".repeat(32)}",
            oa112GenerationId = "rgr_${"2".repeat(32)}",
            ownerGenerationId = "rgr_${"3".repeat(32)}",
            embeddingProfileId = "voyage_context_4_1024_v1",
            policyVersion = 1,
            ownerEmbeddingProfileId = ownerProfile,
        )

    private fun citation(documentId: String?) =
        RagV2RetrievedCitation(
            citationId = "cit_1",
            sourceId = "src_owner_vertex_boundary_001",
            sourceRevisionId = "srv_owner_vertex_boundary_001",
            chunkRevisionId = "rag_v2_chk_${"4".repeat(32)}",
            generationId = "rgr_${"3".repeat(32)}",
            citationKind = if (documentId == null) "PUBLIC_WEB" else "LOCAL_DOCUMENT",
            title = if (documentId == null) "Public evidence" else null,
            canonicalUrl = if (documentId == null) "https://example.org/evidence" else null,
            documentId = documentId,
            displayName = if (documentId == null) null else "Owner evidence",
            locator = mapOf("section" to "Evidence"),
        )
}
