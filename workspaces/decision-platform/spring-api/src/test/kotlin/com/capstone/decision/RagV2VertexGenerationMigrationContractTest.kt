package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagV2VertexGenerationMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val scopeLedgerPath by lazy { resolveMigration("s4_7d_vertex_generation_scope_ledger") }
    private val hardeningPath by lazy { resolveMigration("s4_7d_vertex_outbound_authorization_hardening") }
    private val claimRecheckPath by lazy { resolveMigration("s4_7d_vertex_claim_time_evidence_recheck") }
    private val scopeLedger by lazy { Files.readString(scopeLedgerPath) }
    private val hardening by lazy { Files.readString(hardeningPath) }
    private val claimRecheck by lazy { Files.readString(claimRecheckPath) }

    @Test
    fun `Vertex migrations use consecutive next free Flyway versions`() {
        val migrations = migrationFiles()
        val scopeVersion = migrationVersion(scopeLedgerPath)
        val hardeningVersion = migrationVersion(hardeningPath)
        val claimRecheckVersion = migrationVersion(claimRecheckPath)
        val beforeScope = migrations.filter { migrationVersion(it) < scopeVersion }.maxOf(::migrationVersion)

        assertThat(scopeVersion).isEqualTo(beforeScope + 1)
        assertThat(scopeVersion).isEqualTo(39)
        assertThat(hardeningVersion).isEqualTo(scopeVersion + 1)
        assertThat(hardeningVersion).isEqualTo(40)
        assertThat(claimRecheckVersion).isEqualTo(42)
        assertThat(migrations.any { migrationVersion(it) == 41 }).isTrue()
        assertThat(claimRecheckVersion).isEqualTo(hardeningVersion + 2)
    }

    @Test
    fun `Vertex generation rechecks top five scope and keeps the initial ledger append only and sanitized`() {
        assertThat(scopeLedger).contains(
            "read_rag_v2_vertex_generation_evidence",
            "canonicalize_rag_v2_immutable_retrieval_citations",
            "source.external_processing_eligible",
            "p_citations jsonb",
            "persist_rag_v2_immutable_vertex_history",
            "'ANSWERED'",
            "CREATE TABLE rag_v2_immutable_vertex_usage_reservations",
            "CREATE TABLE rag_v2_immutable_vertex_usage_attempts",
            "CREATE TABLE rag_v2_immutable_vertex_usage_outcomes",
            "SECURITY DEFINER",
            "FORCE ROW LEVEL SECURITY",
        )
    }

    @Test
    fun `hardening binds packet to owner scope consent and records OAuth plus generation attempts separately`() {
        assertThat(hardening).contains(
            "question_fingerprint_hmac",
            "scope_claim_id",
            "consent_event_id",
            "pg_advisory_xact_lock",
            "rag-v2-immutable-consent|",
            "action <> 'GRANT'",
            "CREATE TABLE public.rag_v2_immutable_vertex_usage_token_attempts",
            "rag_v2_immutable_vertex_usage_generate_content_attempts",
            "physical_token_call_count",
            "physical_generate_content_call_count",
            "p_input_byte_cap + 512 > p_input_token_cap",
            "claim_rag_v2_immutable_vertex_token_attempt",
            "claim_rag_v2_immutable_vertex_generate_content_attempt",
            "nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id",
            "REVOKE ALL PRIVILEGES ON FUNCTION",
        )
        assertThat(hardening).doesNotContain(
            "raw_response",
            "raw_request",
            "provider_payload",
            "GRANT INSERT ON TABLE public.rag_v2_immutable_vertex_usage_reservations TO decision_app",
        )
    }

    @Test
    fun `claim-time hardening stores only evidence identity and rechecks consent scope pointer plus eligibility`() {
        assertThat(claimRecheck).contains(
            "evidence_manifest jsonb NOT NULL",
            "citationId",
            "chunkRevisionId",
            "canonicalTextSha256",
            "rag-v2-immutable-bundle-activation",
            "rag-v2-immutable-consent|",
            "scope.expires_at > statement_timestamp()",
            "pointer.pointer_version = scope_row.public_pointer_version",
            "source.external_processing_eligible",
            "source.external_embedding_allowed",
            "source.external_generation_allowed",
            "assert_rag_v2_immutable_vertex_reservation_is_current(reservation)",
            "claim_rag_v2_immutable_vertex_token_attempt",
            "claim_rag_v2_immutable_vertex_generate_content_attempt",
        )
        assertThat(claimRecheck).doesNotContain(
            "canonical_content",
            "canonical_text,",
            "raw_response",
            "raw_request",
            "provider_payload",
            "GRANT INSERT ON TABLE public.rag_v2_immutable_vertex_usage_reservations TO decision_app",
        )
    }

    private fun resolveMigration(stem: String): Path {
        val candidates =
            migrationFiles().filter { it.fileName.toString().matches(Regex("""V[0-9]+__${Regex.escape(stem)}\.sql""")) }
        check(candidates.size == 1) {
            "Expected one Pre-S5 Vertex migration for $stem, found ${candidates.size}."
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
        ) { "Flyway migration version is missing from ${path.fileName}." }
}
