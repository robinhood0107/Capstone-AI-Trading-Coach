package com.capstone.decision.infrastructure.security

import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.application.security.AuthenticatedActorRef
import io.jsonwebtoken.JwtException
import io.jsonwebtoken.Jwts
import io.jsonwebtoken.security.Keys
import org.springframework.stereotype.Service
import java.nio.charset.StandardCharsets
import java.time.Clock
import java.time.OffsetDateTime
import java.util.Date
import javax.crypto.SecretKey

// token 발급/검증 책임을 분리해 controller와 filter가 JWT 구현 세부사항을 모르도록 한다.
@Service
class JwtService(
    private val properties: JwtProperties,
    private val userSecurityRepository: UserSecurityRepository,
) {
    // 테스트에서 토큰 만료 계산이 한 지점으로 모이도록 Clock을 명시한다.
    private val clock: Clock = Clock.systemUTC()

    init {
        properties.validate()
    }

    fun issue(account: DemoAccount): IssuedToken {
        val now = OffsetDateTime.now(clock)
        val expiresAt = account.expiresAt
        require(expiresAt.isAfter(now) && !expiresAt.isAfter(now.plusHours(properties.ttlHours).plusSeconds(5)))
        val token =
            // JWT에는 userId와 role만 담아 민감정보가 토큰 payload에 들어가지 않게 한다.
            Jwts
                .builder()
                .issuer(properties.issuer)
                .audience()
                .add(properties.audience)
                .and()
                .subject(account.userId)
                .claim("role", account.role.name)
                .claim("securityVersion", account.securityVersion)
                .claim("sid", account.sessionHandle)
                .issuedAt(Date.from(now.toInstant()))
                .expiration(Date.from(expiresAt.toInstant()))
                .signWith(signingKey(), Jwts.SIG.HS256)
                .compact()
        return IssuedToken(token = token, expiresAt = expiresAt)
    }

    fun parse(token: String): AppPrincipal {
        // 허용 algorithm을 HS256 하나로 제한하고 issuer/audience/signature/exp를 parser에서 먼저 검증한다.
        val parser =
            Jwts.parser().keyLocator { header ->
                if (header.algorithm != Jwts.SIG.HS256.id) {
                    throw JwtException("Unsupported JWT algorithm.")
                }
                signingKey()
            }
        val signedClaims =
            parser
                .requireIssuer(properties.issuer)
                .requireAudience(properties.audience)
                .build()
                .parseSignedClaims(token)
        if (signedClaims.header.algorithm != Jwts.SIG.HS256.id) {
            throw JwtException("Unsupported JWT algorithm.")
        }
        val claims = signedClaims.payload
        if (claims.audience != setOf(properties.audience)) {
            throw JwtException("JWT audience is invalid.")
        }
        val subject =
            claims.subject?.takeIf { it.isNotBlank() && it.length <= 128 }
                ?: throw JwtException("JWT subject is invalid.")
        val issuedAt = claims.issuedAt?.toInstant() ?: throw JwtException("JWT issued-at is required.")
        val expiresAt = claims.expiration?.toInstant() ?: throw JwtException("JWT expiration is required.")
        val now = clock.instant()
        if (issuedAt.isAfter(now.plusSeconds(MAX_FUTURE_ISSUED_AT_SECONDS)) || !expiresAt.isAfter(issuedAt)) {
            throw JwtException("JWT temporal claims are invalid.")
        }
        val claimedRole = parseRole(claims["role"])
        val claimedSecurityVersion = parseSecurityVersion(claims["securityVersion"])
        val sessionHandle =
            (claims["sid"] as? String)?.takeIf { it.matches(SESSION_HANDLE) }
                ?: throw JwtException("JWT session is invalid.")
        val storedUser =
            userSecurityRepository.findBySessionHandle(sessionHandle)
                ?: throw JwtException("JWT actor is invalid.")
        if (
            storedUser.userId != subject ||
            storedUser.role != claimedRole ||
            storedUser.securityVersion != claimedSecurityVersion ||
            storedUser.securityVersion <= 0
        ) {
            throw JwtException("JWT actor is invalid.")
        }
        return AppPrincipal(
            userId = storedUser.userId,
            username = storedUser.username,
            role = storedUser.role.name,
            securityVersion = storedUser.securityVersion,
            actorRef =
                AuthenticatedActorRef(
                    sessionHandle = sessionHandle,
                    expectedUserId = storedUser.userId,
                    securityVersion = storedUser.securityVersion,
                ),
        )
    }

    private fun parseRole(value: Any?): DemoRole =
        try {
            DemoRole.valueOf(value as? String ?: throw JwtException("JWT role is invalid."))
        } catch (exception: IllegalArgumentException) {
            throw JwtException("JWT role is invalid.", exception)
        }

    private fun parseSecurityVersion(value: Any?): Long {
        val number = value as? Number ?: throw JwtException("JWT security version is invalid.")
        val parsed = number.toLong()
        if (parsed <= 0 || number.toDouble() != parsed.toDouble()) {
            throw JwtException("JWT security version is invalid.")
        }
        return parsed
    }

    private fun signingKey(): SecretKey {
        // HS256은 짧은 secret을 허용하면 brute force 위험이 커서 시작 시 바로 실패시킨다.
        return Keys.hmacShaKeyFor(properties.secret.toByteArray(StandardCharsets.UTF_8))
    }

    companion object {
        private const val MAX_FUTURE_ISSUED_AT_SECONDS = 60L
        private val SESSION_HANDLE = Regex("^sid1_[0-9a-f]{64}$")
    }
}

// swagger smoke가 만료 시각까지 확인할 수 있도록 발급 결과를 명시적으로 묶는다.
data class IssuedToken(
    val token: String,
    val expiresAt: OffsetDateTime,
)
