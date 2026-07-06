package com.capstone.decision.infrastructure.security

import org.springframework.boot.context.properties.ConfigurationProperties

// JWT secret과 만료 시간은 환경별로 달라지므로 코드 상수가 아니라 설정으로 둔다.
@ConfigurationProperties("app.jwt")
data class JwtProperties(
    var secret: String = "",
    var ttlHours: Long = 12,
)
