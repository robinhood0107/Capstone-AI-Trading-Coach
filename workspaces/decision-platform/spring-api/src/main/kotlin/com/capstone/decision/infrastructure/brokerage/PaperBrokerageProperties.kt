package com.capstone.decision.infrastructure.brokerage

import jakarta.validation.constraints.Max
import jakarta.validation.constraints.Min
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.validation.annotation.Validated

@ConfigurationProperties("app.brokerage.paper")
@Validated
data class PaperBrokerageProperties(
    @field:Min(0)
    @field:Max(100)
    var slippageBps: Int = 5,
    @field:Min(1)
    @field:Max(300)
    var priceMaxAgeSeconds: Int = 300,
) {
    fun validate() {
        require(slippageBps in 0..100)
        require(priceMaxAgeSeconds in 1..300)
    }
}
