package com.capstone.decision.infrastructure.grpc

import org.springframework.boot.context.properties.ConfigurationProperties

/** Strong LLM Python process는 numeric loopback과 purpose-separated secret만 허용한다. */
@ConfigurationProperties("app.s4-9.strong-llm.grpc")
data class StrongLlmAgentGrpcProperties(
    var target: String = "127.0.0.1:50055",
    var sharedSecret: String = "",
    var deadlineMillis: Long = 45_000,
    var requestMaxBytes: Int = 262_144,
    var responseMaxBytes: Int = 262_144,
) {
    fun validate(enabled: Boolean) {
        if (!enabled) return
        require(NUMERIC_LOOPBACK.matches(target))
        require(sharedSecret.matches(SAFE_SECRET))
        require(deadlineMillis in 1_000..60_000)
        require(requestMaxBytes in 65_536..262_144 && responseMaxBytes in 65_536..262_144)
    }

    private companion object {
        val NUMERIC_LOOPBACK = Regex("^127[.]0[.]0[.]1:[1-9][0-9]{3,4}$")
        val SAFE_SECRET = Regex("^[A-Za-z0-9._~:-]{32,256}$")
    }
}
