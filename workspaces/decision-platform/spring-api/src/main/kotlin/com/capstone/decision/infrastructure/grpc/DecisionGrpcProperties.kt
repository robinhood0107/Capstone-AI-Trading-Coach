package com.capstone.decision.infrastructure.grpc

import jakarta.validation.constraints.Max
import jakarta.validation.constraints.Min
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.validation.annotation.Validated

@ConfigurationProperties("app.decision.grpc")
@Validated
data class DecisionGrpcProperties(
    var target: String = "127.0.0.1:50051",
    @field:Min(1)
    @field:Max(2_000)
    var hardDeadlineMillis: Long = 2_000,
    @field:Min(1)
    @field:Max(500)
    var sourceDeadlineMillis: Long = 500,
    @field:Min(1)
    @field:Max(900)
    var totalEvaluationDeadlineMillis: Long = 900,
    @field:Min(1_024)
    @field:Max(262_144)
    var requestMaxBytes: Int = 262_144,
    @field:Min(1_024)
    @field:Max(1_048_576)
    var responseMaxBytes: Int = 1_048_576,
    @field:Min(1)
    @field:Max(8)
    var concurrencyMax: Int = 8,
) {
    /**
     * mTLS가 없는 v1 transport는 numeric loopback만 허용해 Pod/namespace 밖 노출을 startup에서 차단한다.
     */
    fun validate() {
        require(NUMERIC_LOOPBACK.matches(target)) {
            "Decision gRPC target must be a numeric loopback address."
        }
        val port =
            target
                .substringAfterLast(':')
                .toIntOrNull()
                ?: throw IllegalArgumentException("Decision gRPC target port is invalid.")
        require(port in 1..65_535) { "Decision gRPC target port is invalid." }
        require(sourceDeadlineMillis <= hardDeadlineMillis)
        require(sourceDeadlineMillis <= totalEvaluationDeadlineMillis)
        require(requestMaxBytes == 262_144)
        require(responseMaxBytes == 1_048_576)
        require(concurrencyMax <= 8)
    }

    private companion object {
        val NUMERIC_LOOPBACK = Regex("""(?:127\.0\.0\.1:\d{1,5}|\[::1]:\d{1,5})""")
    }
}
