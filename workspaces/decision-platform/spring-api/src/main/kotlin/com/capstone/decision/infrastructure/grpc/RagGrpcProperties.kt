package com.capstone.decision.infrastructure.grpc

import jakarta.validation.constraints.Max
import jakarta.validation.constraints.Min
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.validation.annotation.Validated
import java.nio.charset.StandardCharsets
import java.security.MessageDigest

@ConfigurationProperties("app.rag.grpc")
@Validated
data class RagGrpcProperties(
    var enabled: Boolean = false,
    var target: String = "127.0.0.1:50053",
    var sharedSecret: String = "",
    @field:Min(1)
    @field:Max(15_000)
    var deadlineMillis: Long = 15_000,
    @field:Min(17_000)
    @field:Max(17_000)
    var readTimeoutMillis: Long = 17_000,
    @field:Min(65_536)
    @field:Max(65_536)
    var requestMaxBytes: Int = 65_536,
    @field:Min(262_144)
    @field:Max(262_144)
    var responseMaxBytes: Int = 262_144,
    @field:Min(1)
    @field:Max(8)
    var concurrencyMax: Int = 8,
    @field:Min(0)
    @field:Max(0)
    var retryCount: Int = 0,
) {
    /**
     * mTLS 전 S4.6 transport는 numeric loopback과 고정 상한, 무재시도만 허용한다.
     */
    fun validate() {
        require(NUMERIC_LOOPBACK.matches(target)) {
            "RAG gRPC target must be a numeric loopback address."
        }
        require(SHARED_SECRET.matches(sharedSecret)) {
            "RAG gRPC shared secret must be 32..256 safe ASCII characters."
        }
        val port =
            target
                .substringAfterLast(':')
                .toIntOrNull()
                ?: throw IllegalArgumentException("RAG gRPC target port is invalid.")
        require(port in 1..65_535) { "RAG gRPC target port is invalid." }
        require(deadlineMillis in 1..15_000)
        require(readTimeoutMillis == 17_000L && readTimeoutMillis > deadlineMillis)
        require(requestMaxBytes == 65_536)
        require(responseMaxBytes == 262_144)
        require(concurrencyMax in 1..8)
        require(retryCount == 0)
    }

    /**
     * enabled RAG transport가 Decision/Disclosure credential을 재사용하지 않도록 channel 생성 전에 비교한다.
     */
    fun validatePurposeSeparation(decisionGrpcProperties: DecisionGrpcProperties) {
        validate()
        decisionGrpcProperties.validate()
        val ragSecret = sharedSecret.toByteArray(StandardCharsets.UTF_8)
        val decisionSecret = decisionGrpcProperties.sharedSecret.toByteArray(StandardCharsets.UTF_8)
        try {
            require(!MessageDigest.isEqual(ragSecret, decisionSecret)) {
                "RAG gRPC shared secret must be purpose-separated from Decision gRPC."
            }
        } finally {
            ragSecret.fill(0)
            decisionSecret.fill(0)
        }
    }

    private companion object {
        val NUMERIC_LOOPBACK = Regex("""(?:127\.0\.0\.1:\d{1,5}|\[::1]:\d{1,5})""")
        val SHARED_SECRET = Regex("""[A-Za-z0-9._~:-]{32,256}""")
    }
}
