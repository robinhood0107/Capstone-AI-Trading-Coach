package com.capstone.decision.infrastructure.async

import org.springframework.boot.context.properties.ConfigurationProperties
import java.nio.charset.StandardCharsets
import java.time.Duration

enum class AsyncAdapterMode {
    DB,
    KAFKA,
}

@ConfigurationProperties("app.async")
data class AsyncProperties(
    val adapter: AsyncAdapterMode = AsyncAdapterMode.DB,
    val pollingEnabled: Boolean = true,
    val cursorHmacKey: String = "",
    val partitionHmacKey: String = "",
    val pollDelay: Duration = Duration.ofSeconds(5),
    val claimPageSize: Int = 100,
) {
    fun validate() {
        validateSecret("Async cursor", cursorHmacKey)
        validateSecret("Async partition", partitionHmacKey)
        require(cursorHmacKey != partitionHmacKey) { "Async cursor and partition keys must be purpose-separated." }
        require(pollDelay == Duration.ofSeconds(5)) { "Async polling delay is fixed at five seconds." }
        require(claimPageSize == 100) { "Async claim page size is fixed at 100." }
    }

    private fun validateSecret(
        label: String,
        value: String,
    ) {
        val bytes = value.toByteArray(StandardCharsets.UTF_8)
        try {
            require(bytes.size in 32..128 && value.none(Char::isISOControl)) {
                "$label HMAC key must be 32-128 non-control UTF-8 bytes."
            }
        } finally {
            bytes.fill(0)
        }
    }
}

@ConfigurationProperties("app.async.worker")
data class AsyncWorkerProperties(
    val enabled: Boolean = true,
    val jdbcUrl: String = "",
    val username: String = "decision_worker",
    val password: String = "",
    val grpcTarget: String = "127.0.0.1:50056",
    val grpcSharedSecret: String = "",
    val grpcDeadline: Duration = Duration.ofSeconds(30),
    val requestMaxBytes: Int = 65_536,
    val responseMaxBytes: Int = 4_096,
) {
    fun validate(adapter: AsyncAdapterMode) {
        if (!enabled || adapter != AsyncAdapterMode.DB) return
        require(jdbcUrl.startsWith("jdbc:postgresql://") && !jdbcUrl.contains(Regex("[\\r\\n]"))) {
            "DB async worker requires an explicit PostgreSQL JDBC URL."
        }
        require(username == "decision_worker") { "DB async worker must use the decision_worker role." }
        require(password.toByteArray(StandardCharsets.UTF_8).size in 16..256) {
            "DB async worker password must be injected from a secret store."
        }
        require(grpcTarget.startsWith("127.0.0.1:") || grpcTarget.startsWith("localhost:")) {
            "DB async worker gRPC must remain loopback-only."
        }
        require(grpcSharedSecret.toByteArray(StandardCharsets.UTF_8).size in 32..128) {
            "DB async worker gRPC secret must be 32-128 UTF-8 bytes."
        }
        require(grpcDeadline in Duration.ofSeconds(1)..Duration.ofSeconds(60))
        require(requestMaxBytes == 65_536 && responseMaxBytes == 4_096)
    }
}
