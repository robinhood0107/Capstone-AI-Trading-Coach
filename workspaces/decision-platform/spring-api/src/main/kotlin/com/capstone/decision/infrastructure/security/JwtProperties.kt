package com.capstone.decision.infrastructure.security

import org.springframework.boot.context.properties.ConfigurationProperties

// JWT secret과 만료 시간은 환경별로 달라지므로 코드 상수가 아니라 설정으로 둔다.
@ConfigurationProperties("app.jwt")
data class JwtProperties(
    var secret: String = "",
    var issuer: String = "",
    var audience: String = "",
    var ttlHours: Long = 12,
) {
    fun validate() {
        require(secret.toByteArray(Charsets.UTF_8).size >= 32) {
            "app.jwt.secret must be at least 32 bytes for HS256."
        }
        require(issuer.isNotBlank() && issuer == issuer.trim() && issuer.length <= 200) {
            "app.jwt.issuer must be an exact nonblank value of at most 200 characters."
        }
        require(audience.isNotBlank() && audience == audience.trim() && audience.length <= 200) {
            "app.jwt.audience must be an exact nonblank value of at most 200 characters."
        }
        require(ttlHours in 1..24) { "app.jwt.ttl-hours must be between 1 and 24." }
    }
}
