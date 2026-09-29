package com.capstone.decision.infrastructure.security

import org.springframework.security.crypto.password.PasswordEncoder
import org.springframework.stereotype.Service
import java.nio.charset.StandardCharsets
import java.security.SecureRandom
import java.time.Duration
import java.util.Base64

internal val MAX_ACTOR_SESSION_TTL: Duration = Duration.ofDays(7)

// demo login도 DB users를 source of truth로 사용해 이후 owner FK와 같은 user_id namespace를 보장한다.
@Service
class DemoAccountService(
    private val userSecurityRepository: UserSecurityRepository,
    private val passwordEncoder: PasswordEncoder,
    private val jwtProperties: JwtProperties,
) {
    // unknown username도 동일 cost의 BCrypt 경로를 지나 username enumeration timing 차이를 줄인다.
    private val dummyPassword: String = randomDummyPassword()
    private val dummyPasswordHash: String =
        DemoCredentialHashPolicy.requireValid(
            requireNotNull(passwordEncoder.encode(dummyPassword)),
        )

    fun authenticate(
        username: String,
        password: String,
    ): AuthenticatedAccount? =
        authenticate(
            username = username,
            password = password,
            sessionTtl = Duration.ofHours(jwtProperties.ttlHours),
        )

    // OAuth refresh family처럼 login 종류가 더 오래 지속되면 같은 DB session도 그 계약만큼만 연장한다.
    fun authenticate(
        username: String,
        password: String,
        sessionTtl: Duration,
    ): AuthenticatedAccount? {
        require(!sessionTtl.isZero && !sessionTtl.isNegative && sessionTtl <= MAX_ACTOR_SESSION_TTL)
        // V213 이후 고정 비밀번호 계정은 demo-user 하나다. 역할은 DB 행이 정하며 USER·ADMIN 모두 허용한다.
        val operatorIdentity = requireNotNull(DemoAccounts.byUserId(DemoOperatorAccountPolicy.OPERATOR_USER_ID))
        val expectedIdentity = operatorIdentity.takeIf { it.username == username }
        val storedUsers = userSecurityRepository.findDemoCredentials()
        val operatorRow =
            storedUsers
                .singleOrNull { it.userId == operatorIdentity.userId }
                ?.takeIf { it.matchesOperator(operatorIdentity) }
        val storedUser = expectedIdentity?.let { operatorRow }

        // BCrypt 검증은 72-byte 초과 입력도 접두사와 일치시킬 수 있으므로 DTO의 문자 수 제한과 별도로 byte 경계를 잠근다.
        val passwordBytes = password.toByteArray(StandardCharsets.UTF_8)
        val passwordWithinBcryptBoundary =
            try {
                passwordBytes.size in 1..MAX_BCRYPT_PASSWORD_BYTES
            } finally {
                passwordBytes.fill(0)
            }
        val verificationPassword = if (passwordWithinBcryptBoundary) password else dummyPassword

        // 알려진 계정, unknown 계정, overlong 입력 모두 정확히 두 번의 BCrypt cost를 지불한다.
        val selectedMatches = passwordEncoder.matches(verificationPassword, storedUser?.passwordHash ?: dummyPasswordHash)
        passwordEncoder.matches(verificationPassword, dummyPasswordHash)
        if (
            expectedIdentity == null ||
            storedUser == null ||
            !passwordWithinBcryptBoundary ||
            !selectedMatches
        ) {
            return null
        }
        val session =
            userSecurityRepository.createAuthenticatedSession(
                username = username,
                password = password,
                ttlSeconds = Math.toIntExact(sessionTtl.seconds),
            ) ?: return null
        if (
            session.userId != storedUser.userId ||
            session.username != storedUser.username ||
            session.role != storedUser.role ||
            session.securityVersion != storedUser.securityVersion
        ) {
            return null
        }
        return AuthenticatedAccount(
            userId = session.userId,
            username = session.username,
            role = session.role,
            securityVersion = session.securityVersion,
            sessionHandle = session.sessionHandle,
            expiresAt = session.expiresAt,
        )
    }

    private fun randomDummyPassword(): String {
        val bytes = ByteArray(32)
        SecureRandom().nextBytes(bytes)
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    }

    companion object {
        private const val ACTIVE_STATUS = "ACTIVE"
        private const val MAX_BCRYPT_PASSWORD_BYTES = 72
    }

    private fun UserSecurityRecord.matchesOperator(identity: DemoAccountIdentity): Boolean =
        userId == identity.userId &&
            username == identity.username &&
            status == ACTIVE_STATUS &&
            securityVersion > 0 &&
            DemoCredentialHashPolicy.isValid(passwordHash)
}

// S0.3 권한 테스트는 USER와 ADMIN의 최소 역할 차이만 확인한다.
enum class DemoRole {
    USER,
    ADMIN,
}
