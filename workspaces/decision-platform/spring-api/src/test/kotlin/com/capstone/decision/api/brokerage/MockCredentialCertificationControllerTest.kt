package com.capstone.decision.api.brokerage

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.application.security.AuthenticatedActorRef
import com.capstone.decision.infrastructure.brokerage.MockCredentialCertificationOutcome
import com.capstone.decision.infrastructure.brokerage.MockCredentialCertificationService
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import org.springframework.mock.web.MockHttpServletRequest
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken
import org.springframework.security.core.context.SecurityContextHolder

class MockCredentialCertificationControllerTest {
    @Test
    fun `fixed certification uses the authenticated owner and has no caller-controlled order body`() {
        val service = mockk<MockCredentialCertificationService>()
        every { service.certify("usr_demo_user", any()) } returns
            MockCredentialCertificationOutcome("PASS", "cert_${"a".repeat(32)}", "2026-08-26", 1, 7, 0, null)
        val controller = MockCredentialCertificationController(service)
        val request = MockHttpServletRequest("POST", "/api/v1/brokerage/mock/credential/certify")
        request.addHeader("X-Request-Id", "req_certification_test_001")
        val principal = testPrincipal()

        val response = withPrincipal(principal) { controller.certify(principal, null, request) }

        assertEquals(200, response.statusCode.value())
        assertEquals("PASS", response.body?.data?.status)
        assertEquals("no-store", response.headers.cacheControl)
        verify(exactly = 1) { service.certify("usr_demo_user", "req_certification_test_001") }

        val badRequest = MockHttpServletRequest("POST", "/api/v1/brokerage/mock/credential/certify")
        assertThrows(ApiException::class.java) {
            withPrincipal(principal) { controller.certify(principal, """{"symbol":"005930"}""", badRequest) }
        }
        verify(exactly = 1) { service.certify(any(), any()) }
    }

    @Test
    fun `query parameters are rejected before certification`() {
        val service = mockk<MockCredentialCertificationService>()
        val controller = MockCredentialCertificationController(service)
        val request = MockHttpServletRequest("POST", "/api/v1/brokerage/mock/credential/certify")
        request.queryString = "quantity=1"
        val principal = testPrincipal()

        assertThrows(ApiException::class.java) {
            withPrincipal(principal) { controller.certify(principal, null, request) }
        }
        verify(exactly = 0) { service.certify(any(), any()) }
    }

    @Test
    fun `recovery acknowledgement is owner scoped and accepts no order details`() {
        val service = mockk<MockCredentialCertificationService>(relaxed = true)
        val controller = MockCredentialCertificationController(service)
        val principal = testPrincipal()
        val request = MockHttpServletRequest("POST", "/api/v1/brokerage/mock/credential/certify/recovery-confirm")

        val response = withPrincipal(principal) { controller.acknowledgeRecovery(principal, null, request) }

        assertEquals(204, response.statusCode.value())
        assertEquals("no-store", response.headers.cacheControl)
        verify(exactly = 1) { service.acknowledgeRecovery("usr_demo_user") }
        verify(exactly = 0) { service.certify(any(), any()) }
    }

    private fun <T> withPrincipal(
        principal: AppPrincipal,
        block: () -> T,
    ): T {
        val previous = SecurityContextHolder.getContext()
        val context = SecurityContextHolder.createEmptyContext()
        context.authentication = UsernamePasswordAuthenticationToken(principal, null, emptyList())
        SecurityContextHolder.setContext(context)
        return try {
            block()
        } finally {
            SecurityContextHolder.setContext(previous)
        }
    }

    private fun testPrincipal(): AppPrincipal {
        val userId = "usr_demo_user"
        return AppPrincipal(
            userId,
            "oidc_demo_user",
            "USER",
            1,
            AuthenticatedActorRef("sid1_${"a".repeat(64)}", userId, 1),
        )
    }
}
