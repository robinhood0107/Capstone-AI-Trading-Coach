package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagPreS5VoyageQueryUsageLedgerMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath by lazy(::resolveMigration)
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `Voyage query usage ledger uses the dynamic next free Flyway version`() {
        val migrations = migrationFiles()
        val selectedVersion = migrationVersion(migrationPath)
        val previousHighest = migrations.filter { migrationVersion(it) < selectedVersion }.maxOf(::migrationVersion)

        assertThat(selectedVersion).isEqualTo(previousHighest + 1)
        assertThat(selectedVersion).isEqualTo(46)
    }

    @Test
    fun `query ledger binds hashes only and keeps writer table privileges closed`() {
        assertThat(migration).contains(
            "CREATE TABLE rag_v2_immutable_voyage_query_usage_reservations",
            "CREATE TABLE rag_v2_immutable_voyage_query_usage_attempts",
            "CREATE TABLE rag_v2_immutable_voyage_query_usage_outcomes",
            "query_sha256",
            "scope_claim_sha256",
            "UNIQUE (packet_sha256)",
            "UNIQUE (nonce_sha256)",
            "CONTEXTUALIZED_QUERY_EMBEDDING",
            "reserve_rag_v2_immutable_voyage_query_usage",
            "claim_rag_v2_immutable_voyage_query_usage_attempt",
            "commit_rag_v2_immutable_voyage_query_usage",
            "mark_rag_v2_immutable_voyage_query_usage_unknown_billing",
            "session_user <> 'decision_rag_writer'",
            "SECURITY DEFINER",
            "REVOKE ALL PRIVILEGES ON FUNCTION",
        )
        assertThat(migration).doesNotContain(
            "question text",
            "scope_claim text",
            "raw_response",
            "raw_request",
            "provider_payload",
            "GRANT INSERT ON TABLE rag_v2_immutable_voyage_query_usage_reservations TO decision_rag_writer",
        )
    }

    private fun resolveMigration(): Path {
        val candidates =
            migrationFiles().filter {
                it.fileName.toString().matches(Regex("""V[0-9]+__s4_7d_voyage_query_usage_ledger\.sql"""))
            }
        check(candidates.size == 1) { "Expected one Voyage query usage migration, found ${candidates.size}." }
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
