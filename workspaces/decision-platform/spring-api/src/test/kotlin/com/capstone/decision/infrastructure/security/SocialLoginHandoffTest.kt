package com.capstone.decision.infrastructure.security

import com.capstone.decision.api.auth.SocialLoginExchangeController
import com.capstone.decision.api.common.ApiException
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.springframework.mock.web.MockHttpServletRequest
import org.springframework.mock.web.MockHttpServletResponse
import org.springframework.mock.web.MockHttpSession
import java.security.MessageDigest
import java.time.OffsetDateTime
import java.util.HexFormat

class SocialLoginHandoffTest {
    @Test
    fun `only the configured exact Google subject receives operator eligibility`() {
        val hash =
            HexFormat
                .of()
                .formatHex(MessageDigest.getInstance("SHA-256").digest("subject-A".toByteArray()))
        val properties = FullSocialLoginProperties("https://mars.example.test", hash)
        assertEquals("https://mars.example.test", properties.validatedOrigin())
        assertTrue(properties.isGoogleAdminSubject("subject-A"))
        assertFalse(properties.isGoogleAdminSubject("subject-B"))
        assertThrows(IllegalArgumentException::class.java) {
            FullSocialLoginProperties("http://mars.example.test", hash).validatedOrigin()
        }
    }

    @Test
    fun `verified subject reaches one use exchange with no token in the redirect URL`() {
        val repository = RecordingRepository()
        val handoff = SocialLoginHandoff(repository, jwtService(), jwtProperties())
        val session = MockHttpSession()
        handoff.stage(SocialLoginHandoff.GOOGLE_ISSUER, "subject-A", false, session)
        assertThrows(IllegalArgumentException::class.java) {
            handoff.stage(SocialLoginHandoff.GOOGLE_ISSUER, "subject-A", false, session)
        }
        assertEquals(1, repository.calls)

        val controller =
            SocialLoginExchangeController(
                handoff,
                FullSocialLoginProperties("https://mars.example.test", "a".repeat(64)),
            )
        val rejected =
            MockHttpServletRequest("POST", "/api/v1/auth/oidc/exchange").apply {
                addHeader("Origin", "https://other.example.test")
                setSession(session)
            }
        assertThrows(ApiException::class.java) { controller.exchange(rejected, MockHttpServletResponse()) }

        val request =
            MockHttpServletRequest("POST", "/api/v1/auth/oidc/exchange").apply {
                addHeader("Origin", "https://mars.example.test")
                setSession(session)
            }
        val response = MockHttpServletResponse()
        val result = controller.exchange(request, response)
        assertEquals("usr_oidc_test_0001", result.data?.user?.userId)
        assertTrue(result.data?.accessToken?.isNotBlank() == true)
        assertEquals("no-store", response.getHeader("Cache-Control"))
        assertThrows(IllegalStateException::class.java) { handoff.consume(session) }

        val kakaoSession = MockHttpSession()
        handoff.stage(SocialLoginHandoff.KAKAO_ISSUER, "123456", false, kakaoSession)
        assertEquals(2, repository.calls)
        assertThrows(IllegalArgumentException::class.java) {
            handoff.stage(SocialLoginHandoff.KAKAO_ISSUER, "123456", true, MockHttpSession())
        }
    }

    @Test
    fun `provider link intent stays with the logged in owner through the one use exchange`() {
        val repository = RecordingRepository()
        val handoff = SocialLoginHandoff(repository, jwtService(), jwtProperties())
        val session = MockHttpSession()
        handoff.beginLink("usr_demo_user", "google", session)
        assertEquals(SocialLoginLinkIntent("usr_demo_user", "google"), handoff.linkIntent(session))

        handoff.stageLinkedIdentity("usr_demo_user", SocialLoginHandoff.GOOGLE_ISSUER, "subject-A", false, session)
        assertEquals(null, handoff.linkIntent(session))
        val controller =
            SocialLoginExchangeController(
                handoff,
                FullSocialLoginProperties("https://mars.example.test", "a".repeat(64)),
            )
        val request =
            MockHttpServletRequest("POST", "/api/v1/auth/oidc/exchange").apply {
                addHeader("Origin", "https://mars.example.test")
                setSession(session)
            }
        val result = controller.exchange(request, MockHttpServletResponse())
        assertEquals("usr_demo_user", result.data?.user?.userId)
        assertEquals("demo-user", result.data?.user?.username)
        assertThrows(IllegalStateException::class.java) { handoff.consume(session) }
    }

    private fun jwtProperties() = JwtProperties(secret = "j" + "s".repeat(63), issuer = "test", audience = "test")

    private fun jwtService() = JwtService(jwtProperties(), EmptyUserSecurityRepository)

    private class RecordingRepository : SocialLoginSessionRepository {
        var calls = 0

        override fun createSession(
            issuer: String,
            subject: String,
            operatorSubject: Boolean,
            ttlSeconds: Int,
        ): AuthenticatedAccount {
            calls++
            assertTrue(issuer in setOf(SocialLoginHandoff.GOOGLE_ISSUER, SocialLoginHandoff.KAKAO_ISSUER))
            assertEquals(if (issuer == SocialLoginHandoff.GOOGLE_ISSUER) "subject-A" else "123456", subject)
            assertFalse(operatorSubject)
            assertEquals(43_200, ttlSeconds)
            return AuthenticatedAccount(
                userId = "usr_oidc_test_0001",
                username = "oidc_test_0001",
                role = DemoRole.USER,
                securityVersion = 1,
                sessionHandle = "sid1_" + "a".repeat(64),
                expiresAt = OffsetDateTime.now().plusHours(1),
            )
        }

        override fun linkIdentityAndCreateSession(
            userId: String,
            issuer: String,
            subject: String,
            operatorSubject: Boolean,
            ttlSeconds: Int,
        ): AuthenticatedAccount {
            calls++
            assertEquals("usr_demo_user", userId)
            assertEquals(SocialLoginHandoff.GOOGLE_ISSUER, issuer)
            assertEquals("subject-A", subject)
            assertFalse(operatorSubject)
            assertEquals(43_200, ttlSeconds)
            return AuthenticatedAccount(
                userId = userId,
                username = "demo-user",
                role = DemoRole.USER,
                securityVersion = 1,
                sessionHandle = "sid1_" + "b".repeat(64),
                expiresAt = OffsetDateTime.now().plusHours(1),
            )
        }

        override fun listAuthenticationMethods(userId: String): List<AccountAuthenticationMethod> = emptyList()

        override fun unlinkIdentity(
            userId: String,
            issuer: String,
        ): Boolean = true
    }

    private object EmptyUserSecurityRepository : UserSecurityRepository {
        override fun findDemoCredentials(): List<UserSecurityRecord> = error("unused")

        override fun createAuthenticatedSession(
            username: String,
            password: String,
            ttlSeconds: Int,
        ): UserSecuritySessionRecord? = error("unused")

        override fun findBySessionHandle(sessionHandle: String): UserSecuritySessionRecord? = error("unused")

        override fun findByUserId(userId: String): UserSecurityActorRecord? = error("unused")
    }
}
