package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagGuardHistoryMigrationContractTest {
    private val migrationPath =
        Path.of(
            "src/main/resources/db/migration/" +
                "V20__s4_4_rag_guard_history.sql",
        )
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `V20 creates append-only consent durable claim encrypted history citation and feedback state`() {
        listOf(
            "CREATE TABLE rag_consent_events",
            "CREATE TABLE rag_answer_claims",
            "CREATE TABLE rag_answer_claim_transitions",
            "CREATE TABLE rag_answer_history",
            "CREATE TABLE rag_answer_citations",
            "CREATE TABLE rag_answer_feedback",
            "CREATE TABLE rag_provider_usage_ledger",
        ).forEach { table ->
            assertThat(migration).contains(table)
        }
        assertThat(migration).contains(
            "'PENDING'",
            "'COMPLETE'",
            "'FAILED_BEFORE_PROVIDER'",
            "'UNKNOWN_AFTER_PROVIDER'",
            "'EXTERNAL_AI_RAG_V1'",
            "'GRANT'",
            "'REVOKE'",
        )
        assertThat(migration).contains("expires_at = created_at + interval '30 days'")
        assertThat(migration).contains(
            "chunk_revision_id text NOT NULL",
            "'chunkRevisionId'",
            "(item.value ->> 'chunkRevisionId') ~ '^rag_chk_[0-9a-f]{32}$'",
            "item.value ->> 'sectionTitle' =",
            "chunk.heading_path[cardinality(chunk.heading_path)]",
        )
        assertThat(migration).doesNotContain(
            "question_plaintext",
            "answer_plaintext",
            "raw_idempotency",
            "raw_question",
        )
    }

    @Test
    fun `V20 exposes only bounded definer operations to decision app`() {
        listOf(
            "record_rag_consent_event",
            "read_effective_rag_consent",
            "claim_rag_answer",
            "complete_rag_answer",
            "fail_rag_answer_before_provider",
            "mark_rag_answer_unknown_after_provider",
            "read_rag_history_metadata",
            "read_rag_history_detail",
            "read_rag_history_citations",
            "delete_owned_rag_history",
            "upsert_owned_rag_answer_feedback",
            "purge_expired_rag_history",
        ).forEach { function ->
            assertThat(migration).contains("CREATE FUNCTION $function")
            assertThat(migration).contains("ON FUNCTION $function")
        }
        assertThat(migration).contains("SECURITY DEFINER")
        assertThat(migration).contains("SET search_path = pg_catalog, public, pg_temp")
        assertThat(migration).contains("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC")
        assertThat(migration).contains("REVOKE CREATE ON SCHEMA public FROM decision_app")
        assertThat(migration).doesNotContain(
            "GRANT SELECT ON TABLE rag_answer_history TO decision_app",
            "GRANT INSERT ON TABLE rag_answer_history TO decision_app",
            "GRANT UPDATE ON TABLE rag_answer_history TO decision_app",
            "GRANT DELETE ON TABLE rag_answer_history TO decision_app",
            "EXECUTE format",
            "quote_ident",
        )
    }

    @Test
    fun `V20 leaves writer query and public roles unable to reach user history or consent`() {
        listOf("decision_rag_writer", "decision_rag_query").forEach { role ->
            assertThat(migration).contains("FROM $role;")
        }
        assertThat(migration).contains("session_user <> 'decision_app'")
        assertThat(migration).contains("current_setting('app.actor_user_id', true)")
        assertThat(migration).contains("jsonb_array_length(p_citations)")
    }
}
