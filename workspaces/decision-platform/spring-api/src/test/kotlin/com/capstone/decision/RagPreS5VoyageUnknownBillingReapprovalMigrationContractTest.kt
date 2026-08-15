package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagPreS5VoyageUnknownBillingReapprovalMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath =
        migrationDirectory.resolve("V59__pre_s5_voyage_unknown_billing_reapproval.sql")

    @Test
    fun `V59 allows only a fresh approved packet after an unknown billing attempt`() {
        val versions =
            Files.list(migrationDirectory).use { paths ->
                paths
                    .filter { it.fileName.toString().matches(Regex("V[0-9]+__.*\\.sql")) }
                    .map { migrationVersion(it) }
                    .toList()
            }
        assertThat(migrationVersion(migrationPath)).isEqualTo(59)
        assertThat(versions.count { it == 59 }).isEqualTo(1)
        assertThat(versions.max()).isEqualTo(71)

        val migration = Files.readString(migrationPath)
        assertThat(migration).contains(
            "attempt_ordinal",
            "state = 'UNKNOWN_BILLING'",
            "state = 'CLAIMED'",
            "state = 'COMMITTED'",
            "packet_sha256 <> p_packet_sha256",
            "rag_v2_immutable_voyage_document_batch_attempt_active_unique",
            "rag_v2_immutable_voyage_document_batch_attempt_committed_unique",
            "pg_advisory_xact_lock",
            "SECURITY DEFINER",
            "session_user <> 'decision_rag_writer'",
            "REVOKE ALL PRIVILEGES ON FUNCTION",
            "GRANT EXECUTE ON FUNCTION",
        )
        assertThat(migration).doesNotContain(
            "DELETE FROM public.rag_v2_immutable_voyage_document_batch_attempts",
            "UPDATE public.rag_v2_immutable_voyage_document_batch_attempts SET packet_sha256",
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
