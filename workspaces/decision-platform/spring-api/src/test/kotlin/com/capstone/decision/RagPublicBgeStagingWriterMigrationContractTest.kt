package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

// V36은 public corpus의 transient text/vector를 writer capability 하나로만 immutable graph에 넣는다.
class RagPublicBgeStagingWriterMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath by lazy(::resolveMigration)
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `public BGE staging keeps dynamic next free Flyway version`() {
        val migrations = migrationFiles()
        val selectedVersion = migrationVersion(migrationPath)
        val previousHighest =
            migrations
                .filter { migrationVersion(it) < selectedVersion }
                .maxOf(::migrationVersion)

        assertThat(selectedVersion).isEqualTo(previousHighest + 1)
        assertThat(selectedVersion).isEqualTo(36)
    }

    @Test
    fun `public BGE writer remains the only direct-table-free staging and evaluation capability`() {
        assertThat(migration).contains(
            "CREATE FUNCTION stage_rag_v2_immutable_public_bge_document(p_payload jsonb)",
            "CREATE FUNCTION evaluate_rag_v2_immutable_public_bge_component(",
            "SECURITY DEFINER",
            "SET search_path = pg_catalog, public, pg_temp",
            "current_user <> 'flyway'",
            "session_user <> 'decision_rag_writer'",
            "REVOKE ALL PRIVILEGES ON FUNCTION stage_rag_v2_immutable_public_bge_document(jsonb) FROM PUBLIC",
            "REVOKE ALL PRIVILEGES ON FUNCTION evaluate_rag_v2_immutable_public_bge_component(text, jsonb) FROM PUBLIC",
            "GRANT EXECUTE ON FUNCTION stage_rag_v2_immutable_public_bge_document(jsonb)",
            "GRANT EXECUTE ON FUNCTION evaluate_rag_v2_immutable_public_bge_component(text, jsonb)",
        )
        assertThat(migration).doesNotContain(
            "GRANT INSERT ON TABLE rag_v2_immutable_source_revisions TO decision_rag_writer",
            "GRANT INSERT ON TABLE rag_v2_immutable_public_component_evaluations TO decision_rag_writer",
            "COPY PROGRAM",
            "COPY FROM '",
        )
    }

    @Test
    fun `staging locks exact public corpus rights vector and evaluation gates`() {
        assertThat(migration).contains(
            "payload_expected_source_count <> (CASE payload_scope WHEN 'EXACT30' THEN 30 ELSE 112 END)",
            "payload_expected_chunk_count < payload_expected_source_count",
            "(p_payload -> 'expectedSourceCount')::text !~ '^(0|[1-9][0-9]*)$'",
            "(payload_chunk -> 'chunkOrdinal')::text !~ '^(0|[1-9][0-9]*)$'",
            "field.value::text !~ '^(0|[1-9][0-9]*)$'",
            "memberDigests",
            "payload_run_id <> 'rgr_run_'",
            "rag-v2-immutable-public-bge-source|",
            "jsonb_array_length(payload_embedding -> 'embedding') <> 1024",
            "payload_scope = 'OA112'",
            "NOT payload_machine_fetch_allowed",
            "NOT payload_local_processing_allowed",
            "NOT payload_external_embedding_allowed",
            "NOT payload_external_generation_allowed",
            "providerPhysicalCallCount",
            "submitted_provider_physical_call_count <> 0",
            "submitted_exact_top5_hit_rate <> 1.0",
            "submitted_track_recall_at5 < 0.80",
            "submitted_citation_coverage < 0.80",
            "submitted_direct_advice_block_rate <> 1.0",
            "submitted_cross_owner_leak_count <> 0",
            "submitted_mixed_profile_row_count <> 0",
            "submitted_owner_delete_residual_row_count <> 0",
            "submitted_warm_p95_millis >= 8000",
        )
    }

    @Test
    fun `evaluation evidence has forced RLS with a flyway policy only`() {
        assertThat(migration).contains(
            "CREATE TABLE rag_v2_immutable_public_component_evaluations",
            "CREATE TABLE rag_v2_immutable_public_component_manifests",
            "CREATE TABLE rag_v2_immutable_exact30_source_allowlist",
            "ALTER TABLE rag_v2_immutable_public_component_evaluations ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE rag_v2_immutable_public_component_evaluations FORCE ROW LEVEL SECURITY",
            "CREATE POLICY rag_v2_immutable_public_component_evaluations_flyway_write",
            "TO flyway",
        )
    }

    @Test
    fun `exact30 frozen identity and persisted component hash are rechecked before transition`() {
        assertThat(migration).contains(
            "source_card_sha256 text NOT NULL",
            "sourceCardSha256",
            "rag_v2_immutable_exact30_source_is_approved(",
            "allowed.canonical_https_url = p_canonical_https_url",
            "allowed.raw_content_sha256 = p_raw_content_sha256",
            "allowed.source_card_sha256 = p_source_card_sha256",
            "rag_v2_immutable_public_bge_component_hashes_are_valid(",
            "guard_rag_v2_immutable_public_bge_component_hash_transition",
            "BEFORE UPDATE OF state",
        )
        assertThat(migration).doesNotContain(
            "rag_v2_immutable_canonical_json_text",
            "rag_v2_immutable_canonical_json_sha256",
            "rag_source_card_verifications AS verification",
        )
    }

    private fun resolveMigration(): Path {
        val candidates =
            migrationFiles().filter {
                it.fileName.toString().matches(Regex("""V[0-9]+__s4_7d_public_bge_staging_writer\.sql"""))
            }
        check(candidates.size == 1) {
            "Expected one public BGE staging migration, found ${candidates.size}."
        }
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
