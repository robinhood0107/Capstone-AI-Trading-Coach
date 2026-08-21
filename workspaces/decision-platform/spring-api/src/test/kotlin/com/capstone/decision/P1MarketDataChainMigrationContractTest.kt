package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class P1MarketDataChainMigrationContractTest {
    private val migration =
        Files.readString(
            Path.of("src/main/resources/db/migration/V76__p1_market_data_chain_guard.sql"),
        )

    @Test
    fun `V76 locks the accepted predecessor and keeps provider authority absent`() {
        assertTrue(migration.contains("enforce_market_data_daily_chain"))
        assertTrue(migration.contains("current_market_data_manifest_head"))
        assertTrue(migration.contains("pg_advisory_xact_lock"))
        assertTrue(migration.contains("previous accepted market-data manifest is not the DB head"))
        assertTrue(migration.contains("daily market-data sessions must append forward"))
        assertTrue(migration.contains("REVOKE ALL ON FUNCTION enforce_market_data_daily_chain() FROM PUBLIC"))
        assertTrue(
            migration.contains(
                "GRANT EXECUTE ON FUNCTION current_market_data_manifest_head(DATE) TO decision_market_writer",
            ),
        )
        assertTrue(!migration.contains("GRANT INSERT"))
        assertTrue(!migration.contains("decision_signal"))
    }
}
