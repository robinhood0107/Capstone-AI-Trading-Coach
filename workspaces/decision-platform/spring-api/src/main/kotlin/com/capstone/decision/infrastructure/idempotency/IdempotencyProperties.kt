package com.capstone.decision.infrastructure.idempotency

import jakarta.validation.constraints.Max
import jakarta.validation.constraints.Min
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.validation.annotation.Validated

// 멱등성 대상 path와 TTL은 향후 주문/원칙/백테스트 API 추가 때 설정으로 조정 가능해야 한다.
@ConfigurationProperties("app.idempotency")
@Validated
data class IdempotencyProperties(
    @field:Min(1)
    @field:Max(168)
    var ttlHours: Long = 24,
    @field:Min(60)
    @field:Max(3600)
    var claimTtlSeconds: Long = 900,
    @field:Min(16)
    @field:Max(256)
    var maxKeyLength: Int = 128,
    @field:Min(256)
    @field:Max(10_485_760)
    var maxRequestBodyBytes: Int = 1_048_576,
    @field:Min(256)
    @field:Max(10_485_760)
    var maxResponseBodyBytes: Int = 4_194_304,
    var paths: List<String> =
        listOf(
            "/api/v1/orders/**",
            "/api/v1/principles/**",
            "/api/v1/backtests/**",
        ),
)
