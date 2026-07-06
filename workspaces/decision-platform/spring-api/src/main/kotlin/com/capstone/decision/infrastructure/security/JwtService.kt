package com.capstone.decision.infrastructure.security

import io.jsonwebtoken.Jwts
import io.jsonwebtoken.security.Keys
import org.springframework.stereotype.Service
import java.time.Clock
import java.time.OffsetDateTime
import java.util.Date
import javax.crypto.SecretKey

// token 발급/검증 책임을 분리해 controller와 filter가 JWT 구현 세부사항을 모르도록 한다.
@Service
class JwtService(
    private val properties: JwtProperties,
) {
    // 테스트에서 토큰 만료 계산이 한 지점으로 모이도록 Clock을 명시한다.
    private val clock: Clock = Clock.systemUTC()

    fun issue(account: DemoAccount): IssuedToken {
        val now = OffsetDateTime.now(clock)
        val expiresAt = now.plusHours(properties.ttlHours)
        val token =
            // JWT에는 userId와 role만 담아 민감정보가 토큰 payload에 들어가지 않게 한다.
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
            // 같은 signing key로 검증한 토큰만 SecurityContext에 올려 forged token을 차단한다.
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
        // HS256은 짧은 secret을 허용하면 brute force 위험이 커서 시작 시 바로 실패시킨다.
        require(properties.secret.toByteArray().size >= 32) {
            "app.jwt.secret must be at least 32 bytes for HS256."
        }
        return Keys.hmacShaKeyFor(properties.secret.toByteArray())
    }
}

// swagger smoke가 만료 시각까지 확인할 수 있도록 발급 결과를 명시적으로 묶는다.
data class IssuedToken(
    val token: String,
    val expiresAt: OffsetDateTime,
)
