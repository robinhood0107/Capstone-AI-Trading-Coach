package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagPublicVoyageEvaluationUsageLinkMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath by lazy(::resolveMigration)
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `public Voyage evaluation ledger link uses the dynamic next free Flyway version`() {
        val migrations = migrationFiles()
        val selectedVersion = migrationVersion(migrationPath)
        val previousHighest = migrations.filter { migrationVersion(it) < selectedVersion }.maxOf(::migrationVersion)

        assertThat(selectedVersion).isEqualTo(previousHighest + 1)
        assertThat(selectedVersion).isEqualTo(47)
    }

    @Test
    fun `public Voyage evaluation must link exact component counts to committed distinct V46 query attempts`() {
        assertThat(migration).contains(
            "evaluation_scope_claim_sha256",
            "evaluate_rag_v2_immutable_public_voyage_component_v45_unlinked",
            "rag_v2_immutable_voyage_query_usage_reservations",
            "rag_v2_immutable_voyage_query_usage_attempts",
            "rag_v2_immutable_voyage_query_usage_outcomes",
            "evaluation_component_scope = generation_scope",
            "outcome.state = 'COMMITTED'",
            "COUNT(DISTINCT reservation.query_sha256)",
            "WHEN 'EXACT30' THEN 10",
            "WHEN 'OA112' THEN 112",
            "session_user <> 'decision_rag_writer'",
            "SECURITY DEFINER",
        )
        assertThat(migration).doesNotContain(
            "question text",
            "scope claim text",
            "raw_response",
            "raw_request",
            "provider_payload",
            "GRANT SELECT ON TABLE rag_v2_immutable_voyage_query_usage_reservations TO decision_rag_writer",
        )
    }

    private fun resolveMigration(): Path {
        val candidates =
            migrationFiles().filter {
                it.fileName.toString().matches(Regex("""V[0-9]+__s4_7d_public_voyage_evaluation_usage_link\.sql"""))
            }
        check(candidates.size == 1) { "Expected one public Voyage evaluation usage-link migration, found ${candidates.size}." }
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
