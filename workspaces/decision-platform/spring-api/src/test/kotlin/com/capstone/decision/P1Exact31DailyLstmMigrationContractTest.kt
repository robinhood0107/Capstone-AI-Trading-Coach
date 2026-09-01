package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Path

class P1Exact31DailyLstmMigrationContractTest {
    private val migration =
        Path.of("src/main/resources/db/migration/V116__p1_exact31_daily_lstm.sql")
            .toFile()
            .readText()

    @Test
    fun `V116 separates model seed and daily signals without confidence`() {
        assertThat(migration).contains(
            "CREATE TABLE public.p1_return_model_seed_signal",
            "CREATE TABLE public.p1_return_daily_signal_batch",
            "CREATE TABLE public.p1_return_daily_signal_projection",
            "CREATE FUNCTION public.import_p1_return_bundle_v2",
            "CREATE FUNCTION public.p1_commit_daily_signal_batch_v1",
            "CREATE FUNCTION public.p1_read_automation_runtime_state_v4",
            "signal ? 'confidence'",
            "model_quality IN ('PASS','BELOW_BASELINE')",
        )
        assertThat(migration).doesNotContain(
            "confidence numeric NOT NULL",
            "GRANT INSERT ON TABLE public.p1_return_daily_signal_projection TO decision_automation_runtime",
        )
    }

    @Test
    fun `V116 uses exact62 transactional identities and function only roles`() {
        assertThat(migration).contains(
            "jsonb_array_length(packet->'signals')<>62",
            "count(DISTINCT signal->>'symbol')",
            "outcome:='REPLAYED'",
            "RAISE EXCEPTION 'daily inference identity conflict'",
            "GRANT EXECUTE ON FUNCTION public.import_p1_return_bundle_v2(text,text) TO decision_worker",
            "TO decision_automation_runtime",
        )
    }
}
