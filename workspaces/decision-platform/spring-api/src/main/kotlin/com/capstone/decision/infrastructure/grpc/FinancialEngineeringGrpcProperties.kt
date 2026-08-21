package com.capstone.decision.infrastructure.grpc

import org.springframework.boot.context.properties.ConfigurationProperties

@ConfigurationProperties("app.financial-engineering.grpc")
data class FinancialEngineeringGrpcProperties(
    var enabled: Boolean = false,
    var target: String = "127.0.0.1:50054",
    var sharedSecret: String = "",
    var deadlineMillis: Long = 2_000,
    var requestMaxBytes: Int = 65_536,
    var responseMaxBytes: Int = 65_536,
    var concurrencyMax: Int = 8,
    var retryCount: Int = 0,
) {
    fun validateEnabled() {
        require(enabled)
        require(LOOPBACK.matches(target))
        require(SAFE_SECRET.matches(sharedSecret))
        require(deadlineMillis in 1..2_000)
        require(requestMaxBytes == 65_536 && responseMaxBytes == 65_536)
        require(concurrencyMax in 1..8 && retryCount == 0)
    }

    private companion object {
        val LOOPBACK = Regex("(?:127\\.0\\.0\\.1:\\d{1,5}|\\[::1]:\\d{1,5})")
        val SAFE_SECRET = Regex("[A-Za-z0-9._~:-]{32,256}")
    }
}
