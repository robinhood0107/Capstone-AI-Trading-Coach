package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class P1AutomationMarketDataV110MigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath = migrationDirectory.resolve("V110__p1_automation_market_data.sql")
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `V110 adds only additive bootstrap and bounded reader authority`() {
        assertThat(migration).contains(
            "'AUTOMATION_BOOTSTRAP'",
            "'p1-automation-market-bootstrap.v1'",
            "CREATE FUNCTION public.p1_read_automation_atr_bars_v1",
            "CREATE FUNCTION public.p1_read_automation_market_history_status_v1",
            "session_user<>'decision_automation_runtime'",
            "p_limit NOT BETWEEN 1 AND 101",
            "GRANT EXECUTE ON FUNCTION public.p1_read_automation_atr_bars_v1",
        )
        assertThat(migration).doesNotContain(
            "GRANT SELECT ON TABLE public.market_data_bars TO decision_automation_runtime",
            "GRANT SELECT ON TABLE public.market_data_research_bars TO decision_automation_runtime",
            "DROP TABLE",
            "TRUNCATE",
        )
    }

    @Test
    fun `V110 is the next migration and historical V75 stays immutable`() {
        val versions =
            Files.list(migrationDirectory).use { paths ->
                paths
                    .filter { Files.isRegularFile(it) && it.fileName.toString().matches(Regex("V[0-9]+__.+\\.sql")) }
                    .map {
                        it.fileName
                            .toString()
                            .substringAfter('V')
                            .substringBefore("__")
                            .toInt()
                    }.toList()
            }
        assertThat(versions.max()).isEqualTo(115)
        assertThat(migrationDirectory.resolve("V75__s5_7b_market_data_archive.sql")).isRegularFile()
    }
}
