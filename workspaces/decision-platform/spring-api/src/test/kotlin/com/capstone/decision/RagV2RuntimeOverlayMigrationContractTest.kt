package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagV2RuntimeOverlayMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath by lazy(::resolveMigration)
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `S4_7D runtime overlay migration uses the dynamic next free Flyway version`() {
        val migrations = migrationFiles()
        val selectedVersion = migrationVersion(migrationPath)
        val previousHighest =
            migrations
                .filter { migrationVersion(it) < selectedVersion }
                .maxOf(::migrationVersion)

        assertThat(selectedVersion).isEqualTo(previousHighest + 1)
        assertThat(selectedVersion).isLessThanOrEqualTo(migrations.maxOf(::migrationVersion))
    }

    @Test
    fun `V24 creates immutable public owner private document history and deletion state`() {
        listOf(
            "CREATE TABLE rag_v2_public_corpus_state",
            "CREATE TABLE rag_v2_owner_private_generation_pointers",
            "CREATE TABLE rag_v2_owner_documents",
            "CREATE TABLE rag_v2_owner_document_chunks",
            "CREATE TABLE rag_v2_owner_document_embeddings",
            "CREATE TABLE rag_v2_document_deletion_receipts",
            "CREATE TABLE rag_v2_answer_history",
            "CREATE TABLE rag_v2_answer_citations",
        ).forEach { table ->
            assertThat(migration).contains(table)
        }
        assertThat(migration).contains(
            "'CORE_READY'",
            "'BUILDING'",
            "'FULL_READY'",
            "'FAILED'",
            "'ABSENT'",
            "'READY'",
            "'LOCAL_EPHEMERAL_PARSE'",
            "'PUBLIC_WEB'",
            "'LOCAL_DOCUMENT'",
        )
        assertThat(migration).doesNotContain("LICENSED_EPHEMERAL_LOCAL")
        assertThat(migration).doesNotContain("original_path", "local_path", "absolute_path", "file_path")
    }

    @Test
    fun `V24 exposes only owner scoped definer operations to decision app`() {
        listOf(
            "read_rag_v2_corpus_status",
            "read_rag_v2_history_metadata",
            "read_rag_v2_history_detail",
            "delete_owned_rag_v2_history",
            "delete_owner_rag_v2_document",
        ).forEach { function ->
            assertThat(migration).contains("CREATE FUNCTION $function")
            assertThat(migration).contains("ON FUNCTION $function")
        }
        assertThat(migration).contains("SECURITY DEFINER")
        assertThat(migration).contains("SET search_path = pg_catalog, public, pg_temp")
        assertThat(migration).contains("session_user <> 'decision_app'")
        assertThat(migration).contains("current_setting('app.actor_user_id', true)")
        assertThat(migration).contains("TO decision_app")
        listOf(
            "rag_v2_public_corpus_state",
            "rag_v2_owner_private_generation_pointers",
            "rag_v2_owner_documents",
            "rag_v2_owner_document_chunks",
            "rag_v2_owner_document_embeddings",
            "rag_v2_document_deletion_receipts",
            "rag_v2_answer_history",
            "rag_v2_answer_citations",
        ).forEach { table ->
            assertThat(migration).doesNotContain("GRANT SELECT ON TABLE $table TO decision_app")
            assertThat(migration).doesNotContain("GRANT INSERT ON TABLE $table TO decision_app")
            assertThat(migration).doesNotContain("GRANT UPDATE ON TABLE $table TO decision_app")
            assertThat(migration).doesNotContain("GRANT DELETE ON TABLE $table TO decision_app")
        }
    }

    @Test
    fun `V24 enables owner RLS and content free hard delete receipts`() {
        assertThat(migration).contains("ENABLE ROW LEVEL SECURITY")
        assertThat(migration).contains("FORCE ROW LEVEL SECURITY")
        assertThat(migration).contains("owner_user_id = current_setting('app.actor_user_id', true)")
        assertThat(migration).contains("DELETE FROM public.rag_v2_owner_document_embeddings")
        assertThat(migration).contains("DELETE FROM public.rag_v2_owner_document_chunks")
        assertThat(migration).contains("DELETE FROM public.rag_v2_owner_documents")
        assertThat(migration).contains("INSERT INTO public.rag_v2_document_deletion_receipts")
        assertThat(migration).contains("content_hash_was_removed")
        assertThat(migration).doesNotContain("normalized_text")
    }

    private fun resolveMigration(): Path {
        val candidates =
            migrationFiles().filter {
                it.fileName.toString().matches(
                    Regex("""V[0-9]+__s4_7d_rag_v2_runtime_overlay\.sql"""),
                )
            }
        check(candidates.size == 1) {
            "Expected one S4.7D RAG v2 runtime overlay migration; found ${candidates.size}."
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
