package com.capstone.decision.infrastructure.grpc

import jakarta.validation.constraints.Max
import jakarta.validation.constraints.Min
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.validation.annotation.Validated
import java.nio.charset.StandardCharsets
import java.security.MessageDigest

@ConfigurationProperties("app.rag-v2.grpc")
@Validated
data class RagV2GrpcProperties(
    var enabled: Boolean = false,
    var target: String = "127.0.0.1:50054",
    var sharedSecret: String = "",
    @field:Min(1)
    @field:Max(15_000)
    var deadlineMillis: Long = 15_000,
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
     * v2 process는 v1 port와 별도 numeric loopback/secret으로만 연결하고 retry를 만들지 않는다.
     */
    fun validate() {
        require(NUMERIC_LOOPBACK.matches(target)) {
            "RAG v2 gRPC target must be a numeric loopback address."
        }
        require(SHARED_SECRET.matches(sharedSecret)) {
            "RAG v2 gRPC shared secret must be 32..256 safe ASCII characters."
        }
        val port =
            target
                .substringAfterLast(':')
                .toIntOrNull()
                ?: throw IllegalArgumentException("RAG v2 gRPC target port is invalid.")
        require(port in 1..65_535) { "RAG v2 gRPC target port is invalid." }
        require(deadlineMillis in 1..15_000)
        require(requestMaxBytes == 65_536)
        require(responseMaxBytes == 262_144)
        require(concurrencyMax in 1..8)
        require(retryCount == 0)
    }

    /**
     * enabled v2 channel cannot reuse Decision or legacy v1 RAG wire credentials.
     */
    fun validatePurposeSeparation(
        decisionGrpcProperties: DecisionGrpcProperties,
        ragGrpcProperties: RagGrpcProperties,
    ) {
        validate()
        decisionGrpcProperties.validate()
        val v2Secret = sharedSecret.toByteArray(StandardCharsets.UTF_8)
        val decisionSecret = decisionGrpcProperties.sharedSecret.toByteArray(StandardCharsets.UTF_8)
        try {
            require(!MessageDigest.isEqual(v2Secret, decisionSecret)) {
                "RAG v2 gRPC shared secret must be purpose-separated from Decision gRPC."
            }
            if (ragGrpcProperties.enabled) {
                ragGrpcProperties.validate()
                val v1Secret = ragGrpcProperties.sharedSecret.toByteArray(StandardCharsets.UTF_8)
                try {
                    require(!MessageDigest.isEqual(v2Secret, v1Secret)) {
                        "RAG v2 gRPC shared secret must be purpose-separated from RAG v1 gRPC."
                    }
                } finally {
                    v1Secret.fill(0)
                }
            }
        } finally {
            v2Secret.fill(0)
            decisionSecret.fill(0)
        }
    }

    private companion object {
        val NUMERIC_LOOPBACK = Regex("""(?:127\.0\.0\.1:\d{1,5}|\[::1]:\d{1,5})""")
        val SHARED_SECRET = Regex("""[A-Za-z0-9._~:-]{32,256}""")
    }
}
