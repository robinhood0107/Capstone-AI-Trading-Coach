package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

// V43은 direct pointer grant 없이 public BGE pair의 same-transaction CAS input만 admin에 제공한다.
class RagPublicBgeActivationPrepareMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath by lazy(::resolveMigration)
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `public BGE activation prepare uses the next free Flyway version`() {
        val migrations = migrationFiles()
        val selectedVersion = migrationVersion(migrationPath)
        val previousHighest =
            migrations
                .filter { migrationVersion(it) < selectedVersion }
                .maxOf(::migrationVersion)

        assertThat(selectedVersion).isEqualTo(previousHighest + 1)
        assertThat(selectedVersion).isEqualTo(43)
    }

    @Test
    fun `prepare holds the existing activation lock and exposes only a CAS version plus idempotence flag`() {
        assertThat(migration).contains(
            "CREATE FUNCTION prepare_rag_v2_immutable_public_base_activation(",
            "RETURNS TABLE (",
            "expected_pointer_version bigint",
            "activation_required boolean",
            "SECURITY DEFINER",
            "SET search_path = pg_catalog, public, pg_temp",
            "current_user <> 'flyway'",
            "session_user <> 'decision_rag_admin'",
            "set_config('app.rag_admin_maintenance', 'public_base_activation', true)",
            "rag-v2-immutable-bundle-activation",
            "FOR UPDATE",
            "pointer_record.state = 'ACTIVE'",
        )
        assertThat(migration).doesNotContain(
            "p_expected_pointer_version",
            "GRANT SELECT ON TABLE rag_v2_immutable_public_bundle_pointers TO decision_rag_admin",
            "GRANT UPDATE ON TABLE rag_v2_immutable_public_bundle_pointers TO decision_rag_admin",
            "raw_content",
            "canonical_text",
        )
    }

    @Test
    fun `only the existing rag admin capability receives execute`() {
        assertThat(migration).contains(
            "GRANT EXECUTE ON FUNCTION prepare_rag_v2_immutable_public_base_activation(text, text)",
            "TO decision_rag_admin",
            "REVOKE ALL PRIVILEGES ON FUNCTION prepare_rag_v2_immutable_public_base_activation(text, text) FROM PUBLIC",
        )
        assertThat(migration).doesNotContain(
            "TO decision_rag_writer",
            "TO decision_rag_query",
            "TO decision_app",
        )
    }

    private fun resolveMigration(): Path {
        val candidates =
            migrationFiles().filter {
                it.fileName.toString() == "V43__s4_7d_public_bge_activation_prepare.sql"
            }
        check(candidates.size == 1) {
            "Expected one public BGE activation prepare migration, found ${candidates.size}."
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
