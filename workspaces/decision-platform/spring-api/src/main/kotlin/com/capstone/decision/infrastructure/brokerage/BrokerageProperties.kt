package com.capstone.decision.infrastructure.brokerage

import jakarta.validation.constraints.Max
import jakarta.validation.constraints.Min
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.validation.annotation.Validated

@ConfigurationProperties("app.brokerage")
@Validated
data class BrokerageProperties(
    var idempotencyScopeHmacKey: String = "",
    @field:Min(1)
    @field:Max(168)
    var idempotencyTtlHours: Long = 24,
) {
    fun validate() {
        require(idempotencyScopeHmacKey.toByteArray(Charsets.UTF_8).size >= 32) {
            "Brokerage idempotency scope HMAC key must be at least 32 bytes."
        }
        require(idempotencyTtlHours in 1..168)
    }

    companion object {
        const val PURPOSE_VERSION = "brokerage-mock-order/v1"
    }
}
