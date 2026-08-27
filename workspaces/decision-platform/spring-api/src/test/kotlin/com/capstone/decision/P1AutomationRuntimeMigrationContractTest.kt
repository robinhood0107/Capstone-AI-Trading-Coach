package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class P1AutomationRuntimeMigrationContractTest {
    private val path = Path.of("src/main/resources/db/migration/V90__p1_mock_automation_runtime.sql")

    @Test
    fun `V90 adds a least privilege restart safe automation runtime`() {
        val sql = Files.readString(path)

        assertThat(sql).contains(
            "CREATE TABLE public.automation_runtime_schedule",
            "CREATE TABLE public.automation_runtime_claim",
            "CREATE TABLE public.automation_runtime_checkpoint",
            "CREATE TABLE public.automation_processed_ticks",
            "CREATE TABLE public.automation_order_reservations",
            "UNIQUE (user_id,session_date)",
            "logical_submit_count integer NOT NULL CHECK (logical_submit_count BETWEEN 0 AND 1)",
            "quantity integer NOT NULL CHECK (quantity = 1)",
            "order_id text",
            "provider_order_ref_hash text",
            "CREATE FUNCTION public.p1_author_automation_activation_gate_v2",
            "CREATE FUNCTION public.p1_automation_runtime_readiness_v1",
            "CREATE FUNCTION public.p1_start_automation_runtime_v1",
            "CREATE FUNCTION public.p1_stop_automation_runtime_v1",
            "CREATE FUNCTION public.p1_roll_automation_schedule_v1",
            "CREATE FUNCTION public.p1_claim_automation_session_v1",
            "CREATE FUNCTION public.p1_advance_automation_checkpoint_v1",
            "FORCE ROW LEVEL SECURITY",
            "TO decision_automation_runtime",
        )
    }

    @Test
    fun `V90 stores hashes and bounded columns but no raw provider account or order payload`() {
        val sql = Files.readString(path)

        assertThat(sql).contains(
            "claim_token_hash text",
            "tick_identity_hash text",
            "result_hash text",
            "provider_order_ref_hash text",
            "CREATE TRIGGER automation_runtime_events_append_only",
        )
        assertThat(sql.lowercase()).doesNotContain(
            "raw_provider",
            "raw_account",
            "raw_order",
            "provider_payload",
            "account_payload",
            "order_payload",
            "on delete cascade",
        )
    }
}
