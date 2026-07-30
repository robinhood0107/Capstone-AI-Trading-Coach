package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagSourceRegistryMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath by lazy(::resolveNextFreeS4Migration)
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `S4 migration uses the current next-free Flyway version`() {
        val migrations = migrationFiles()
        val selectedVersion = migrationVersion(migrationPath)
        val previousHighest =
            migrations
                .filter { migrationVersion(it) < selectedVersion }
                .maxOf(::migrationVersion)

        assertThat(selectedVersion).isEqualTo(previousHighest + 1)
    }

    @Test
    fun `S4 migration fails before mutation unless every V2 RAG table is empty`() {
        assertThat(migration).contains("S4 normalized RAG precondition failed")
        val lockPosition = migration.indexOf("LOCK TABLE")
        val preconditionPosition = migration.indexOf("DO \$s4_v2_precondition\$")
        assertThat(lockPosition).isGreaterThanOrEqualTo(0)
        assertThat(lockPosition).isLessThan(preconditionPosition)
        listOf(
            "rag_sources",
            "rag_chunks",
            "rag_answers",
            "rag_citations",
            "rag_answer_feedback",
        ).forEach { legacyTable ->
            assertThat(migration.substring(lockPosition, preconditionPosition)).contains(legacyTable)
            assertThat(migration).contains("FROM $legacyTable")
        }
        assertThat(migration).contains("V2 legacy RAG tables must all be empty")
    }

    @Test
    fun `S4 migration tombstones V2 and creates the normalized immutable generation graph`() {
        listOf(
            "rag_sources_v2_legacy",
            "rag_chunks_v2_legacy",
            "rag_answers_v2_legacy",
            "rag_citations_v2_legacy",
            "rag_answer_feedback_v2_legacy",
        ).forEach { tombstone ->
            assertThat(migration).contains(tombstone)
        }
        listOf(
            "CREATE TABLE rag_sources",
            "CREATE TABLE rag_source_revisions",
            "CREATE TABLE rag_ingest_runs",
            "CREATE TABLE rag_chunk_revisions",
            "CREATE TABLE rag_corpus_generations",
            "CREATE TABLE rag_chunk_embeddings",
        ).forEach { normalizedTable ->
            assertThat(migration).contains(normalizedTable)
        }
    }

    @Test
    fun `S4 migration separates ingest and retrieval roles without broad worker DML`() {
        assertThat(migration).contains("decision_rag_writer")
        assertThat(migration).contains("decision_rag_query")
        assertThat(migration).doesNotContain("GRANT INSERT ON", "TO decision_worker")
        assertThat(migration).doesNotContain("GRANT UPDATE ON", "TO decision_worker")
        assertThat(migration).doesNotContain("GRANT DELETE ON", "TO decision_worker")
        assertThat(migration).contains("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC")
        listOf(
            "guard_rag_source_revision_locator()",
            "guard_rag_ingest_run_transition()",
            "guard_rag_chunk_scope()",
            "guard_rag_generation_materialization()",
            "guard_rag_generation_transition()",
            "guard_rag_generation_activation()",
            "retire_rag_source_for_relocation(text, text)",
            "read_rag_source_registry(text)",
            "read_active_rag_chunks(text, integer)",
        ).forEach { signature ->
            assertThat(migration).contains("REVOKE ALL PRIVILEGES ON FUNCTION $signature FROM PUBLIC")
        }
        assertThat(migration).contains(
            "GRANT EXECUTE\n      ON FUNCTION retire_rag_source_for_relocation(text, text)\n" +
                "      TO decision_rag_writer",
        )
    }

    @Test
    fun `S4 migration locks source identity revision ownership and embedding identity`() {
        assertThat(migration).contains("rag_sources_source_id_format_check")
        assertThat(migration).contains("^src_[a-z0-9]+_[a-z0-9_]+_[0-9]{3}$")
        assertThat(migration).contains("license_decision")
        assertThat(migration).contains("retention_owner")
        assertThat(migration).contains("canonical_url")
        assertThat(migration).contains("allowed_origin")
        assertThat(migration).contains("allowed_path")
        assertThat(migration).contains("chunk_revision_id")
        assertThat(migration).contains("embedding_profile_id")
        assertThat(migration).contains("embedding_input_hash")
        assertThat(migration).contains("context_set_hash")
        listOf(
            "REGISTERED",
            "PLANNED",
            "MATERIALIZING",
            "MATERIALIZED",
            "EVAL_PASSED",
            "ACTIVE",
            "FAILED_FINAL",
            "DISABLED",
        ).forEach { status ->
            assertThat(migration).contains("'$status'")
        }
        assertThat(migration).doesNotContain("'EVALUATING'", "'RETIRED'")
    }

    private fun resolveNextFreeS4Migration(): Path {
        val candidates =
            migrationFiles().filter {
                it.fileName.toString().matches(Regex("""V[0-9]+__s4_0_rag_normalized_registry\.sql"""))
            }
        check(candidates.size == 1) {
            "Expected exactly one next-free S4 normalized RAG migration; found ${candidates.size}."
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
        ) {
            "Flyway migration version is missing from ${path.fileName}."
        }
}
