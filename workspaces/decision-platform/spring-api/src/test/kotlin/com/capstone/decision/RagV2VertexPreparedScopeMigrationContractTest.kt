package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagV2VertexPreparedScopeMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath by lazy(::resolveMigration)
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `Vertex prepared scope uses the dynamic next free Flyway version`() {
        val migrations = migrationFiles()
        val selectedVersion = migrationVersion(migrationPath)
        val previousHighest = migrations.filter { migrationVersion(it) < selectedVersion }.maxOf(::migrationVersion)

        assertThat(selectedVersion).isEqualTo(previousHighest + 1)
        assertThat(selectedVersion).isEqualTo(48)
    }

    @Test
    fun `prepared scope resumes only exact owner request topic scope without a raw question table`() {
        assertThat(migration).contains(
            "read_rag_v2_vertex_prepared_scope",
            "session_user <> 'decision_app'",
            "scope.allowed_topics = p_allowed_topics",
            "scope.expires_at > statement_timestamp()",
            "rag_v2_immutable_public_bundle_pointers",
            "rag_v2_immutable_owner_bundle_pointers",
            "SECURITY DEFINER",
            "REVOKE ALL PRIVILEGES ON TABLE public.rag_v2_retrieval_scope_claims FROM decision_app",
        )
        assertThat(migration).doesNotContain(
            "question text",
            "canonical_content",
            "raw_evidence",
            "GRANT SELECT ON TABLE public.rag_v2_retrieval_scope_claims TO decision_app",
        )
    }

    private fun resolveMigration(): Path {
        val candidates =
            migrationFiles().filter {
                it.fileName.toString().matches(Regex("""V[0-9]+__s4_7d_vertex_prepared_scope_resume\.sql"""))
            }
        check(candidates.size == 1) { "Expected one Vertex prepared-scope migration, found ${candidates.size}." }
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
