package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagV2EmptyOwnerGenerationScopeForwardRepairMigrationContractTest {
    private val migration =
        Files.readString(
            Path.of(
                "src/main/resources/db/migration/" +
                    "V63__pre_s5_empty_owner_generation_scope_forward_repair.sql",
            ),
        )

    @Test
    fun `V63 accepts only a zero-row active owner generation for an empty library`() {
        assertThat(migration).contains(
            "bundle.owner_private_generation_id IS NULL",
            "generation.component_generation_id = bundle.owner_private_generation_id",
            "generation.component_scope = 'OWNER_PRIVATE'",
            "generation.state = 'ACTIVE'",
            "generation.evaluation_status = 'PASSED'",
            "generation.expected_source_count = 0",
            "generation.actual_source_count = 0",
            "generation.expected_chunk_count = 0",
            "generation.actual_chunk_count = 0",
            "NOT EXISTS (",
            "public.rag_v2_immutable_source_revisions",
            "public.rag_v2_immutable_generation_memberships",
            "public.rag_v2_immutable_generation_embeddings",
        )
    }

    @Test
    fun `V63 preserves owner boundary and denies public execution`() {
        assertThat(migration).contains(
            "current_bundle.owner_user_id = p_owner_user_id",
            "source.owner_user_id = p_owner_user_id",
            "generation.owner_user_id = p_owner_user_id",
            "ALTER FUNCTION public.rag_v2_immutable_empty_owner_scope_is_current(text,bigint,text,text,text) OWNER TO flyway",
            "REVOKE ALL PRIVILEGES ON FUNCTION " +
                "public.rag_v2_immutable_empty_owner_scope_is_current(text,bigint,text,text,text) FROM PUBLIC",
        )
        assertThat(migration).doesNotContain(
            "GRANT EXECUTE ON FUNCTION public.rag_v2_immutable_empty_owner_scope_is_current",
        )
    }
}
