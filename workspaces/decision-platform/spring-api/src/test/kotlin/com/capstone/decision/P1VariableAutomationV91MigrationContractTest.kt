package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class P1VariableAutomationV91MigrationContractTest {
    private val path = Path.of("src/main/resources/db/migration/V91__p1_variable_quantity_policy_runtime.sql")

    @Test
    fun `V91 adds versioned policy variable reservation and structural account lineage`() {
        val sql = Files.readString(path)

        assertThat(sql).contains(
            "CREATE TABLE public.automation_policy_versions",
            "capital_limit_krw BETWEEN 10000 AND 10000000000",
            "stop_loss_bps BETWEEN 100 AND 1500",
            "take_profit_bps BETWEEN 200 AND 3000",
            "CREATE TABLE public.automation_account_lineage",
            "quantity>0",
            "estimated_amount_krw=quantity*limit_price_krw",
            "filled_quantity+leaves_quantity+unfilled_terminated_quantity=quantity",
            "'ORDER_SIZING'",
            "CREATE FUNCTION public.p1_reserve_automation_order_v2",
            "CREATE FUNCTION public.p1_bind_automation_decision_v2",
            "CREATE FUNCTION public.p1_read_automation_runtime_state_v2",
            "CREATE FUNCTION public.p1_advance_automation_checkpoint_v2",
            "snapshot_artifact_canonical_json::jsonb->'orderIntent'<>expected_intent",
            "IF p_expected_version<>0 THEN RAISE EXCEPTION 'automation policy version conflict'",
        )
    }

    @Test
    fun `V91 arm is atomic and blocks incomplete KIS risk balance without fabrication`() {
        val sql = Files.readString(path)

        assertThat(sql).contains(
            "source_version='kis-mock-online-complete-v2'",
            "completeness='COMPLETE'",
            "RAISE EXCEPTION 'BLOCKED_INCOMPLETE_RISK_BALANCE' USING ERRCODE='P1B01'",
            "CREATE FUNCTION public.p1_arm_automation_v2",
            "schedule_state,run_at",
            "(target_session+time '09:30') AT TIME ZONE 'Asia/Seoul'",
        )
        assertThat(sql.lowercase()).doesNotContain(
            "raw_provider",
            "raw_account",
            "raw_order",
            "provider_payload",
            "margin_requirement_krw=0",
        )
    }

    @Test
    fun `V91 decision binding reuses canonical exact intent bytes`() {
        val sql = Files.readString(path)

        assertThat(sql).contains(
            "expected_intent:=reservation_row.exact_intent_json::jsonb",
            "convert_to(reservation_row.exact_intent_json,'UTF8')",
        )
        assertThat(sql).doesNotContain(
            "reservation_row.estimated_amount_krw::text",
            "convert_to(expected_intent::text,'UTF8')",
        )
    }
}
