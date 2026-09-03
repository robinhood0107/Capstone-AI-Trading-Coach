package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class P1AutomationV3MigrationsContractTest {
    private val directory = Path.of("src/main/resources/db/migration")
    private val v111 = Files.readString(directory.resolve("V111__p1_automation_exit_policy_atr.sql"))
    private val v112 = Files.readString(directory.resolve("V112__p1_automation_evidence_first.sql"))
    private val v113 = Files.readString(directory.resolve("V113__p1_automation_ai_settings_snapshot.sql"))
    private val v114 = Files.readString(directory.resolve("V114__p1_automation_v3_owner_read_scope.sql"))
    private val v115 = Files.readString(directory.resolve("V115__p1_automation_v3_policy_upgrade_cas.sql"))

    @Test
    fun `V111 adds nullable legacy compatible user exit snapshots and bounded runtime functions`() {
        assertThat(v111).contains(
            "max_holding_sessions BETWEEN 0 AND 1260",
            "atr_period BETWEEN 5 AND 100",
            "atr_multiplier_milli%100=0",
            "'ATR_TRAILING'",
            "ALTER COLUMN expiry_session DROP NOT NULL",
            "CREATE FUNCTION public.p1_put_automation_policy_v2",
            "CREATE FUNCTION public.p1_arm_automation_v3",
            "CREATE FUNCTION public.p1_read_automation_runtime_state_v3",
            "CREATE FUNCTION public.p1_advance_automation_checkpoint_v3",
            "LEGACY_POSITION_PRESENT",
            "MARKET_DATA_CATCHUP_REQUIRED",
            "<policy_row.atr_period+1",
            "<required_period+1",
        )
        assertThat(v111).doesNotContain(
            "GRANT SELECT ON TABLE public.market_data_bars TO decision_automation_runtime",
            "DROP TABLE",
            "TRUNCATE",
        )
    }

    @Test
    fun `V112 owns evidence first state verified ledgers and replay only reader`() {
        assertThat(v112).contains(
            "'NEWS_SCREENING'",
            "CREATE TABLE public.automation_candidate_screenings",
            "CREATE TABLE public.automation_candidate_evidence",
            "CREATE TABLE public.automation_ai_provider_operations",
            "bounded_quote text NOT NULL",
            "verified boolean NOT NULL CHECK (verified)",
            "CREATE TABLE public.automation_v3_usage",
            "provider_call_count BETWEEN 0 AND 64",
            "grounding_query_count=0 OR screening_provider_call_count=1",
            "CREATE FUNCTION public.p1_reserve_automation_ai_provider_v1",
            "CREATE FUNCTION public.p1_complete_automation_ai_provider_v1",
            "CREATE FUNCTION public.p1_fail_automation_ai_provider_v1",
            "CREATE FUNCTION public.p1_read_after_hours_replay_bars_v1",
            "session_user<>'decision_replay'",
        )
        assertThat(v112.lowercase()).doesNotContain("raw_provider_response", "access_token")
    }

    @Test
    fun `V113 snapshots owner AI settings and blocks unready enabled provider`() {
        assertThat(v113).contains(
            "ai_judgement_enabled boolean NOT NULL DEFAULT false",
            "thinking_level IN ('minimal','low','medium')",
            "CREATE FUNCTION public.put_strong_llm_owner_settings_v2",
            "ai_settings_sha256",
            "ai_settings_snapshot_json jsonb",
            "AI_PROVIDER_NOT_READY",
            "COALESCE(p_provider_capability_ready,false)",
            "CREATE TRIGGER automation_run_ai_snapshot_v113",
            "CREATE FUNCTION public.p1_record_automation_ai_judgement_v2",
            "<policy_row.atr_period+1",
        )
    }

    @Test
    fun `V114 keeps runtime checkpoint private while owner run reads stay available`() {
        assertThat(v114).contains(
            "CREATE FUNCTION public.p1_automation_ai_judgement_runtime_scope_v1",
            "session_user<>'decision_automation_runtime' THEN false",
            "DROP POLICY automation_ai_judgements_scope_v106",
            "CREATE POLICY automation_ai_judgements_runtime_v114",
            "TO decision_app,decision_automation_runtime",
        )
        assertThat(v114).doesNotContain(
            "GRANT SELECT ON public.automation_runtime_checkpoint",
            "GRANT SELECT ON TABLE public.automation_runtime_checkpoint",
        )
    }

    @Test
    fun `V115 creates first V3 policy after legacy history with external version zero`() {
        assertThat(v115).contains(
            "CREATE OR REPLACE FUNCTION public.p1_put_automation_policy_v2",
            "current_v3_version IS NULL AND p_expected_version<>0",
            "WHEN current_v3_version IS NULL THEN COALESCE(latest_historical_version,0)",
            "effective_expected_version,p_scope_hash,p_request_hash",
        )
    }

    @Test
    fun `V111 through V117 remain consecutive latest migrations`() {
        val versions =
            Files.list(directory).use { paths ->
                paths
                    .map { it.fileName.toString() }
                    .filter { it.matches(Regex("V[0-9]+__.+\\.sql")) }
                    .map { it.substringAfter('V').substringBefore("__").toInt() }
                    .sorted()
                    .toList()
            }
        assertThat(versions.takeLast(7)).containsExactly(111, 112, 113, 114, 115, 116, 117)
    }
}
