package com.capstone.decision.infrastructure.risk

import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketOverlayConfig
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketRiskOverlay
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketRiskPort
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketRuntimeMode
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import java.math.BigDecimal

@ConfigurationProperties("app.cross-market.overlay")
data class CrossMarketOverlayProperties(
    var mode: CrossMarketRuntimeMode = CrossMarketRuntimeMode.OFF,
    var thresholdPercentile: String = "",
    var thresholdArtifactHash: String = "",
    var configHash: String = "",
) {
    fun toConfig(): CrossMarketOverlayConfig {
        val threshold = thresholdPercentile.trim().takeIf(String::isNotEmpty)?.let(::parseThreshold)
        val thresholdHash = thresholdArtifactHash.trim().takeIf(String::isNotEmpty)
        val runtimeConfigHash = configHash.trim().takeIf(String::isNotEmpty)
        val supplied = listOf(threshold, thresholdHash, runtimeConfigHash).count { it != null }
        require(supplied == 0 || supplied == 3) { "FROZEN_THRESHOLD_CONFIG_INCOMPLETE" }
        require(mode == CrossMarketRuntimeMode.OFF || supplied == 3) { "FROZEN_THRESHOLD_REQUIRED" }
        return CrossMarketOverlayConfig(mode, threshold, thresholdHash, runtimeConfigHash)
    }

    private fun parseThreshold(value: String): BigDecimal =
        try {
            BigDecimal(value)
        } catch (_: NumberFormatException) {
            throw IllegalArgumentException("FROZEN_THRESHOLD_INVALID")
        }
}

/**
 * P1 runtime composition only. The existing Decision v1 exact-14 payload and decision semantic hash
 * remain unchanged until an additive public projection contract is approved.
 */
@Configuration
@EnableConfigurationProperties(CrossMarketOverlayProperties::class)
class CrossMarketRuntimeConfiguration {
    @Bean
    fun crossMarketRiskOverlay(
        port: CrossMarketRiskPort,
        properties: CrossMarketOverlayProperties,
    ): CrossMarketRiskOverlay = CrossMarketRiskOverlay(port, properties.toConfig())
}
