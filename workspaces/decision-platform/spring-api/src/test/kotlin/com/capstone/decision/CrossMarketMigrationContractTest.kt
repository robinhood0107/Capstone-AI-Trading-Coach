package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class CrossMarketMigrationContractTest {
    @Test
    fun `V23 fixes append-only evidence and bounded reader capabilities without snapshot writer`() {
        val migration = Files.readString(repositoryRoot().resolve(MIGRATION))

        listOf(
            "market_source_entitlements",
            "cross_market_exposure_catalog_entries",
            "cross_market_observations",
            "analyst_revision_evidence",
            "market_cause_evidence",
            "cross_market_risk_snapshots",
            "cross_market_snapshot_evidence_links",
        ).forEach { table ->
            assertThat(migration).contains("CREATE TABLE $table")
        }
        listOf(
            "latest_cross_market_observations",
            "latest_analyst_revision_evidence",
            "latest_market_cause_evidence",
            "latest_cross_market_risk_snapshots",
        ).forEach { view ->
            assertThat(migration).contains("CREATE VIEW $view")
        }
        listOf(
            "append_market_source_entitlement",
            "append_cross_market_exposure_catalog_entry",
            "append_cross_market_observation",
            "append_analyst_revision_evidence",
            "append_market_cause_evidence",
        ).forEach { function ->
            assertThat(migration).contains("CREATE FUNCTION $function")
        }
        assertThat(migration).contains("SECURITY DEFINER")
        assertThat(migration).contains("SET search_path = pg_catalog, public, pg_temp")
        assertThat(migration).contains("session_user <> 'decision_market_writer'")
        assertThat(migration).contains("ENABLE ROW LEVEL SECURITY")
        assertThat(migration).contains("FORCE ROW LEVEL SECURITY")
        assertThat(migration).contains("REVOKE ALL PRIVILEGES")
        assertThat(migration).contains("GRANT EXECUTE")
        assertThat(migration).contains("TO decision_market_writer")
        assertThat(migration).contains("GRANT SELECT ON TABLE")
        assertThat(migration).contains("TO decision_app")
        assertThat(migration).doesNotContain("append_cross_market_risk_snapshot")
        assertThat(migration).doesNotContain("GRANT INSERT")
        assertThat(migration).doesNotContain("GRANT UPDATE")
        assertThat(migration).doesNotContain("GRANT DELETE")
    }

    private fun repositoryRoot(): Path {
        var current = Path.of(System.getProperty("user.dir")).toAbsolutePath()
        while (!Files.exists(current.resolve("AGENTS.md"))) {
            current = current.parent ?: error("repository root was not found")
        }
        return current
    }

    private companion object {
        const val MIGRATION =
            "workspaces/decision-platform/spring-api/src/main/resources/db/migration/" +
                "V23__s4_8b_cross_market_evidence.sql"
    }
}
