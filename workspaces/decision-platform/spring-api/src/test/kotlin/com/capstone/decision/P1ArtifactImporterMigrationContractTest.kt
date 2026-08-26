package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Path

class P1ArtifactImporterMigrationContractTest {
    private val migration =
        Path
            .of("src/main/resources/db/migration/V88__p1_return_artifact_import.sql")
            .toFile()
            .readText()
    private val roleBootstrap =
        Path
            .of("../../../infra/init/02-application-roles.sh")
            .normalize()
            .toFile()
            .readText()

    @Test
    fun `V88 imports one exact bundle through function-only worker authority`() {
        assertThat(migration).contains(
            "CREATE TABLE public.p1_return_artifact_bundle",
            "CREATE TABLE public.p1_return_signal_projection",
            "CREATE FUNCTION public.import_p1_return_bundle_v1",
            "session_user<>'decision_worker'",
            "jsonb_array_length(packet->'signals')<>62",
            "count(DISTINCT signal->>'symbol')",
            "packet->>'manifestSha256'<>v_bundle_sha256",
            "GRANT EXECUTE ON FUNCTION public.import_p1_return_bundle_v1(text,text) TO decision_worker",
        )
        assertThat(migration).doesNotContain(
            "GRANT INSERT ON TABLE public.p1_return_artifact_bundle TO decision_worker",
            "GRANT UPDATE ON TABLE public.p1_return_artifact_bundle TO decision_worker",
            "GRANT DELETE ON TABLE public.p1_return_artifact_bundle TO decision_worker",
        )
    }

    @Test
    fun `V88 keeps synthetic projection behind an explicit read flag and production pointer real-only`() {
        assertThat(migration).contains(
            "CREATE VIEW public.current_p1_return_signal_pointer",
            "WHERE bundle.real_team_b AND bundle.model_quality='PASS' AND bundle.mock_runtime_eligible",
            "CREATE FUNCTION public.read_p1_return_signal_v2(p_symbol text,p_allow_synthetic boolean)",
            "p_allow_synthetic AND NOT bundle.real_team_b",
            "GRANT EXECUTE ON FUNCTION public.read_p1_return_signal_v2(text,boolean) TO decision_app",
        )
        assertThat(migration).doesNotContain("GRANT SELECT ON TABLE public.p1_return_signal_projection TO decision_app")
    }

    @Test
    fun `role bootstrap replay restores only the two V88 function grants`() {
        assertThat(roleBootstrap).contains(
            "import_p1_return_bundle_v1(text,text)",
            "TO decision_worker",
            "read_p1_return_signal_v2(text,boolean)",
            "TO decision_app",
        )
        assertThat(roleBootstrap).doesNotContain(
            "GRANT INSERT ON TABLE p1_return_artifact_bundle TO decision_worker",
            "GRANT SELECT ON TABLE p1_return_signal_projection TO decision_app",
        )
    }
}
