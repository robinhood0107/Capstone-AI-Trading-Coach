package com.capstone.decision.infrastructure.idempotency

import org.springframework.boot.context.properties.ConfigurationProperties

// 멱등성 대상 path와 TTL은 향후 주문/원칙/백테스트 API 추가 때 설정으로 조정 가능해야 한다.
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
