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
        val storedUsers = userSecurityRepository.findDemoCredentials()
        val verifiedRows =
            DemoAccounts.identities.associateWith { identity ->
                storedUsers
                    .singleOrNull { it.userId == identity.userId }
                    ?.takeIf { it.matches(identity) }
            }
        val storedUser = expectedIdentity?.let(verifiedRows::get)
        val peer =
            expectedIdentity
                ?.let { selected -> DemoAccounts.identities.single { it.userId != selected.userId } }
                ?.let(verifiedRows::get)

        // 알려진 계정과 unknown 계정 모두 정확히 두 번의 BCrypt cost를 지불하며 peer plaintext 일치도 fail-closed한다.
        val selectedMatches = passwordEncoder.matches(password, storedUser?.passwordHash ?: dummyPasswordHash)
        val peerMatches = passwordEncoder.matches(password, peer?.passwordHash ?: dummyPasswordHash)
        val trustRootComplete = storedUsers.size == DemoAccounts.identities.size && verifiedRows.values.all { it != null }
        if (
            expectedIdentity == null ||
            storedUser == null ||
            !trustRootComplete ||
            !selectedMatches ||
            peerMatches
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

    private fun UserSecurityRecord.matches(identity: DemoAccountIdentity): Boolean =
        userId == identity.userId &&
            username == identity.username &&
            role == identity.role &&
            status == ACTIVE_STATUS &&
            securityVersion > 0 &&
            DemoCredentialHashPolicy.isValid(passwordHash)
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
