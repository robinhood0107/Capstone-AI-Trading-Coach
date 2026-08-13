package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagV2BaseOnlyOwnerScopeForwardRepairMigrationContractTest {
    private val migration =
        Files.readString(
            Path.of(
                "src/main/resources/db/migration/" +
                    "V62__pre_s5_base_only_owner_scope_vertex_forward_repair.sql",
            ),
        )

    @Test
    fun `V62 permits only current READY base-only overlay for an empty owner scope`() {
        assertThat(migration).contains(
            "rag_v2_immutable_empty_owner_scope_is_current",
            "pointer.state = 'READY'",
            "bundle.owner_private_generation_id IS NULL",
            "bundle.owner_embedding_profile_id IS NULL",
            "bundle.exact30_generation_id = p_exact30_generation_id",
            "bundle.oa112_generation_id = p_oa112_generation_id",
            "bundle.embedding_profile_id = p_embedding_profile_id",
            "pointer.bundle_version = p_owner_pointer_version",
        )
    }

    @Test
    fun `V62 repairs both retrieval and Vertex claim-time guards without public execute`() {
        assertThat(migration).contains(
            "canonicalize_rag_v2_immutable_retrieval_citations(text,text,text,jsonb)",
            "assert_rag_v2_immutable_vertex_reservation_is_current(" +
                "public.rag_v2_immutable_vertex_usage_reservations)",
            "REVOKE ALL PRIVILEGES ON FUNCTION " +
                "public.rag_v2_immutable_empty_owner_scope_is_current(text,bigint,text,text,text) FROM PUBLIC",
        )
        assertThat(migration).doesNotContain("GRANT EXECUTE ON FUNCTION public.rag_v2_immutable_empty_owner_scope_is_current")
    }
}
