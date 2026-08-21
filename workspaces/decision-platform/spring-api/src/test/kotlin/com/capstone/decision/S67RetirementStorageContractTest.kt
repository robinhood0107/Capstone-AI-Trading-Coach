package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import java.util.HexFormat

class S67RetirementStorageContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val historicalMigration = migrationDirectory.resolve("V78__s6_7_cross_market_warn_only.sql")
    private val retirementMigration = migrationDirectory.resolve("V79__s6_7_cross_market_retirement.sql")

    @Test
    fun `V78 remains byte stable and V79 removes only runtime capabilities`() {
        val historicalHash =
            HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(historicalMigration)),
            )
        assertThat(historicalHash).isEqualTo("fec3b51f15031c7980fcc1265f42eb8bde530b981618349516c49256f8bf14ac")

        val retirement = Files.readString(retirementMigration)
        assertThat(retirement).contains(
            "REVOKE ALL PRIVILEGES ON FUNCTION public.append_cross_market_risk_snapshot_v2",
            "REVOKE ALL PRIVILEGES ON FUNCTION public.read_cross_market_decision_input_v2",
            "DROP FUNCTION public.append_cross_market_risk_snapshot_v2",
            "DROP FUNCTION public.read_cross_market_decision_input_v2",
            "REVOKE ALL PRIVILEGES ON TABLE public.cross_market_risk_snapshots_v2",
            "HISTORICAL_ONLY",
        )
        assertThat(retirement).doesNotContain(
            "DROP TABLE",
            "TRUNCATE",
            "UPDATE public.cross_market_risk_snapshots_v2",
            "DELETE FROM public.cross_market_risk_snapshots_v2",
        )
    }

    @Test
    fun `S6 7 runtime source configuration and bean classes are absent`() {
        val application = Files.readString(Path.of("src/main/resources/application.yml"))
        assertThat(application).doesNotContain(
            "cross-market:",
            "CROSS_MARKET_OVERLAY_MODE",
            "CROSS_MARKET_THRESHOLD_PERCENTILE",
        )
        assertThat(
            Path.of(
                "src/main/kotlin/com/capstone/decision/application/risk/crossmarket/v2/CrossMarketRiskOverlay.kt",
            ),
        ).doesNotExist()
        assertThat(
            Path.of(
                "src/main/kotlin/com/capstone/decision/application/risk/crossmarket/v2/CrossMarketRiskPort.kt",
            ),
        ).doesNotExist()
        assertThat(
            Path.of("src/main/kotlin/com/capstone/decision/infrastructure/risk/CrossMarketRuntimeConfiguration.kt"),
        ).doesNotExist()
        assertThat(
            Path.of("src/main/kotlin/com/capstone/decision/infrastructure/risk/JdbcCrossMarketRiskV2Adapter.kt"),
        ).doesNotExist()
    }
}
