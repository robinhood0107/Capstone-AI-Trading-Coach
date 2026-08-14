package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagV2OwnerDualProfileMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath by lazy(::resolveMigration)
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `owner dual profile repair uses the dynamic next free Flyway version`() {
        val migrations = migrationFiles()
        val selectedVersion = migrationVersion(migrationPath)
        val previousHighest = migrations.filter { migrationVersion(it) < selectedVersion }.maxOf(::migrationVersion)

        assertThat(selectedVersion).isEqualTo(previousHighest + 1)
        assertThat(selectedVersion).isEqualTo(60)
    }

    @Test
    fun `owner ticket and bundle bind public and owner profiles without direct table grants`() {
        assertThat(migration).contains(
            "owner_embedding_profile_id",
            "RAG_V2_OWNER_DOCUMENT_V2",
            "issue_rag_v2_immutable_import_ticket_v2",
            "consume_rag_v2_immutable_import_ticket_v2",
            "stage_rag_v2_immutable_owner_document_v3",
            "reserve_rag_v2_owner_voyage_import",
            "complete_rag_v2_owner_voyage_import",
            "prepare_rag_v2_immutable_owner_overlay",
            "search_authorized_rag_v2_dense_v2",
            "read_rag_v2_retrieval_scope_v2",
            "read_rag_v2_retrieval_scope_by_claim_v2",
            "read_rag_v2_vertex_prepared_scope_v2",
            "SECURITY DEFINER",
            "REVOKE ALL PRIVILEGES ON TABLE rag_v2_immutable_import_tickets",
            "REVOKE ALL PRIVILEGES ON TABLE rag_v2_retrieval_scope_claims",
        )
        assertThat(migration).contains(
            "bge_m3_local_1024_v1",
            "voyage_context_4_1024_v1",
            "OWNER_VOYAGE_IMPORT_TOO_LARGE",
            "UNKNOWN_BILLING",
        )
        assertThat(migration).doesNotContain(
            "voyage_context_3_1024_v1",
            "GRANT SELECT ON TABLE rag_v2_immutable_import_tickets",
            "GRANT SELECT ON TABLE rag_v2_retrieval_scope_claims",
            "owner BGE evidence is retrieval only",
        )
    }

    @Test
    fun `dense merge ranks inside each profile before owner priority and stable ids`() {
        assertThat(migration).contains(
            "rank() OVER (",
            "PARTITION BY embedding_profile_id",
            "ORDER BY dense_distance",
            "ORDER BY profile_rank, (source_scope = 'OWNER_PRIVATE') DESC,",
            "source_id COLLATE \"C\", chunk_id COLLATE \"C\"",
        )
        assertThat(migration).doesNotContain(
            "row_number() OVER (\n        PARTITION BY embedding_profile_id",
        )
    }

    @Test
    fun `S4 8 direct lanes accept available and incomplete terminal receipts`() {
        assertThat(migration).contains(
            "CREATE OR REPLACE FUNCTION s48_runtime_state_is_safe",
            "COMPLETE_DIRECT_PROBE_SET_AVAILABLE",
            "DIRECT_PROBE_RECEIPT_SET_INCOMPLETE",
        )
    }

    private fun resolveMigration(): Path {
        val candidates =
            migrationFiles().filter {
                it.fileName.toString().matches(Regex("""V[0-9]+__pre_s5_owner_dual_profile_and_s48_alignment\.sql"""))
            }
        check(candidates.size == 1) { "Expected one Pre-S5 owner dual-profile migration, found ${candidates.size}." }
        return candidates.single()
    }

    private fun migrationFiles(): List<Path> =
        Files.list(migrationDirectory).use { paths ->
            paths
                .filter { it.fileName.toString().matches(Regex("""V[0-9]+__.+\.sql""")) }
                .sorted()
                .toList()
        }

    private fun migrationVersion(path: Path): Int =
        requireNotNull(
            Regex("""^V([0-9]+)__""")
                .find(path.fileName.toString())
                ?.groupValues
                ?.get(1)
                ?.toIntOrNull(),
        ) { "Flyway migration version is missing from ${path.fileName}." }
}
