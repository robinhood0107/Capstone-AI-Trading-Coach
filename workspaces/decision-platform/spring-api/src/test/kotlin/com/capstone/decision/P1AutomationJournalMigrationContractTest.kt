package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class P1AutomationJournalMigrationContractTest {
    private val path = Path.of("src/main/resources/db/migration/V89__p1_automation_journal_api.sql")

    @Test
    fun `V89 locks owner scoped automation Journal and hash only idempotency`() {
        val sql = Files.readString(path)

        assertThat(sql).contains(
            "ALTER TABLE public.journals",
            "owner_scope text",
            "CHECK (char_length(title) BETWEEN 1 AND 120)",
            "CHECK (char_length(body) BETWEEN 1 AND 8192)",
            "cardinality(p_tags) <= 20",
            "CREATE TABLE public.automation_control",
            "CREATE TABLE public.automation_activation_gate",
            "CREATE TABLE public.automation_runs",
            "CREATE TABLE public.automation_positions",
            "CREATE TABLE public.automation_events",
            "CREATE TABLE public.automation_control_idempotency",
            "CREATE TABLE public.journal_idempotency",
            "scope_hash text PRIMARY KEY",
            "request_hash text NOT NULL",
            "result_json jsonb NOT NULL",
            "CREATE UNIQUE INDEX automation_positions_one_open_lot_idx",
            "CREATE TRIGGER automation_events_append_only",
            "CREATE FUNCTION public.p1_journal_links_owned",
            "CREATE FUNCTION public.p1_arm_automation_v1",
            "CREATE FUNCTION public.p1_disarm_automation_v1",
            "ALTER TABLE public.journals FORCE ROW LEVEL SECURITY",
            "ALTER TABLE public.journal_idempotency FORCE ROW LEVEL SECURITY",
            "FROM PUBLIC,decision_worker,decision_replay",
        )
        assertThat(sql).doesNotContain(
            "raw_idempotency",
            "raw_provider",
            "provider_payload",
            "ON DELETE CASCADE",
        )
    }

    @Test
    fun `V89 keeps exact automation states quantity and no short boundary`() {
        val sql = Files.readString(path)

        assertThat(sql).contains(
            "'DISARMED','ARMED','HALTED'",
            "'PENDING_RECONCILIATION','CANCELLED_UNFILLED','COMPLETED','SKIPPED_NO_ACTION'",
            "quantity integer NOT NULL CHECK (quantity = 1)",
            "bot_owned boolean NOT NULL CHECK (bot_owned)",
            "short_allowed boolean NOT NULL CHECK (NOT short_allowed)",
            "WHERE status IN ('OPEN','EXIT_PENDING')",
        )
    }
}
