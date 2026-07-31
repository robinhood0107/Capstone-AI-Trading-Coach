package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagFullGenerationActivationMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath by lazy(::resolveMigration)
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `S4_2B migration uses the dynamic next free Flyway version`() {
        val migrations = migrationFiles()
        val selectedVersion = migrationVersion(migrationPath)
        val previousHighest =
            migrations
                .filter { migrationVersion(it) < selectedVersion }
                .maxOf(::migrationVersion)

        assertThat(selectedVersion).isEqualTo(previousHighest + 1)
        assertThat(selectedVersion).isEqualTo(migrations.maxOf(::migrationVersion))
    }

    @Test
    fun `admin has bounded verification and CAS activation without table DML`() {
        assertThat(migration).contains("CREATE TABLE rag_generation_attestations")
        assertThat(migration).contains("read_rag_generation_embeddings_for_verification")
        assertThat(migration).contains("read_rag_activation_state")
        assertThat(migration).contains("activate_verified_rag_generation")
        assertThat(migration).contains("session_user <> 'decision_rag_admin'")
        assertThat(migration).contains("SECURITY DEFINER")
        assertThat(migration).contains("SET search_path = pg_catalog, public")
        assertThat(migration).contains(
            "GRANT EXECUTE ON FUNCTION activate_verified_rag_generation",
        )
        assertThat(migration).contains("TO decision_rag_admin")
        assertThat(migration).doesNotContain(
            "GRANT UPDATE ON TABLE rag_embedding_policy_state TO decision_rag_admin",
        )
        assertThat(migration).doesNotContain(
            "GRANT UPDATE ON TABLE rag_corpus_generations TO decision_rag_admin",
        )
    }

    @Test
    fun `activation checks identity counts hashes vectors benchmark and pointer CAS`() {
        listOf(
            "p_expected_current_generation_id",
            "p_expected_policy_version",
            "p_expected_corpus_hash",
            "p_generation_hash",
            "p_membership_hash",
            "p_aggregate_row_hash",
            "p_db_vector_hash",
            "p_expected_source_revision_count",
            "p_expected_chunk_count",
            "p_batch_size",
            "p_model_revision",
            "p_model_file_manifest_hash",
            "p_tokenizer_sha256",
            "p_parser_version",
            "p_chunker_version",
            "p_input_strategy_version",
            "p_batch_benchmark_sha256",
            "p_environment_fingerprint_sha256",
            "p_benchmark_report_sha256",
            "p_warm_p95_ms",
            "p_approved_by_audit_ref",
        ).forEach { argument ->
            assertThat(migration).contains(argument)
        }
        assertThat(migration).contains("vector_dims")
        assertThat(migration).contains("vector_norm")
        assertThat(migration).contains("FOR UPDATE")
        assertThat(migration).contains("IS DISTINCT FROM")
        assertThat(migration).contains("'EVAL_PASSED'")
        assertThat(migration).contains("'ACTIVE'")
        assertThat(migration).contains("'DISABLED'")
        assertThat(migration).contains("rag_embedding_policy_transitions")
    }

    @Test
    fun `final embeddings retain per row receipt and writer cannot activate or supersede`() {
        assertThat(migration).contains("ADD COLUMN materialization_row_hash")
        assertThat(migration).contains("staging.staging_row_hash")
        assertThat(migration).contains("aggregate_row_hash")
        assertThat(migration).contains("generation_vector_hash")
        assertThat(migration).contains("session_user = 'decision_rag_writer'")
        assertThat(migration).contains("OLD.status = 'ACTIVE' AND NEW.status = 'DISABLED'")
        assertThat(migration).contains(
            "REVOKE ALL PRIVILEGES ON TABLE rag_generation_attestations FROM decision_rag_writer",
        )
    }

    private fun resolveMigration(): Path {
        val candidates =
            migrationFiles().filter {
                it.fileName.toString().matches(
                    Regex("""V[0-9]+__s4_2b_rag_full_generation_activation\.sql"""),
                )
            }
        check(candidates.size == 1) {
            "Expected one S4.2B migration; found ${candidates.size}."
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
        )
}
