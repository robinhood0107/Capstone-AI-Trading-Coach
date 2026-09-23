package com.capstone.decision.infrastructure.security

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import org.springframework.mock.env.MockEnvironment
import org.springframework.mock.web.MockFilterChain
import org.springframework.mock.web.MockHttpServletRequest
import org.springframework.mock.web.MockHttpServletResponse

class PublicSurfaceGateTest {
    @Test
    fun `public modes reject all application routes before authentication or provider code`() {
        val paths =
            listOf(
                "/api/v1/auth/login",
                "/api/v1/brokerage/mock/orders",
                "/api/v2/rag/ask",
                "/internal/automation-runtime/run",
            )
        for (mode in listOf(PublicSurfaceMode.DEMO, PublicSurfaceMode.FULL)) {
            for (path in paths) {
                val response = MockHttpServletResponse()
                val chain = MockFilterChain()
                PublicSurfaceGate(mode).doFilter(MockHttpServletRequest("POST", path), response, chain)
                assertEquals(404, response.status, "$mode $path")
                assertEquals(null, chain.request, "$mode $path")
            }
        }
    }

    @Test
    fun `public mode permits only read only liveness`() {
        for (mode in listOf(PublicSurfaceMode.DEMO, PublicSurfaceMode.FULL)) {
            val response = MockHttpServletResponse()
            val chain = MockFilterChain()
            PublicSurfaceGate(mode).doFilter(MockHttpServletRequest("GET", "/actuator/health"), response, chain)
            assertEquals(200, response.status)
            assertEquals("/actuator/health", (chain.request as MockHttpServletRequest).requestURI)
        }
    }

    @Test
    fun `local mode retains current private routes and invalid public mode fails startup`() {
        val chain = MockFilterChain()
        PublicSurfaceGate(PublicSurfaceMode.LOCAL).doFilter(
            MockHttpServletRequest("POST", "/api/v1/auth/login"),
            MockHttpServletResponse(),
            chain,
        )
        assertEquals("/api/v1/auth/login", (chain.request as MockHttpServletRequest).requestURI)
        assertThrows(IllegalArgumentException::class.java) {
            PublicSurfaceGateConfiguration().publicSurfaceGate("UNKNOWN", MockEnvironment())
        }
        val fullEnvironment = MockEnvironment().apply { setActiveProfiles("mars-full") }
        assertThrows(IllegalArgumentException::class.java) {
            PublicSurfaceGateConfiguration().publicSurfaceGate("LOCAL", fullEnvironment)
        }
    }
}
