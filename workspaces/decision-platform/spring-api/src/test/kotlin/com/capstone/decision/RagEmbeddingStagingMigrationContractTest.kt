package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagEmbeddingStagingMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath by lazy(::resolveEmbeddingStagingMigration)
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `S4_2A embedding staging migration preserves its original next-free Flyway version`() {
        val migrations = migrationFiles()
        val selectedVersion = migrationVersion(migrationPath)
        val previousHighest =
            migrations
                .filter { migrationVersion(it) < selectedVersion }
                .maxOf(::migrationVersion)

        assertThat(selectedVersion).isEqualTo(previousHighest + 1)
        assertThat(selectedVersion).isEqualTo(17)
    }

    @Test
    fun `writer can stage but cannot write final embeddings or activate generations`() {
        assertThat(migration).contains("CREATE TABLE rag_embedding_staging")
        assertThat(migration).contains(
            "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE rag_chunk_embeddings " +
                "FROM decision_rag_writer",
        )
        assertThat(migration).contains(
            "GRANT INSERT, SELECT ON TABLE rag_embedding_staging TO decision_rag_writer",
        )
        assertThat(migration).doesNotContain(
            "GRANT INSERT ON TABLE rag_chunk_embeddings TO decision_rag_writer",
        )
        assertThat(migration).doesNotContain(
            "GRANT UPDATE ON TABLE rag_embedding_policy_state TO decision_rag_writer",
        )
        assertThat(migration).doesNotContain("COPY PROGRAM", "COPY FROM '")
    }

    @Test
    fun `bounded finalize validates ownership membership cardinality hashes and vectors`() {
        listOf(
            "finalize_rag_embedding_staging(text, text, text, integer, text)",
            "purge_rag_embedding_staging(text, text)",
        ).forEach { signature ->
            assertThat(migration).contains(
                "REVOKE ALL PRIVILEGES ON FUNCTION $signature FROM PUBLIC",
            )
        }
        assertThat(migration).contains("SECURITY DEFINER")
        assertThat(migration).contains("SET search_path = pg_catalog, public")
        assertThat(migration).contains("current_user")
        assertThat(migration).contains("corpus_generation_id")
        assertThat(migration).contains("materialization_run_id")
        assertThat(migration).contains("embedding_input_hash")
        assertThat(migration).contains("staging_row_hash")
        assertThat(migration).contains("expected_row_count")
        assertThat(migration).contains("vector_dims")
        assertThat(migration).contains("vector_norm")
        assertThat(migration).contains("ON CONFLICT")
        assertThat(migration).contains("RAG embedding staging finalize")
    }

    @Test
    fun `staging rows are bounded immutable and purge is run scoped`() {
        listOf(
            "generation_id",
            "materialization_run_id",
            "chunk_revision_id",
            "embedding_profile_id",
            "embedding_input_hash",
            "embedding",
            "staging_row_hash",
            "writer_role",
        ).forEach { column ->
            assertThat(migration).contains(column)
        }
        assertThat(migration).contains("CHECK (vector_dims(embedding) = 1024)")
        assertThat(migration).contains("CHECK (abs(vector_norm(embedding)")
        assertThat(migration).contains("CHECK (octet_length(materialization_run_id)")
        assertThat(migration).contains("PRIMARY KEY (generation_id, materialization_run_id, chunk_revision_id)")
        assertThat(migration).doesNotContain(
            "GRANT DELETE ON TABLE rag_embedding_staging TO decision_rag_writer",
        )
        assertThat(migration).contains(
            "GRANT EXECUTE ON FUNCTION purge_rag_embedding_staging(text, text) " +
                "TO decision_rag_writer",
        )
    }

    private fun resolveEmbeddingStagingMigration(): Path {
        val candidates =
            migrationFiles().filter {
                it.fileName.toString().matches(
                    Regex("""V[0-9]+__s4_2a_rag_embedding_staging\.sql"""),
                )
            }
        check(candidates.size == 1) {
            "Expected exactly one S4.2A embedding staging migration; found ${candidates.size}."
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
        ) {
            "Flyway migration version is missing from ${path.fileName}."
        }
}
