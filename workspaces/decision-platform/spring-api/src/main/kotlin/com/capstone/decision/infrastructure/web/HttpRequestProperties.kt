package com.capstone.decision.infrastructure.web

import jakarta.validation.constraints.Max
import jakarta.validation.constraints.Min
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.validation.annotation.Validated

// 인증·JSON binding보다 먼저 적용할 전역 request body 상한을 운영 설정으로 고정한다.
@ConfigurationProperties("app.http")
@Validated
data class HttpRequestProperties(
    @field:Min(256)
    @field:Max(10_485_760)
    var maxRequestBodyBytes: Int = 1_048_576,
)
