package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import java.util.HexFormat

class RagV2ImmutableBundleMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath by lazy(::resolveMigration)
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `S4_7D immutable bundle migration uses the dynamic next free Flyway version`() {
        val migrations = migrationFiles()
        val selectedVersion = migrationVersion(migrationPath)
        val previousHighest =
            migrations
                .filter { migrationVersion(it) < selectedVersion }
                .maxOf(::migrationVersion)

        assertThat(selectedVersion).isEqualTo(previousHighest + 1)
        assertThat(selectedVersion).isEqualTo(25)
    }

    @Test
    fun `V25 preserves the historical V24 runtime overlay bytes`() {
        val historicalMigration = migrationDirectory.resolve("V24__s4_7d_rag_v2_runtime_overlay.sql")

        assertThat(Files.exists(historicalMigration)).isTrue()
        assertThat(sha256(Files.readAllBytes(historicalMigration)))
            .isEqualTo("f9aee13b0de445f06e3f267304316d525f8f28b165fae10b8fc5a62780b02a3a")
    }

    @Test
    fun `V25 creates a separate immutable source chunk generation and bundle graph`() {
        listOf(
            "CREATE TABLE rag_v2_immutable_source_revisions",
            "CREATE TABLE rag_v2_immutable_chunks",
            "CREATE TABLE rag_v2_immutable_component_generations",
            "CREATE TABLE rag_v2_immutable_generation_memberships",
            "CREATE TABLE rag_v2_immutable_generation_embeddings",
            "CREATE TABLE rag_v2_immutable_embedding_cache",
            "CREATE TABLE rag_v2_immutable_materialization_runs",
            "CREATE TABLE rag_v2_immutable_source_receipts",
            "CREATE TABLE rag_v2_immutable_chunk_receipts",
            "CREATE TABLE rag_v2_immutable_embedding_receipts",
            "CREATE TABLE rag_v2_immutable_public_bundle_pointers",
            "CREATE TABLE rag_v2_immutable_bundles",
            "CREATE TABLE rag_v2_immutable_owner_bundle_pointers",
            "CREATE TABLE rag_v2_immutable_import_tickets",
            "CREATE TABLE rag_v2_immutable_activation_receipts",
            "CREATE TABLE rag_v2_immutable_deletion_receipts",
        ).forEach { table ->
            assertThat(migration).contains(table)
        }
        assertThat(migration).contains("'EXACT30'", "'OA112'", "'OWNER_PRIVATE'")
        assertThat(migration).contains("'bge_m3_local_1024_v1'", "'voyage_context_4_1024_v1'")
        assertThat(migration).contains("machine_fetch_allowed", "local_processing_allowed")
        assertThat(migration).contains("external_embedding_allowed", "external_generation_allowed")
        assertThat(migration).contains("canonical_text", "canonical_text_sha256", "context_set_hash")
        assertThat(migration).contains("gin_trgm_ops", "vector(1024)")
        assertThat(migration).contains("COUNT(DISTINCT membership.source_revision_id) <> 112")
        assertThat(migration).contains("reserve_source = false")
    }

    @Test
    fun `V25 keeps owner rows behind RLS and capability functions`() {
        listOf(
            "rag_v2_immutable_source_revisions",
            "rag_v2_immutable_chunks",
            "rag_v2_immutable_component_generations",
            "rag_v2_immutable_generation_memberships",
            "rag_v2_immutable_generation_embeddings",
            "rag_v2_immutable_embedding_cache",
            "rag_v2_immutable_materialization_runs",
            "rag_v2_immutable_bundles",
            "rag_v2_immutable_owner_bundle_pointers",
            "rag_v2_immutable_import_tickets",
            "rag_v2_immutable_deletion_receipts",
        ).forEach { table ->
            assertThat(migration).contains("ALTER TABLE $table ENABLE ROW LEVEL SECURITY")
            assertThat(migration).contains("ALTER TABLE $table FORCE ROW LEVEL SECURITY")
        }
        assertThat(migration).contains("owner_user_id = current_setting('app.actor_user_id', true)")
        listOf(
            "record_rag_v2_immutable_consent",
            "issue_rag_v2_immutable_import_ticket",
            "consume_rag_v2_immutable_import_ticket",
            "activate_rag_v2_immutable_public_base",
            "activate_rag_v2_immutable_owner_bundle",
            "delete_rag_v2_immutable_owner_document",
        ).forEach { function ->
            assertThat(migration).contains("CREATE FUNCTION $function")
            assertThat(migration).contains("ON FUNCTION $function")
        }
        assertThat(migration).contains("SECURITY DEFINER")
        assertThat(migration).contains("SET search_path = pg_catalog, public, pg_temp")
        assertThat(migration).contains(
            "REVOKE ALL PRIVILEGES ON FUNCTION delete_owner_rag_v2_document(text, text, text, text) FROM decision_app",
        )
    }

    @Test
    fun `V25 binds tickets to one owner policy operation and five minute single use`() {
        assertThat(migration).contains("ticket_hash", "operation", "policy_version", "consumed_at")
        assertThat(migration).contains("expires_at = issued_at + interval '5 minutes'")
        assertThat(migration).contains("consumed_at IS NULL")
        assertThat(migration).contains("pg_advisory_xact_lock")
        assertThat(migration).contains("session_user <> 'decision_app'")
        assertThat(migration).contains("session_user <> 'decision_rag_writer'")
    }

    @Test
    fun `V25 activates a replacement before owner IR text chunk and vector deletion`() {
        val activation = migration.indexOf("UPDATE public.rag_v2_immutable_owner_bundle_pointers")
        val deleteMembership = migration.indexOf("DELETE FROM public.rag_v2_immutable_generation_memberships")
        val deleteEmbedding = migration.indexOf("DELETE FROM public.rag_v2_immutable_generation_embeddings")
        val deleteChunk = migration.indexOf("DELETE FROM public.rag_v2_immutable_chunks")
        val deleteSource = migration.indexOf("DELETE FROM public.rag_v2_immutable_source_revisions")

        assertThat(activation).isGreaterThanOrEqualTo(0)
        assertThat(deleteMembership).isGreaterThan(activation)
        assertThat(deleteEmbedding).isGreaterThan(activation)
        assertThat(deleteChunk).isGreaterThan(activation)
        assertThat(deleteSource).isGreaterThan(activation)
        assertThat(migration).contains("'rgr_'", "'rti_'", "'rgr_del_'")
    }

    @Test
    fun `V25 migration has no filesystem raw artifact or provider transport sink`() {
        assertThat(migration).doesNotContain(
            "COPY PROGRAM",
            "COPY FROM '",
            "original_path",
            "absolute_path",
            "local_path",
            "file_path",
            "http://",
            "https://",
        )
    }

    private fun resolveMigration(): Path {
        val candidates =
            migrationFiles().filter {
                it.fileName.toString().matches(Regex("""V[0-9]+__s4_7d_rag_v2_immutable_bundle\.sql"""))
            }
        check(candidates.size == 1) {
            "Expected one S4.7D immutable RAG v2 bundle migration; found ${candidates.size}."
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

    private fun sha256(bytes: ByteArray): String =
        HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes))
}
