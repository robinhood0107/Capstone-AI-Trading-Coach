package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class S57MarketDataMigrationContractTest {
    private val migrationPath =
        Path.of("src/main/resources/db/migration/V75__s5_7b_market_data_archive.sql")
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `V75 is the next forward migration and stores normalized data outside quote observations`() {
        assertThat(migration).contains(
            "CREATE TABLE market_data_manifests",
            "CREATE TABLE market_data_bars",
            "CREATE TABLE market_data_indices",
            "CREATE TABLE market_data_macro",
            "CREATE TABLE market_data_universes",
            "market_data_operational_bars",
            "history_rank <= 253",
            "history_rank <= 1260",
            "NEEDS_HUMAN",
            "supersedes_sha256",
            "symbol ~ '^[0-9A-Z]{6}$'",
        )
        assertThat(migration).doesNotContain(
            "INSERT INTO market_quote_observations",
            "GRANT SELECT ON TABLE market_data_research_bars TO decision_app",
            "GRANT UPDATE",
            "GRANT DELETE",
            "GRANT TRUNCATE",
        )
    }

    @Test
    fun `V75 separates writer operational research and retention authorities`() {
        assertThat(migration).contains(
            "TO decision_market_writer",
            "TO decision_market_operational_reader",
            "TO decision_market_research_reader",
            "TO decision_market_retention_admin",
            "prune_market_data_macro(date, boolean)",
            "p_apply boolean DEFAULT false",
            "REVOKE SELECT, UPDATE, DELETE, TRUNCATE",
            "FROM PUBLIC, decision_app, decision_market_writer",
        )
    }
}
