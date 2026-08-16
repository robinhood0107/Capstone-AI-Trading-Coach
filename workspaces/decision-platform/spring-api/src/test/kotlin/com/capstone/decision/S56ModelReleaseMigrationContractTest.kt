package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class S56ModelReleaseMigrationContractTest {
    private val migrationPath =
        Path.of("src/main/resources/db/migration/V73__s5_6_model_release_signal_batch.sql")
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `V73 is forward-only and release-level LightGBM activation is capability separated`() {
        assertThat(migration).contains(
            "CREATE TABLE public.signal_model_releases",
            "CREATE TABLE public.signal_batches",
            "CREATE TABLE public.signal_batch_members",
            "CREATE TABLE public.active_signal_model_release",
            "CREATE TABLE public.active_signal_batch",
            "CREATE TABLE public.signal_batch_publications",
            "CREATE FUNCTION public.current_s5_signal_batch_clock()",
            "JOIN public.current_s5_signal_batch_clock() current_clock",
            "candidate.producer = 'LIGHTGBM'",
            "session_user <> 'decision_signal_writer'",
            "session_user <> 'decision_signal_admin'",
            "session_user <> 'decision_signal_scheduler'",
            "session_user NOT IN ('decision_signal_scheduler','decision_signal_admin')",
            "FORCE ROW LEVEL SECURITY",
        )
        assertThat(migration).doesNotContain(
            "GRANT SELECT ON TABLE public.signal_model_releases TO decision_app",
            "GRANT INSERT ON TABLE public.signal_batches TO decision_signal_writer",
            "GRANT UPDATE ON TABLE public.active_signal_batch TO decision_signal_scheduler",
            "DROP TABLE public.ingested_signals",
        )
    }

    @Test
    fun `writer scheduler admin receive only their exact definer functions`() {
        assertThat(migration).contains(
            "TO decision_signal_writer;",
            "TO decision_signal_scheduler;",
            "TO decision_signal_scheduler, decision_signal_admin;",
            "TO decision_signal_admin;",
        )
    }
}
