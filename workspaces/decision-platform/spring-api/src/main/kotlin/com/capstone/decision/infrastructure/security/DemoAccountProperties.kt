package com.capstone.decision.infrastructure.security

import org.springframework.boot.context.properties.ConfigurationProperties

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

// V7이 두 고정 행을 만들고 V213이 demo-admin을 삭제한다. demo-user는 운영자 계정이라 역할이
// USER·ADMIN 어느 쪽이든 된다. demo-admin 행이 아직 남은 DB에서는 DISABLED도 허용한다.
object DemoOperatorAccountPolicy {
    const val OPERATOR_USER_ID = "usr_demo_user"
    const val RETIRED_ADMIN_USER_ID = "usr_demo_admin"

    fun roleAccepted(
        userId: String,
        storedRole: String,
        bootstrapRole: DemoRole,
    ): Boolean =
        if (userId == OPERATOR_USER_ID) {
            storedRole == DemoRole.USER.name || storedRole == DemoRole.ADMIN.name
        } else {
            storedRole == bootstrapRole.name
        }

    fun statusAccepted(
        userId: String,
        status: String,
    ): Boolean = status == "ACTIVE" || (userId == RETIRED_ADMIN_USER_ID && status == "DISABLED")
}

data class DemoAccountIdentity(
    val userId: String,
    val username: String,
    val role: DemoRole,
)

// V7 Java migration에만 전달할 attested bundle이며 Flyway SQL placeholder에는 노출하지 않는다.
@ConfigurationProperties("app.demo-credentials")
data class DemoCredentialBootstrapProperties(
    var userCredentialBundle: String = "",
    var adminCredentialBundle: String = "",
    var separationKey: String = "",
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
