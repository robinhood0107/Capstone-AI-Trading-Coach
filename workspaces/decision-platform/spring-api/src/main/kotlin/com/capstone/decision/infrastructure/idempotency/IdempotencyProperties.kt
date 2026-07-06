package com.capstone.decision.infrastructure.idempotency

import org.springframework.boot.context.properties.ConfigurationProperties

@ConfigurationProperties("app.idempotency")
data class IdempotencyProperties(
    var ttlHours: Long = 24,
    var paths: List<String> =
        listOf(
            "/api/v1/orders/**",
            "/api/v1/principles/**",
            "/api/v1/backtests/**",
        ),
)
