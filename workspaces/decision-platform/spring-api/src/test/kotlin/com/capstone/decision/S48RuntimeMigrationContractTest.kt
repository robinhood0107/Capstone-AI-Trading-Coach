package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class S48RuntimeMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath by lazy(::resolveMigration)
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `S4 8 runtime uses the dynamic next free Flyway version`() {
        val migrations = migrationFiles()
        val selectedVersion = migrationVersion(migrationPath)
        val previousHighest = migrations.filter { migrationVersion(it) < selectedVersion }.maxOf(::migrationVersion)

        assertThat(selectedVersion).isEqualTo(previousHighest + 1)
        assertThat(selectedVersion).isEqualTo(50)
    }

    @Test
    fun `S4 8 runtime allows only exact nine state-only lanes through definer capabilities`() {
        assertThat(migration).contains(
            "s48_runtime_sanitized_projections",
            "S48_CORE6_KIS",
            "S48_CORE6_OPENDART",
            "S48_CORE6_SEC_EDGAR",
            "S48_CORE6_KRX",
            "S48_CORE6_KOFIA",
            "S48_CORE6_ECOS",
            "S48_OPTIONAL3_FINNHUB",
            "S48_OPTIONAL3_TWELVE_DATA",
            "S48_OPTIONAL3_MASSIVE",
            "append_s48_runtime_sanitized_projection",
            "read_latest_s48_runtime_sanitized_projection",
            "session_user <> 'decision_market_writer'",
            "session_user <> 'decision_app'",
            "SECURITY DEFINER",
            "REVOKE ALL PRIVILEGES ON TABLE s48_runtime_sanitized_projections",
        )
        assertThat(migration).doesNotContain(
            "raw_response",
            "provider_response",
            "credential text",
            "GRANT SELECT ON TABLE s48_runtime_sanitized_projections TO decision_app",
            "GRANT INSERT ON TABLE s48_runtime_sanitized_projections TO decision_market_writer",
            "GDELT",
        )
    }

    private fun resolveMigration(): Path {
        val candidates =
            migrationFiles().filter {
                it.fileName.toString().matches(Regex("""V[0-9]+__s4_8_runtime_sanitized_projection\.sql"""))
            }
        check(candidates.size == 1) { "Expected one S4.8 runtime migration, found ${candidates.size}." }
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
