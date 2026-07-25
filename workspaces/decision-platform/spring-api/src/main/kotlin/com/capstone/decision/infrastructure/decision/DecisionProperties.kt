package com.capstone.decision.infrastructure.decision

import jakarta.validation.constraints.Max
import jakarta.validation.constraints.Min
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.validation.annotation.Validated

@ConfigurationProperties("app.decision")
@Validated
data class DecisionProperties(
    @field:Min(10)
    @field:Max(10)
    var validMinutes: Long = 10,
    var idempotencyScopeHmacKey: String = "",
    @field:Min(2)
    @field:Max(60)
    var claimTtlSeconds: Long = 30,
) {
    fun validate() {
        require(validMinutes == 10L)
        require(idempotencyScopeHmacKey.toByteArray(Charsets.UTF_8).size >= 32) {
            "Decision idempotency scope HMAC key must be at least 32 bytes."
        }
        require(claimTtlSeconds in 2..60)
    }

    companion object {
        const val PURPOSE_VERSION = "decision-evaluate-order/v1"
    }
}
