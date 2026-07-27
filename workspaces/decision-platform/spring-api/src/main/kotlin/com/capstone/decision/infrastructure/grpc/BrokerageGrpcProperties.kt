package com.capstone.decision.infrastructure.grpc

import jakarta.validation.constraints.Max
import jakarta.validation.constraints.Min
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.validation.annotation.Validated

@ConfigurationProperties("app.brokerage.grpc")
@Validated
data class BrokerageGrpcProperties(
    var enabled: Boolean = false,
    var target: String = "127.0.0.1:50052",
    var sharedSecret: String = "",
    @field:Min(1)
    @field:Max(60_000)
    var deadlineMillis: Long = 45_000,
    @field:Min(1_024)
    @field:Max(262_144)
    var requestMaxBytes: Int = 262_144,
    @field:Min(1_024)
    @field:Max(1_048_576)
    var responseMaxBytes: Int = 1_048_576,
    var circuitBreakerName: String = "kisMockBrokerage",
) {
    /**
     * S3.1 brokerage RPC는 provider proxy가 아니라 same-deployment loopback business RPC다.
     * mTLS 전까지 numeric loopback과 shared secret만 허용하고, live-order transport는 별도 gate가 열기 전까지 배선하지 않는다.
     */
    fun validate() {
        require(NUMERIC_LOOPBACK.matches(target)) {
            "Brokerage gRPC target must be a numeric loopback address."
        }
        require(SHARED_SECRET.matches(sharedSecret)) {
            "Brokerage gRPC shared secret must be 32..256 safe ASCII characters."
        }
        val port =
            target
                .substringAfterLast(':')
                .toIntOrNull()
                ?: throw IllegalArgumentException("Brokerage gRPC target port is invalid.")
        require(port in 1..65_535) { "Brokerage gRPC target port is invalid." }
        require(requestMaxBytes == 262_144)
        require(responseMaxBytes == 1_048_576)
        require(circuitBreakerName == "kisMockBrokerage")
        require(deadlineMillis in 1..60_000) {
            "Brokerage gRPC deadline must stay inside the bounded online envelope."
        }
    }

    private companion object {
        val NUMERIC_LOOPBACK = Regex("""(?:127\.0\.0\.1:\d{1,5}|\[::1]:\d{1,5})""")
        val SHARED_SECRET = Regex("""[A-Za-z0-9._~:-]{32,256}""")
    }
}
