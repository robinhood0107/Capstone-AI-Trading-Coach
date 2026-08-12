package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagPreS5VoyageAccountingCapMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath = migrationDirectory.resolve("V58__pre_s5_voyage_document_batch_accounting_cap.sql")

    @Test
    fun `V58 is the next migration and aligns document packet accounting with the provider ceiling`() {
        val versions =
            Files.list(migrationDirectory).use { paths ->
                paths
                    .filter { it.fileName.toString().matches(Regex("V[0-9]+__.*\\.sql")) }
                    .map { migrationVersion(it) }
                    .toList()
            }
        assertThat(migrationVersion(migrationPath)).isEqualTo(58)
        assertThat(versions.count { it == 58 }).isEqualTo(1)
        assertThat(versions.max()).isEqualTo(58)

        val migration = Files.readString(migrationPath)
        assertThat(migration).contains(
            "CREATE OR REPLACE FUNCTION reserve_rag_v2_immutable_voyage_document_batch_usage",
            "p_token_cap NOT BETWEEN 1 AND 120000",
            "session_user <> 'decision_rag_writer'",
            "SECURITY DEFINER",
            "REVOKE ALL PRIVILEGES ON FUNCTION",
            "GRANT EXECUTE ON FUNCTION",
        )
        assertThat(migration).doesNotContain(
            "GRANT SELECT ON",
            "raw_provider_response",
            "credential",
            "api_key",
        )
    }

    private fun migrationVersion(path: Path): Int =
        Regex("^V([0-9]+)__")
            .find(path.fileName.toString())
            ?.groupValues
            ?.get(1)
            ?.toInt()
            ?: error("Flyway migration version is missing from ${path.fileName}.")
}
