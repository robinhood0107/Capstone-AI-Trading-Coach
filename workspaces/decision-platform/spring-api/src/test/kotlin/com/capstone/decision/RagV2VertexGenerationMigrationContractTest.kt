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
    private val apiKeyOnlyPath by lazy { resolveMigration("s4_7d_vertex_api_key_only_runtime") }
    private val serviceAccountPath by lazy { resolveMigration("pre_s5_vertex_service_account_oauth_runtime") }
    private val scopeLedger by lazy { Files.readString(scopeLedgerPath) }
    private val hardening by lazy { Files.readString(hardeningPath) }
    private val claimRecheck by lazy { Files.readString(claimRecheckPath) }
    private val apiKeyOnly by lazy { Files.readString(apiKeyOnlyPath) }
    private val serviceAccount by lazy { Files.readString(serviceAccountPath) }
    private val bootstrapRoles by lazy {
        Files.readString(Path.of("../../../infra/init/02-application-roles.sh"))
    }

    @Test
    fun `Vertex migrations use consecutive next free Flyway versions`() {
        val migrations = migrationFiles()
        val scopeVersion = migrationVersion(scopeLedgerPath)
        val hardeningVersion = migrationVersion(hardeningPath)
        val claimRecheckVersion = migrationVersion(claimRecheckPath)
        val apiKeyOnlyVersion = migrationVersion(apiKeyOnlyPath)
        val serviceAccountVersion = migrationVersion(serviceAccountPath)
        val beforeScope = migrations.filter { migrationVersion(it) < scopeVersion }.maxOf(::migrationVersion)

        assertThat(scopeVersion).isEqualTo(beforeScope + 1)
        assertThat(scopeVersion).isEqualTo(39)
        assertThat(hardeningVersion).isEqualTo(scopeVersion + 1)
        assertThat(hardeningVersion).isEqualTo(40)
        assertThat(claimRecheckVersion).isEqualTo(42)
        assertThat(migrations.any { migrationVersion(it) == 41 }).isTrue()
        assertThat(claimRecheckVersion).isEqualTo(hardeningVersion + 2)
        assertThat(apiKeyOnlyVersion).isEqualTo(52)
        assertThat(serviceAccountVersion).isEqualTo(57)
        assertThat(migrations.maxOf(::migrationVersion)).isEqualTo(93)
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

    @Test
    fun `historical API-key-only supersession removed OAuth claim authority and kept one generation attempt`() {
        assertThat(apiKeyOnly).contains(
            "VERTEX_EXPRESS_API_KEY",
            "p_token_physical_call_cap <> 0",
            "physical_token_call_count = 0",
            "DROP FUNCTION public.claim_rag_v2_immutable_vertex_token_attempt",
            "p_authentication_mode text",
            "assert_rag_v2_immutable_vertex_reservation_is_current(reservation)",
            "claim_rag_v2_immutable_vertex_generate_content_attempt",
            "physical_generate_content_call_count = 1",
        )
        assertThat(apiKeyOnly).doesNotContain(
            "raw_api_key",
            "raw_response",
            "raw_request",
            "provider_payload",
            "GOOGLE_APPLICATION_CREDENTIALS",
        )
    }

    @Test
    fun `service-account supersession restores one OAuth token and one generation attempt without raw credential storage`() {
        assertThat(serviceAccount).contains(
            "SERVICE_ACCOUNT_OAUTH",
            "p_token_physical_call_cap <> 1",
            "physical_token_call_count = 1",
            "CREATE FUNCTION public.claim_rag_v2_immutable_vertex_token_attempt",
            "claim_rag_v2_immutable_vertex_generate_content_attempt",
            "physical_generate_content_call_count = 1",
            "assert_rag_v2_immutable_vertex_reservation_is_current(reservation)",
        )
        assertThat(serviceAccount).doesNotContain(
            "private_key",
            "access_token",
            "raw_response",
            "raw_request",
            "provider_payload",
        )
    }

    @Test
    fun `bootstrap reapplies current service-account Vertex ledger capabilities after a volume restart`() {
        assertThat(bootstrapRoles).contains(
            "public.reserve_rag_v2_immutable_vertex_usage(text,text,text,text,text,text,text,text,text,text,text,timestamp with time zone,integer,integer,integer,bigint,bigint,bigint,integer,integer,text,jsonb)",
            "V57 service-account OAuth ledger는 token과 generation 각각 1회 capability만 재부여한다.",
            "claim_rag_v2_immutable_vertex_token_attempt(text, text)",
            "claim_rag_v2_immutable_vertex_generate_content_attempt(text, text)",
            "commit_rag_v2_immutable_vertex_usage(text, text, integer, integer, integer)",
            "mark_rag_v2_immutable_vertex_usage_unknown_billing(text, text)",
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
