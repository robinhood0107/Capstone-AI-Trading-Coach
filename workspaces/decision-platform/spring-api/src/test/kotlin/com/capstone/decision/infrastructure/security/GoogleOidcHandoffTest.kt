package com.capstone.decision.infrastructure.security

import com.capstone.decision.api.auth.GoogleOidcExchangeController
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

class GoogleOidcHandoffTest {
    @Test
    fun `only the configured exact Google subject receives operator eligibility`() {
        val hash =
            HexFormat
                .of()
                .formatHex(MessageDigest.getInstance("SHA-256").digest("subject-A".toByteArray()))
        val properties = GoogleOidcProperties("https://mars.example.test", hash)
        assertEquals("https://mars.example.test", properties.validatedOrigin())
        assertTrue(properties.isOperatorSubject("subject-A"))
        assertFalse(properties.isOperatorSubject("subject-B"))
        assertThrows(IllegalArgumentException::class.java) {
            GoogleOidcProperties("http://mars.example.test", hash).validatedOrigin()
        }
    }

    @Test
    fun `verified subject reaches one use exchange with no token in the redirect URL`() {
        val repository = RecordingRepository()
        val handoff = handoff(repository)
        val session = MockHttpSession()
        handoff.stage(GoogleOidcHandoff.GOOGLE_ISSUER, "subject-A", false, session)
        assertThrows(IllegalArgumentException::class.java) {
            handoff.stage(GoogleOidcHandoff.GOOGLE_ISSUER, "subject-A", false, session)
        }
        assertEquals(1, repository.calls)

        val controller = GoogleOidcExchangeController(handoff, GoogleOidcProperties("https://mars.example.test", "a".repeat(64)))
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
    }

    private fun handoff(repository: GoogleOidcSessionRepository): GoogleOidcHandoff {
        val jwtProperties = JwtProperties(secret = "j" + "s".repeat(63), issuer = "test", audience = "test")
        return GoogleOidcHandoff(repository, JwtService(jwtProperties, EmptyUserSecurityRepository), jwtProperties)
    }

    private class RecordingRepository : GoogleOidcSessionRepository {
        var calls = 0

        override fun createSession(
            issuer: String,
            subject: String,
            operatorSubject: Boolean,
            ttlSeconds: Int,
        ): AuthenticatedAccount {
            calls++
            assertEquals(GoogleOidcHandoff.GOOGLE_ISSUER, issuer)
            assertEquals("subject-A", subject)
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
