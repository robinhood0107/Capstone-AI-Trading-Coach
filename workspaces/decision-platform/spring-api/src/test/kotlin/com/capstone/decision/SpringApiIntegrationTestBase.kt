package com.capstone.decision

import com.capstone.decision.infrastructure.security.DemoAccounts
import com.capstone.decision.infrastructure.security.DemoCredentialBundlePolicy
import com.capstone.decision.infrastructure.security.DemoRole
import com.capstone.decision.infrastructure.security.UserSecurityActorRecord
import com.capstone.decision.infrastructure.security.UserSecurityRecord
import com.capstone.decision.infrastructure.security.UserSecurityRepository
import com.capstone.decision.infrastructure.security.V7__s2_1_actor_trust
import org.springframework.boot.test.context.TestConfiguration
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Primary
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import java.util.Base64

// 테스트 credential과 hash는 런타임에 생성해 실제 secret이나 고정 BCrypt material을 fixture에 남기지 않는다.
abstract class SpringApiIntegrationTestBase {
    protected fun userPassword(): String = TEST_USER_PASSWORD

    protected fun adminPassword(): String = TEST_ADMIN_PASSWORD

    protected fun jwtSecret(): String = TEST_JWT_SECRET

    protected fun jwtIssuer(): String = TEST_JWT_ISSUER

    protected fun jwtAudience(): String = TEST_JWT_AUDIENCE

    companion object {
        // HS256/limiter key 분리와 demo 로그인을 검증할 수 있는 비운영 값을 테스트 프로세스에서만 만든다.
        internal val TEST_JWT_SECRET: String = "j" + "s".repeat(63)
        internal const val TEST_JWT_ISSUER: String = "capstone-test-issuer"
        internal const val TEST_JWT_AUDIENCE: String = "capstone-test-audience"
        internal val TEST_LOGIN_SCOPE_HMAC_KEY: String = "l" + "h".repeat(63)
        internal val TEST_PRINCIPLE_CURSOR_HMAC_KEY: String = "p" + "c".repeat(63)
        internal val TEST_DECISION_SCOPE_HMAC_KEY: String = "d" + "i".repeat(63)
        internal val TEST_BROKERAGE_SCOPE_HMAC_KEY: String = "b" + "r".repeat(63)
        internal val TEST_GRPC_SHARED_SECRET: String = "g" + "r".repeat(63)
        internal val TEST_CREDENTIAL_SEPARATION_KEY_BYTES: ByteArray = ByteArray(32) { index -> (index + 17).toByte() }
        internal val TEST_CREDENTIAL_SEPARATION_KEY: String =
            Base64.getUrlEncoder().withoutPadding().encodeToString(TEST_CREDENTIAL_SEPARATION_KEY_BYTES)
        internal val TEST_USER_PASSWORD: String = "u" + "p".repeat(12)
        internal val TEST_ADMIN_PASSWORD: String = "a" + "p".repeat(12)
        internal val TEST_USER_CREDENTIAL_BUNDLE: String =
            prepareTestBundle("usr_demo_user", TEST_USER_PASSWORD)
        internal val TEST_ADMIN_CREDENTIAL_BUNDLE: String =
            prepareTestBundle("usr_demo_admin", TEST_ADMIN_PASSWORD)
        internal val TEST_USER_VERIFIED_BUNDLE =
            DemoCredentialBundlePolicy.verify(
                TEST_USER_CREDENTIAL_BUNDLE,
                requireNotNull(DemoAccounts.byUserId("usr_demo_user")),
                TEST_CREDENTIAL_SEPARATION_KEY_BYTES,
            )
        internal val TEST_ADMIN_VERIFIED_BUNDLE =
            DemoCredentialBundlePolicy.verify(
                TEST_ADMIN_CREDENTIAL_BUNDLE,
                requireNotNull(DemoAccounts.byUserId("usr_demo_admin")),
                TEST_CREDENTIAL_SEPARATION_KEY_BYTES,
            )
        internal val TEST_USER_PASSWORD_HASH: String = TEST_USER_VERIFIED_BUNDLE.passwordHash
        internal val TEST_ADMIN_PASSWORD_HASH: String = TEST_ADMIN_VERIFIED_BUNDLE.passwordHash
        private val redisPasswordValue: String = "r" + "p".repeat(24)

        // 모든 SpringBootTest가 같은 trust-root 설정을 공유해야 token과 migration 검증이 안정적이다.
        @DynamicPropertySource
        @JvmStatic
        fun registerApplicationProperties(registry: DynamicPropertyRegistry) {
            registry.add("app.jwt.secret") { TEST_JWT_SECRET }
            registry.add("app.jwt.issuer") { TEST_JWT_ISSUER }
            registry.add("app.jwt.audience") { TEST_JWT_AUDIENCE }
            registry.add("app.login.scope-hmac-key") { TEST_LOGIN_SCOPE_HMAC_KEY }
            registry.add("app.principle.cursor-hmac-key") { TEST_PRINCIPLE_CURSOR_HMAC_KEY }
            registry.add("app.decision.idempotency-scope-hmac-key") { TEST_DECISION_SCOPE_HMAC_KEY }
            registry.add("app.brokerage.idempotency-scope-hmac-key") { TEST_BROKERAGE_SCOPE_HMAC_KEY }
            registry.add("app.decision.grpc.shared-secret") { TEST_GRPC_SHARED_SECRET }
            registry.add("app.demo-credentials.user-credential-bundle") { TEST_USER_CREDENTIAL_BUNDLE }
            registry.add("app.demo-credentials.admin-credential-bundle") { TEST_ADMIN_CREDENTIAL_BUNDLE }
            registry.add("app.demo-credentials.separation-key") { TEST_CREDENTIAL_SEPARATION_KEY }
            registry.add("spring.data.redis.password") { redisPasswordValue }
        }

        internal fun prepareTestBundle(
            userId: String,
            password: String,
        ): String {
            val chars = password.toCharArray()
            return try {
                DemoCredentialBundlePolicy.prepare(
                    requireNotNull(DemoAccounts.byUserId(userId)),
                    chars,
                    TEST_CREDENTIAL_SEPARATION_KEY_BYTES,
                    BCryptPasswordEncoder(12),
                )
            } finally {
                chars.fill('\u0000')
            }
        }
    }
}

internal fun s21ActorTrustMigration(
    userBundle: String = SpringApiIntegrationTestBase.TEST_USER_CREDENTIAL_BUNDLE,
    adminBundle: String = SpringApiIntegrationTestBase.TEST_ADMIN_CREDENTIAL_BUNDLE,
    separationKey: String = SpringApiIntegrationTestBase.TEST_CREDENTIAL_SEPARATION_KEY,
): V7__s2_1_actor_trust {
    val key = DemoCredentialBundlePolicy.decodeSeparationKey(separationKey)
    return try {
        val user =
            DemoCredentialBundlePolicy.verify(
                userBundle,
                requireNotNull(DemoAccounts.byUserId("usr_demo_user")),
                key,
            )
        val admin =
            DemoCredentialBundlePolicy.verify(
                adminBundle,
                requireNotNull(DemoAccounts.byUserId("usr_demo_admin")),
                key,
            )
        DemoCredentialBundlePolicy.requireSeparated(user, admin)
        V7__s2_1_actor_trust(user, admin)
    } finally {
        key.fill(0)
    }
}

// DataSource를 의도적으로 제외한 web 계약 테스트도 production과 동일한 repository port를 거친다.
@TestConfiguration(proxyBeanMethods = false)
class TestAuthRepositoryConfiguration {
    @Bean
    @Primary
    fun testUserSecurityRepository(): UserSecurityRepository {
        val users =
            listOf(
                UserSecurityRecord(
                    userId = "usr_demo_user",
                    username = "demo-user",
                    passwordHash = SpringApiIntegrationTestBase.TEST_USER_PASSWORD_HASH,
                    role = DemoRole.USER,
                    status = "ACTIVE",
                    securityVersion = 1,
                ),
                UserSecurityRecord(
                    userId = "usr_demo_admin",
                    username = "demo-admin",
                    passwordHash = SpringApiIntegrationTestBase.TEST_ADMIN_PASSWORD_HASH,
                    role = DemoRole.ADMIN,
                    status = "ACTIVE",
                    securityVersion = 1,
                ),
            )
        return object : UserSecurityRepository {
            override fun findDemoCredentials(): List<UserSecurityRecord> = users

            override fun findByUserId(userId: String): UserSecurityActorRecord? = users.firstOrNull { it.userId == userId }?.toActorRecord()
        }
    }

    private fun UserSecurityRecord.toActorRecord(): UserSecurityActorRecord =
        UserSecurityActorRecord(
            userId = userId,
            username = username,
            role = role,
            status = status,
            securityVersion = securityVersion,
        )
}
