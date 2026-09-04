package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class P1DashboardCompletionMigrationContractTest {
    private val directory = Path.of("src/main/resources/db/migration")

    @Test
    fun `V123 adds owner scoped latest and recent risk indexes`() {
        val migration = Files.readString(directory.resolve("V123__dashboard_recent_risk_results.sql"))

        assertThat(migration).contains(
            "latest_dashboard_risk_result_authorized",
            "recent_dashboard_risk_results_authorized",
            "item.user_id=p_actor_user_id",
            "LIMIT 20",
            "consume_actor_request_capability_v2",
        )
        assertThat(migration).doesNotContain("GRANT SELECT ON", "TRUNCATE", "DROP TABLE")
    }

    @Test
    fun `V124 binds exact 31 by 104 replay and publishes only real dashboard projections`() {
        val migration = Files.readString(directory.resolve("V124__owner_scenario_dashboard_materialization.sql"))

        assertThat(migration).contains(
            "read_owner_scenario_materialization_inputs_v1",
            "publish_owner_scenario_dashboard_v1",
            "symbol_count <> 31 OR bar_count <> 3224",
            "'REAL_ARTIFACT','REAL_ARTIFACT'",
            "session_user <> 'decision_worker'",
        )
        assertThat(migration).doesNotContain("GRANT SELECT ON", "TRUNCATE", "DROP TABLE")
    }

    @Test
    fun `V125 exposes registered public references when no indexed generation exists`() {
        val migration = Files.readString(directory.resolve("V125__rag_source_registry_includes_registered_references.sql"))

        assertThat(migration).contains(
            "source.source_type IN ('PROJECT_SOURCE_CARD','UPSTREAM_REFERENCE')",
            "revision.access_level='PUBLIC'",
            "source.retired_at IS NULL",
            "LIMIT 30",
        )
        assertThat(migration).doesNotContain("GRANT SELECT ON", "TRUNCATE", "DROP TABLE")
    }

    @Test
    fun `V126 reports a matching armed schedule as operationally ready`() {
        val migration = Files.readString(directory.resolve("V126__p1_armed_runtime_readiness.sql"))

        assertThat(migration).contains(
            "control_row.control_state IN ('DISARMED','ARMED')",
            "IF control_row.control_state='ARMED' THEN",
            "schedule.control_version=control_row.version",
            "account_baseline_matches:=observed_digest IS NOT NULL",
        )
        assertThat(migration).doesNotContain("GRANT SELECT ON", "TRUNCATE", "DROP TABLE")
    }

    @Test
    fun `V127 fixes the demo window and selects the newest published projection`() {
        val migration = Files.readString(directory.resolve("V127__owner_demo_evaluation_window.sql"))

        assertThat(migration).contains(
            "session_date <= DATE '2026-09-03'",
            "'evaluationStart','2026-08-18'",
            "'evaluationEnd','2026-09-03'",
            "item.published_at DESC",
        )
        assertThat(migration).doesNotContain("TRUNCATE", "DROP TABLE")
    }

    @Test
    fun `V128 projects a bounded finance source catalog from the active RAG bundle`() {
        val migration = Files.readString(directory.resolve("V128__rag_user_finance_source_catalog.sql"))

        assertThat(migration).contains(
            "rag_user_finance_source_catalog",
            "rag_v2_immutable_source_revisions",
            "rag_v2_immutable_public_bundle_pointers",
            "NOT ('API'=ANY(source.retrieval_topics))",
            "LIMIT 8",
        )
        assertThat(migration).doesNotContain("TRUNCATE", "DROP TABLE", "UPSTREAM_REFERENCE")
    }
}
