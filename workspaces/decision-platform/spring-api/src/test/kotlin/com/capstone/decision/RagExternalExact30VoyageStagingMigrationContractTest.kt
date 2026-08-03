package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

// S4.7C external-safe exact-30만 Voyage vector space로 stage하며 S4.7B/V36 graph는 변경하지 않는다.
class RagExternalExact30VoyageStagingMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath by lazy(::resolveMigration)
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `external exact30 Voyage staging keeps dynamic next free Flyway version`() {
        val migrations = migrationFiles()
        val selectedVersion = migrationVersion(migrationPath)
        val previousHighest =
            migrations
                .filter { migrationVersion(it) < selectedVersion }
                .maxOf(::migrationVersion)

        assertThat(selectedVersion).isEqualTo(previousHighest + 1)
        assertThat(selectedVersion).isEqualTo(37)
    }

    @Test
    fun `Voyage staging uses a new writer capability and never changes the BGE writer`() {
        assertThat(migration).contains(
            "CREATE FUNCTION stage_rag_v2_immutable_external_exact30_voyage_document(p_payload jsonb)",
            "SECURITY DEFINER",
            "SET search_path = pg_catalog, public, pg_temp",
            "current_user <> 'flyway'",
            "session_user <> 'decision_rag_writer'",
            "REVOKE ALL PRIVILEGES ON FUNCTION stage_rag_v2_immutable_external_exact30_voyage_document(jsonb) FROM PUBLIC",
            "GRANT EXECUTE ON FUNCTION stage_rag_v2_immutable_external_exact30_voyage_document(jsonb)",
        )
        assertThat(migration).doesNotContain(
            "GRANT INSERT ON TABLE rag_v2_immutable_source_revisions TO decision_rag_writer",
            "GRANT INSERT ON TABLE rag_v2_immutable_generation_embeddings TO decision_rag_writer",
            "COPY PROGRAM",
            "COPY FROM '",
        )
    }

    @Test
    fun `Voyage staging locks the external card allowlist and contextual embedding invariants`() {
        assertThat(migration).contains(
            "CREATE TABLE rag_v2_immutable_external_exact30_source_allowlist",
            "CREATE TABLE rag_v2_immutable_external_exact30_voyage_component_manifests",
            "rag_v2_immutable_external_exact30_voyage_source_is_approved(",
            "voyage_context_4_1024_v1",
            "payload_scope <> 'EXACT30'",
            "payload_expected_source_count <> 30",
            "payload_external_embedding_allowed",
            "payload_external_generation_allowed",
            "payload_external_processing_eligible",
            "contextSetHash",
            "jsonb_array_length(payload_embedding -> 'embedding') <> 1024",
            "rag-v2-immutable-external-exact30-voyage-source|",
            "raw artifact=0",
        )
    }

    private fun resolveMigration(): Path {
        val candidates =
            migrationFiles().filter {
                it.fileName.toString().matches(Regex("""V[0-9]+__s4_7d_external_exact30_voyage_staging_writer\.sql"""))
            }
        check(candidates.size == 1) {
            "Expected one external exact30 Voyage staging migration, found ${candidates.size}."
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
