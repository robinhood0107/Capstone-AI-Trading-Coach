package com.capstone.decision.infrastructure.security

import org.springframework.security.crypto.password.PasswordEncoder
import org.springframework.stereotype.Service
import java.security.SecureRandom
import java.util.Base64

// demo login도 DB users를 source of truth로 사용해 이후 owner FK와 같은 user_id namespace를 보장한다.
@Service
class DemoAccountService(
    private val userSecurityRepository: UserSecurityRepository,
    private val passwordEncoder: PasswordEncoder,
) {
    // unknown username도 동일 cost의 BCrypt 경로를 지나 username enumeration timing 차이를 줄인다.
    private val dummyPasswordHash: String =
        DemoCredentialHashPolicy.requireValid(
            requireNotNull(passwordEncoder.encode(randomDummyPassword())),
        )

    fun authenticate(
        username: String,
        password: String,
    ): DemoAccount? {
        val expectedIdentity = DemoAccounts.byUsername(username)
        // unknown username도 동일한 DB lookup과 BCrypt 검증을 거쳐 존재 여부 timing 단서를 줄인다.
        val storedUser = userSecurityRepository.findByUsername(username)
        val storedHashValid = storedUser?.passwordHash?.let(DemoCredentialHashPolicy::isValid) == true
        val hash = storedUser?.passwordHash?.takeIf { storedHashValid } ?: dummyPasswordHash
        val passwordMatches = passwordEncoder.matches(password, hash)
        if (
            expectedIdentity == null ||
            storedUser == null ||
            !storedHashValid ||
            !passwordMatches ||
            storedUser.status != ACTIVE_STATUS ||
            storedUser.securityVersion <= 0 ||
            storedUser.userId != expectedIdentity.userId ||
            storedUser.username != expectedIdentity.username ||
            storedUser.role != expectedIdentity.role
        ) {
            return null
        }
        return DemoAccount(
            userId = storedUser.userId,
            username = storedUser.username,
            role = storedUser.role,
            securityVersion = storedUser.securityVersion,
        )
    }

    private fun randomDummyPassword(): String {
        val bytes = ByteArray(32)
        SecureRandom().nextBytes(bytes)
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    }

    companion object {
        private const val ACTIVE_STATUS = "ACTIVE"
    }
}

// demo 인증 결과와 JWT claim 생성에 필요한 값만 담아 민감정보 범위를 좁힌다.
data class DemoAccount(
    val userId: String,
    val username: String,
    val role: DemoRole,
    val securityVersion: Long,
)

// S0.3 권한 테스트는 USER와 ADMIN의 최소 역할 차이만 확인한다.
enum class DemoRole {
    USER,
    ADMIN,
}
