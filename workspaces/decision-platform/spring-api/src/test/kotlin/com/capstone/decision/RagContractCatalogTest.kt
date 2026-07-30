package com.capstone.decision

import com.capstone.decision.infrastructure.rag.RagContractCatalog
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import tools.jackson.databind.json.JsonMapper

class RagContractCatalogTest {
    private val catalog = RagContractCatalog(JsonMapper.builder().build())

    @Test
    fun `canonical RAG catalog fixes two profiles and three policies`() {
        assertThat(catalog.profileIds)
            .containsExactly("bge_m3_local_1024_v1", "voyage_context_4_1024_v1")
        assertThat(catalog.policyIds)
            .containsExactly("bge_only_v1", "voyage_only_v1", "bge_then_voyage_on_sla_v1")
        assertThat(catalog.dimension).isEqualTo(1024)
        assertThat(catalog.profileIds).doesNotContain("voyage_context_3_1024_v1")
    }

    @Test
    fun `public ask catalog keeps profile policy provider and topK server owned`() {
        assertThat(catalog.askForbiddenBodyFields)
            .contains(
                "embeddingProfileId",
                "embeddingPolicyId",
                "profileId",
                "policyId",
                "provider",
                "model",
                "topK",
                "sourceTier",
            )
    }
}
