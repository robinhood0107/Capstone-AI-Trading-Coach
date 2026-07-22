package com.capstone.decision.infrastructure.security

// migration과 login이 같은 두 demo identity를 사용하도록 공개 식별자만 한 곳에 고정한다.
object DemoAccounts {
    val identities: List<DemoAccountIdentity> =
        listOf(
            DemoAccountIdentity("usr_demo_user", "demo-user", DemoRole.USER),
            DemoAccountIdentity("usr_demo_admin", "demo-admin", DemoRole.ADMIN),
        )

    fun byUsername(username: String): DemoAccountIdentity? = identities.firstOrNull { it.username == username }

    fun byUserId(userId: String): DemoAccountIdentity? = identities.firstOrNull { it.userId == userId }
}

data class DemoAccountIdentity(
    val userId: String,
    val username: String,
    val role: DemoRole,
)

// BCrypt cost가 낮거나 형식이 다른 hash는 migration, login, rotation 어디서도 신뢰하지 않는다.
object DemoCredentialHashPolicy {
    private val BCRYPT_STRENGTH_TWELVE = Regex("^\\$2[aby]\\$12\\$[./A-Za-z0-9]{53}$")

    fun requireValid(hash: String): String {
        require(BCRYPT_STRENGTH_TWELVE.matches(hash)) {
            "Demo credential hash must be a BCrypt strength-12 value."
        }
        return hash
    }

    fun isValid(hash: String): Boolean = BCRYPT_STRENGTH_TWELVE.matches(hash)
}
