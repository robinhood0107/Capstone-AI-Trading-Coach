package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class S67CrossMarketStorageContractTest {
    private val migration =
        Files.readString(
            Path.of("../../..").toAbsolutePath().normalize().resolve(
                "workspaces/decision-platform/spring-api/src/main/resources/db/migration/" +
                    "V78__s6_7_cross_market_warn_only.sql",
            ),
        )

    @Test
    fun `V78 adds versioned bounded writer reader and immutable least privilege storage`() {
        assertThat(migration).contains("cross_market_risk_snapshots_v2")
        assertThat(migration).contains("append_cross_market_risk_snapshot_v2")
        assertThat(migration).contains("read_cross_market_decision_input_v2")
        assertThat(migration).contains("session_user <> 'decision_market_writer'")
        assertThat(migration).contains("session_user = 'decision_app'")
        assertThat(migration).contains("FORCE ROW LEVEL SECURITY")
        assertThat(migration).contains("BEFORE UPDATE OR DELETE")
        assertThat(migration).contains("RETURN 'NO_OP'")
        assertThat(migration).contains("identity hash conflict")
        assertThat(migration).contains("semantic input hash mismatch")
        assertThat(migration).contains("p_exposure_available_at <> p_available_at")
        assertThat(migration).doesNotContain("GRANT INSERT ON TABLE public.cross_market_risk_snapshots_v2 TO decision_market_writer")
        assertThat(migration).doesNotContain("GRANT UPDATE")
        assertThat(migration).doesNotContain("GRANT DELETE")
    }

    @Test
    fun `P1 storage rejects ENFORCED and has no threshold fallback`() {
        assertThat(migration).contains("p_runtime_mode = 'ENFORCED'")
        assertThat(migration).contains("threshold_percentile IN (95, 97.5, 99)")
        assertThat(migration).doesNotContain("defaultThreshold")
        assertThat(migration).doesNotContain("coalesce(p_threshold_percentile, 80)")
    }
}
