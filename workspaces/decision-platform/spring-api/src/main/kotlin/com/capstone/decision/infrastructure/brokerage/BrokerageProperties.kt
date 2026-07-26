package com.capstone.decision.infrastructure.brokerage

import jakarta.validation.constraints.Max
import jakarta.validation.constraints.Min
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.validation.annotation.Validated
import java.security.MessageDigest
import java.util.HexFormat

@ConfigurationProperties("app.brokerage")
@Validated
data class BrokerageProperties(
    var idempotencyScopeHmacKey: String = "",
    var databaseCapabilityToken: String = "",
    var databaseCapabilityTokenSha256: String = "",
    @field:Min(1)
    @field:Max(168)
    var idempotencyTtlHours: Long = 24,
) {
    fun validate() {
        require(idempotencyScopeHmacKey.toByteArray(Charsets.UTF_8).size >= 32) {
            "Brokerage idempotency scope HMAC key must be at least 32 bytes."
        }
        require(databaseCapabilityToken.toByteArray(Charsets.UTF_8).size >= 32) {
            "Brokerage database capability token must be at least 32 bytes."
        }
        require(DIGEST.matches(databaseCapabilityTokenSha256)) {
            "Brokerage database capability token digest must be lowercase SHA-256 hex."
        }
        val computedDigest =
            HexFormat
                .of()
                .formatHex(
                    MessageDigest
                        .getInstance("SHA-256")
                        .digest(databaseCapabilityToken.toByteArray(Charsets.UTF_8)),
                )
        require(
            MessageDigest.isEqual(
                computedDigest.toByteArray(Charsets.UTF_8),
                databaseCapabilityTokenSha256.toByteArray(Charsets.UTF_8),
            ),
        ) {
            "Brokerage database capability token and digest must match."
        }
        require(idempotencyTtlHours in 1..168)
    }

    companion object {
        const val PURPOSE_VERSION = "brokerage-mock-order/v1"
        private val DIGEST = Regex("^[0-9a-f]{64}$")
    }
}
