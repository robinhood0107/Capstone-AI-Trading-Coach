package com.capstone.decision.infrastructure.security

import io.jsonwebtoken.Jwts
import io.jsonwebtoken.security.Keys
import org.springframework.stereotype.Service
import java.time.Clock
import java.time.OffsetDateTime
import java.util.Date
import javax.crypto.SecretKey

@Service
class JwtService(
    private val properties: JwtProperties,
) {
    private val clock: Clock = Clock.systemUTC()

    fun issue(account: DemoAccount): IssuedToken {
        val now = OffsetDateTime.now(clock)
        val expiresAt = now.plusHours(properties.ttlHours)
        val token =
            Jwts
                .builder()
                .subject(account.username)
                .claim("userId", account.userId)
                .claim("role", account.role.name)
                .issuedAt(Date.from(now.toInstant()))
                .expiration(Date.from(expiresAt.toInstant()))
                .signWith(signingKey())
                .compact()
        return IssuedToken(token = token, expiresAt = expiresAt)
    }

    fun parse(token: String): AppPrincipal {
        val claims =
            Jwts
                .parser()
                .verifyWith(signingKey())
                .build()
                .parseSignedClaims(token)
                .payload
        val role = DemoRole.valueOf(claims["role"] as String)
        return AppPrincipal(
            userId = claims["userId"] as String,
            username = claims.subject,
            role = role,
        )
    }

    private fun signingKey(): SecretKey {
        require(properties.secret.toByteArray().size >= 32) {
            "app.jwt.secret must be at least 32 bytes for HS256."
        }
        return Keys.hmacShaKeyFor(properties.secret.toByteArray())
    }
}

data class IssuedToken(
    val token: String,
    val expiresAt: OffsetDateTime,
)
