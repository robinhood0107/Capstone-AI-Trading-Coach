package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagV2AuthorizedRetrievalMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath by lazy(::resolveMigration)
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `v2 authorized retrieval migration uses the next free Flyway version`() {
        val migrations = migrationFiles()
        val selectedVersion = migrationVersion(migrationPath)
        val previousHighest =
            migrations
                .filter { migrationVersion(it) < selectedVersion }
                .maxOf(::migrationVersion)

        assertThat(selectedVersion).isEqualTo(previousHighest + 1)
        assertThat(selectedVersion).isEqualTo(29)
    }

    @Test
    fun `v2 retrieval uses opaque short lived scope claims and bounded three channel functions`() {
        listOf(
            "CREATE TABLE rag_v2_retrieval_scope_claims",
            "CREATE FUNCTION issue_rag_v2_retrieval_scope",
            "CREATE FUNCTION read_rag_v2_retrieval_scope",
            "CREATE FUNCTION search_authorized_rag_v2_exact",
            "CREATE FUNCTION search_authorized_rag_v2_lexical",
            "CREATE FUNCTION search_authorized_rag_v2_dense",
        ).forEach { expected -> assertThat(migration).contains(expected) }
        assertThat(migration).contains("^rvs_[0-9a-f]{32}$")
        assertThat(migration).contains("interval '2 minutes'")
        assertThat(migration).contains("LIMIT 30")
        assertThat(migration).contains("vector(1024)")
        assertThat(migration).contains("retrieval_topics", "citation_title")
        assertThat(migration).contains("decision_rag_query", "SECURITY DEFINER")
    }

    @Test
    fun `v2 retrieval migration keeps raw paths and provider transports out of SQL`() {
        assertThat(migration).doesNotContain(
            "COPY PROGRAM",
            "COPY FROM '",
            "pg_read_file",
            "http_get",
            "http_post",
            "original_path",
            "absolute_path",
            "local_path",
            "file_path",
        )
    }

    private fun resolveMigration(): Path {
        val candidates =
            migrationFiles().filter {
                it.fileName.toString().matches(Regex("""V[0-9]+__s4_7d_rag_v2_authorized_retrieval\.sql"""))
            }
        check(candidates.size == 1) {
            "Expected one S4.7D authorized retrieval migration; found ${candidates.size}."
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
        )
}
