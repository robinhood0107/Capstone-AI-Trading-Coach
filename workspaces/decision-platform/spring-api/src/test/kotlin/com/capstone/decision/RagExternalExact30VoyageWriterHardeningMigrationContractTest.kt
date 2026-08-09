package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

// V41은 이미 적용된 V37 writer의 checksum을 바꾸지 않고 exact-30 provenance/insertion guard를 보강한다.
class RagExternalExact30VoyageWriterHardeningMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath by lazy(::resolveMigration)
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `external exact30 Voyage hardening uses the next free Flyway version`() {
        val migrations = migrationFiles()
        val selectedVersion = migrationVersion(migrationPath)
        val previousHighest =
            migrations
                .filter { migrationVersion(it) < selectedVersion }
                .maxOf(::migrationVersion)

        assertThat(selectedVersion).isEqualTo(previousHighest + 1)
        assertThat(selectedVersion).isEqualTo(41)
    }

    @Test
    fun `hardening binds canonical text and closes direct writer source set and ordering bypasses`() {
        assertThat(migration).contains(
            "ADD COLUMN IF NOT EXISTS canonical_text_sha256 text",
            "canonical_text_sha256 = p_canonical_text_sha256",
            "guard_rag_v2_immutable_external_exact30_voyage_source_identity",
            "guard_rag_v2_immutable_external_exact30_voyage_membership",
            "duplicate source is invalid",
            "canonical source order is invalid",
            "observed_source_ids IS DISTINCT FROM expected_source_ids",
            "observed_source_total <> 30",
            "rag_v2_immutable_external_exact30_voyage_source_revision_id(",
            "rag_v2_immutable_external_exact30_voyage_document_id(",
            "REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_v2_immutable_external_exact30_voyage_source_identity() FROM PUBLIC",
            "REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_v2_immutable_external_exact30_voyage_membership() FROM PUBLIC",
        )
        assertThat(migration).doesNotContain(
            "GRANT INSERT ON TABLE rag_v2_immutable_source_revisions TO decision_rag_writer",
            "COPY PROGRAM",
            "COPY FROM '",
        )
    }

    private fun resolveMigration(): Path {
        val candidates =
            migrationFiles().filter {
                it.fileName.toString() == "V41__s4_7d_external_exact30_voyage_writer_hardening.sql"
            }
        check(candidates.size == 1) {
            "Expected one external exact-30 Voyage hardening migration, found ${candidates.size}."
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
