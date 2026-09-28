package com.capstone.decision.api.auth

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.infrastructure.security.AuthenticatedAccount
import com.capstone.decision.infrastructure.security.DemoRole
import com.capstone.decision.infrastructure.security.FullBrowserRefreshCookieService
import com.capstone.decision.infrastructure.security.FullBrowserRefreshSessionRepository
import com.capstone.decision.infrastructure.security.IssuedToken
import com.capstone.decision.infrastructure.security.JwtProperties
import com.capstone.decision.infrastructure.security.JwtService
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.springframework.mock.web.MockHttpServletRequest
import org.springframework.mock.web.MockHttpServletResponse
import java.time.OffsetDateTime

class FullBrowserRefreshControllerTest {
    @Test
    fun `same origin refresh returns a new access token`() {
        val repository = mockk<FullBrowserRefreshSessionRepository>()
        val cookies = FullBrowserRefreshCookieService(repository, JwtProperties(ttlHours = 12), "http://localhost:3002")
        val jwt = mockk<JwtService>()
        val account =
            AuthenticatedAccount(
                "usr_fixture_user",
                "fixture",
                DemoRole.USER,
                1,
                "sid1_" + "a".repeat(64),
                OffsetDateTime.now().plusHours(12),
            )
        every { repository.resume(any(), any()) } returns account
        every { jwt.issue(account) } returns IssuedToken("fixture.jwt", account.expiresAt)
        val controller = FullBrowserRefreshController(cookies, jwt, "http://localhost:3002")
        val request =
            MockHttpServletRequest("POST", "/api/v1/auth/refresh").apply {
                addHeader("Origin", "http://localhost:3002")
                setCookies(jakarta.servlet.http.Cookie("mars_full_refresh", "rfs1_" + "b".repeat(64)))
            }
        val response = MockHttpServletResponse()

        val result = controller.refresh(request, response)

        assertEquals("fixture.jwt", result.data?.accessToken)
        assertTrue(response.getHeaders("Set-Cookie").single().contains("HttpOnly"))
        assertTrue(response.getHeaders("Set-Cookie").single().contains("SameSite=Lax"))
        verify(exactly = 1) { repository.resume(any(), 43_200) }

        val rejected =
            MockHttpServletRequest("POST", "/api/v1/auth/refresh").apply {
                addHeader("Origin", "http://other.example")
            }
        assertThrows(ApiException::class.java) { controller.refresh(rejected, MockHttpServletResponse()) }
    }
}
