package com.capstone.decision.infrastructure.security

import com.capstone.decision.SpringApiIntegrationTestBase
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
import java.time.Duration
import java.time.OffsetDateTime

class DemoAccountServiceTest {
    @Test
    fun `OAuth authentication binds the database actor session to the exact refresh ttl`() {
        val repository = RecordingUserSecurityRepository()
        val service = DemoAccountService(repository, BCryptPasswordEncoder(12), JwtProperties())

        val account =
            service.authenticate(
                "demo-user",
                SpringApiIntegrationTestBase.TEST_USER_PASSWORD,
                Duration.ofDays(7),
            )

        assertThat(account?.userId).isEqualTo("usr_demo_user")
        assertThat(repository.recordedTtlSeconds).isEqualTo(604_800)
    }

    @Test
    fun `actor session ttl cannot exceed the refresh family lifetime`() {
        val service = DemoAccountService(RecordingUserSecurityRepository(), BCryptPasswordEncoder(12), JwtProperties())

        assertThatThrownBy {
            service.authenticate(
                "demo-user",
                SpringApiIntegrationTestBase.TEST_USER_PASSWORD,
                Duration.ofDays(7).plusSeconds(1),
            )
        }.isInstanceOf(IllegalArgumentException::class.java)
    }

    private class RecordingUserSecurityRepository : UserSecurityRepository {
        var recordedTtlSeconds: Int? = null

        override fun findDemoCredentials(): List<UserSecurityRecord> =
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

        override fun createAuthenticatedSession(
            username: String,
            password: String,
            ttlSeconds: Int,
        ): UserSecuritySessionRecord {
            recordedTtlSeconds = ttlSeconds
            return UserSecuritySessionRecord(
                sessionHandle = "sid1_${"b".repeat(64)}",
                userId = "usr_demo_user",
                username = username,
                role = DemoRole.USER,
                securityVersion = 1,
                expiresAt = OffsetDateTime.now().plusSeconds(ttlSeconds.toLong()),
            )
        }

        override fun findBySessionHandle(sessionHandle: String): UserSecuritySessionRecord? = null

        override fun findByUserId(userId: String): UserSecurityActorRecord? = null
    }
}
