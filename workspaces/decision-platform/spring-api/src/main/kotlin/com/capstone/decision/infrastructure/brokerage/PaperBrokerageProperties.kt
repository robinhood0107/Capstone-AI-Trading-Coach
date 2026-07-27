package com.capstone.decision.infrastructure.brokerage

import com.capstone.decision.application.brokerage.paper.PaperExecutionPolicy
import jakarta.validation.constraints.Max
import jakarta.validation.constraints.Min
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.validation.annotation.Validated

@ConfigurationProperties("app.brokerage.paper")
@Validated
data class PaperBrokerageProperties(
    @field:Min(0)
    @field:Max(100)
    override var slippageBps: Int = 5,
    @field:Min(1)
    @field:Max(300)
    override var priceMaxAgeSeconds: Int = 300,
) : PaperExecutionPolicy {
    fun validate() {
        require(slippageBps in 0..100)
        require(priceMaxAgeSeconds in 1..300)
    }
}
