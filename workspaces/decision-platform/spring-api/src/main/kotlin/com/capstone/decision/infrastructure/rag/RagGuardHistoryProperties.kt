package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagGuardHistoryPolicy
import jakarta.validation.constraints.Max
import jakarta.validation.constraints.Min
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.validation.annotation.Validated

@ConfigurationProperties("app.rag")
@Validated
data class RagGuardHistoryProperties(
    var answerer: String = "FIXTURE_ONLY",
    var historySecretDirectory: String = "",
    var currentKekVersion: String = "kek-v1",
    var idempotencyScopeHmacKey: String = "",
    var requestFingerprintHmacKey: String = "",
    var providerUsageHmacKey: String = "",
    var rateLimitHmacKey: String = "",
    var historyCursorHmacKey: String = "",
    @field:Min(30)
    @field:Max(300)
    override var claimTtlSeconds: Long = 120,
    @field:Min(30)
    @field:Max(30)
    var retentionDays: Long = 30,
    @field:Min(1)
    @field:Max(100)
    var rateLimitPerMinute: Long = 20,
) : RagGuardHistoryPolicy {
    fun validate() {
        require(answerer == "FIXTURE_ONLY") {
            "S4.4 answerer must remain FIXTURE_ONLY."
        }
        require(KEK_VERSION.matches(currentKekVersion)) {
            "RAG history KEK version is invalid."
        }
        require(historySecretDirectory.isNotBlank()) {
            "RAG history secret directory is required."
        }
        val namedKeys =
            linkedMapOf(
                "idempotencyScope" to idempotencyScopeHmacKey,
                "requestFingerprint" to requestFingerprintHmacKey,
                "providerUsage" to providerUsageHmacKey,
                "rateLimit" to rateLimitHmacKey,
                "historyCursor" to historyCursorHmacKey,
            )
        namedKeys.forEach { (name, value) ->
            require(value.toByteArray(Charsets.UTF_8).size >= 32) {
                "RAG $name HMAC key must be at least 32 bytes."
            }
        }
        require(namedKeys.values.toSet().size == namedKeys.size) {
            "RAG HMAC keys must be purpose-separated."
        }
        require(claimTtlSeconds in 30..300)
        require(retentionDays == 30L)
        require(rateLimitPerMinute in 1..100)
    }

    companion object {
        const val SCOPE_PURPOSE = "rag-ask-idempotency-scope/v1"
        const val REQUEST_PURPOSE = "rag-ask-request-fingerprint/v1"
        const val RATE_PURPOSE = "rag-ask-rate-limit/v1"
        const val CURSOR_PURPOSE = "rag-history-cursor/v1"
        val KEK_VERSION = Regex("^kek-v[1-9][0-9]{0,8}$")
    }
}
