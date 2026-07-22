package com.capstone.decision

import com.capstone.decision.infrastructure.security.DemoRole
import com.capstone.decision.infrastructure.security.UserSecurityRecord
import com.capstone.decision.infrastructure.security.UserSecurityRepository
import org.springframework.boot.test.context.TestConfiguration
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Primary
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource

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
        internal val TEST_USER_PASSWORD: String = "u" + "p".repeat(12)
        internal val TEST_ADMIN_PASSWORD: String = "a" + "p".repeat(12)
        internal val TEST_USER_PASSWORD_HASH: String =
            requireNotNull(BCryptPasswordEncoder(12).encode(TEST_USER_PASSWORD))
        internal val TEST_ADMIN_PASSWORD_HASH: String =
            requireNotNull(BCryptPasswordEncoder(12).encode(TEST_ADMIN_PASSWORD))
        private val redisPasswordValue: String = "r" + "p".repeat(24)

        // 모든 SpringBootTest가 같은 trust-root 설정을 공유해야 token과 migration 검증이 안정적이다.
        @DynamicPropertySource
        @JvmStatic
        fun registerApplicationProperties(registry: DynamicPropertyRegistry) {
            registry.add("app.jwt.secret") { TEST_JWT_SECRET }
            registry.add("app.jwt.issuer") { TEST_JWT_ISSUER }
            registry.add("app.jwt.audience") { TEST_JWT_AUDIENCE }
            registry.add("app.login.scope-hmac-key") { TEST_LOGIN_SCOPE_HMAC_KEY }
            registry.add("spring.flyway.placeholders.demoUserPasswordHash") { TEST_USER_PASSWORD_HASH }
            registry.add("spring.flyway.placeholders.demoAdminPasswordHash") { TEST_ADMIN_PASSWORD_HASH }
            registry.add("spring.data.redis.password") { redisPasswordValue }
        }
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
            override fun findByUsername(username: String): UserSecurityRecord? = users.firstOrNull { it.username == username }

            override fun findByUserId(userId: String): UserSecurityRecord? = users.firstOrNull { it.userId == userId }
        }
    }
}
