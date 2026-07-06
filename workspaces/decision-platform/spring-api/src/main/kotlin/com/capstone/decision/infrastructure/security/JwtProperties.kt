package com.capstone.decision.infrastructure.security

import org.springframework.boot.context.properties.ConfigurationProperties

@ConfigurationProperties("app.jwt")
data class JwtProperties(
    var secret: String = "",
    var ttlHours: Long = 12,
)
