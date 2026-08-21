package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class S65FinancialEngineeringStorageContractTest {
    private val root = Path.of("../../..").toAbsolutePath().normalize()
    private val migration =
        Files.readString(
            root.resolve(
                "workspaces/decision-platform/spring-api/src/main/resources/db/migration/" +
                    "V77__s6_5_financial_engineering_snapshots.sql",
            ),
        )

    @Test
    fun `migration is append only atomic and least privilege`() {
        assertThat(migration).contains("financial_engineering_snapshots")
        assertThat(migration).contains("financial_engineering_report_manifests")
        assertThat(migration).contains("BEFORE UPDATE OR DELETE")
        assertThat(migration).contains("FORCE ROW LEVEL SECURITY")
        assertThat(migration).contains("session_user <> 'decision_market_writer'")
        assertThat(migration).contains("TO decision_market_writer")
        assertThat(migration).contains("TO decision_app")
        assertThat(migration).contains("numeric payload hash mismatch")
        assertThat(migration).contains("financial engineering identity hash conflict")
        assertThat(migration).contains("RETURN QUERY SELECT 'NO_OP'")
    }

    @Test
    fun `reader is complete report joined and point in time bounded`() {
        assertThat(migration).contains("JOIN public.financial_engineering_report_manifests")
        assertThat(migration).contains("s.available_at <= p_evaluation_as_of")
        assertThat(migration).contains("AND r.complete")
        assertThat(migration).doesNotContain("risk_snapshots")
        assertThat(migration).doesNotContain("financial_engineering_reports")
    }
}
