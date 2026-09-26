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
                "/api/v1/auth/identities/github/link/start",
                "/api/v1/brokerage/mock/orders",
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
    fun `full permits account login signup and provider link routes while demo denies them`() {
        val allowed =
            listOf(
                "POST" to "/api/v1/auth/login",
                "POST" to "/api/v1/auth/signup",
                "PUT" to "/api/v1/auth/password",
                "GET" to "/api/v1/auth/options",
                "GET" to "/api/v1/auth/identities",
                "POST" to "/api/v1/auth/identities/google/link/start",
                "POST" to "/api/v1/auth/identities/kakao/link/start",
                "DELETE" to "/api/v1/auth/identities/google",
                "DELETE" to "/api/v1/auth/identities/kakao",
            )
        for ((method, path) in allowed) {
            val fullChain = MockFilterChain()
            PublicSurfaceGate(PublicSurfaceMode.FULL).doFilter(
                MockHttpServletRequest(method, path),
                MockHttpServletResponse(),
                fullChain,
            )
            assertEquals(path, (fullChain.request as MockHttpServletRequest).requestURI, "$method $path")
            val demoResponse = MockHttpServletResponse()
            PublicSurfaceGate(PublicSurfaceMode.DEMO).doFilter(
                MockHttpServletRequest(method, path),
                demoResponse,
                MockFilterChain(),
            )
            assertEquals(404, demoResponse.status, "$method $path")
        }
    }

    @Test
    fun `full agent permits only the owner scoped RAG routes while demo denies them`() {
        val detail = "/api/v2/rag/history/rag_abcdefghijkl"
        val allowed =
            listOf(
                "GET" to "/api/v2/rag/corpus-status",
                "GET" to "/api/v2/rag/consent",
                "GET" to "/api/v2/rag/world-news",
                "GET" to "/api/v2/rag/history",
                "POST" to "/api/v2/rag/consents",
                "POST" to "/api/v2/rag/vertex-preparations",
                "POST" to "/api/v2/rag/ask",
                "GET" to detail,
                "DELETE" to detail,
            )
        for ((method, path) in allowed) {
            val fullChain = MockFilterChain()
            PublicSurfaceGate(PublicSurfaceMode.FULL).doFilter(
                MockHttpServletRequest(method, path),
                MockHttpServletResponse(),
                fullChain,
            )
            assertEquals(path, (fullChain.request as MockHttpServletRequest).requestURI)
            val demoResponse = MockHttpServletResponse()
            PublicSurfaceGate(PublicSurfaceMode.DEMO).doFilter(
                MockHttpServletRequest(method, path),
                demoResponse,
                MockFilterChain(),
            )
            assertEquals(404, demoResponse.status)
        }
        for ((method, path) in listOf("GET" to "/api/v2/rag/ask", "POST" to detail, "GET" to "$detail/extra")) {
            val response = MockHttpServletResponse()
            PublicSurfaceGate(PublicSurfaceMode.FULL).doFilter(
                MockHttpServletRequest(method, path),
                response,
                MockFilterChain(),
            )
            assertEquals(404, response.status)
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
    fun `full mode permits only its two social login handoffs while demo keeps them closed`() {
        val paths =
            listOf(
                "/api/v1/auth/oidc/start/google",
                "/api/v1/auth/oidc/callback/google",
                "/api/v1/auth/oidc/start/kakao",
                "/api/v1/auth/oidc/callback/kakao",
            )
        for (path in paths) {
            val fullChain = MockFilterChain()
            PublicSurfaceGate(PublicSurfaceMode.FULL).doFilter(
                MockHttpServletRequest("GET", path),
                MockHttpServletResponse(),
                fullChain,
            )
            assertEquals(path, (fullChain.request as MockHttpServletRequest).requestURI)
            val demoResponse = MockHttpServletResponse()
            PublicSurfaceGate(PublicSurfaceMode.DEMO).doFilter(
                MockHttpServletRequest("GET", path),
                demoResponse,
                MockFilterChain(),
            )
            assertEquals(404, demoResponse.status)
        }
    }

    @Test
    fun `full mode permits owner credential setup but demo has no KIS surface`() {
        val routes =
            listOf(
                "GET" to "/api/v1/brokerage/mock/credential",
                "PUT" to "/api/v1/brokerage/mock/credential",
                "DELETE" to "/api/v1/brokerage/mock/credential",
                "POST" to "/api/v1/brokerage/mock/credential/connect",
                "POST" to "/api/v1/brokerage/mock/credential/certify",
                "POST" to "/api/v1/brokerage/mock/credential/certify/recovery-confirm",
            )
        for ((method, path) in routes) {
            val fullChain = MockFilterChain()
            PublicSurfaceGate(PublicSurfaceMode.FULL).doFilter(
                MockHttpServletRequest(method, path),
                MockHttpServletResponse(),
                fullChain,
            )
            assertEquals(path, (fullChain.request as MockHttpServletRequest).requestURI)
            val demoResponse = MockHttpServletResponse()
            PublicSurfaceGate(PublicSurfaceMode.DEMO).doFilter(
                MockHttpServletRequest(method, path),
                demoResponse,
                MockFilterChain(),
            )
            assertEquals(404, demoResponse.status)
        }
    }

    @Test
    fun `full automation surface allows exact owner routes and demo or neighboring routes stay closed`() {
        val allowed =
            listOf(
                "GET" to "/api/v2/automation/status",
                "GET" to "/api/v2/automation/positions",
                "GET" to "/api/v3/automation/status",
                "PUT" to "/api/v3/automation/policy",
                "POST" to "/api/v3/automation/arm",
                "GET" to "/api/v3/automation/runs",
                "GET" to "/api/v3/automation/runs/auto_run_abcdefgh",
                "GET" to "/api/v3/automation/positions",
                "POST" to "/api/v1/automation/disarm",
                "GET" to "/api/v4/automation/capital-policy",
                "PUT" to "/api/v4/automation/capital-policy",
                "GET" to "/api/v4/automation/capital-status",
            )
        for ((method, path) in allowed) {
            val fullChain = MockFilterChain()
            PublicSurfaceGate(PublicSurfaceMode.FULL).doFilter(
                MockHttpServletRequest(method, path),
                MockHttpServletResponse(),
                fullChain,
            )
            assertEquals(path, (fullChain.request as MockHttpServletRequest).requestURI)
            val demoResponse = MockHttpServletResponse()
            PublicSurfaceGate(PublicSurfaceMode.DEMO).doFilter(
                MockHttpServletRequest(method, path),
                demoResponse,
                MockFilterChain(),
            )
            assertEquals(404, demoResponse.status, "$method $path")
        }
        val denied =
            listOf(
                "POST" to "/api/v1/automation/arm",
                "POST" to "/api/v2/automation/arm",
                "GET" to "/api/v3/automation/arm",
                "DELETE" to "/api/v3/automation/runs/auto_run_abcdefgh",
                "GET" to "/api/v3/automation/runs/invalid",
                "POST" to "/api/v4/automation/capital-policy",
                "GET" to "/api/v3/automation/positions/extra",
            )
        for ((method, path) in denied) {
            val response = MockHttpServletResponse()
            PublicSurfaceGate(PublicSurfaceMode.FULL).doFilter(
                MockHttpServletRequest(method, path),
                response,
                MockFilterChain(),
            )
            assertEquals(404, response.status, "$method $path")
        }
    }

    @Test
    fun `demo permits only its anonymous ask method while full cannot reach it`() {
        val path = "/api/v1/demo/agent/ask"
        val demoChain = MockFilterChain()
        PublicSurfaceGate(PublicSurfaceMode.DEMO).doFilter(
            MockHttpServletRequest("POST", path),
            MockHttpServletResponse(),
            demoChain,
        )
        assertEquals(path, (demoChain.request as MockHttpServletRequest).requestURI)
        for ((mode, method) in listOf(PublicSurfaceMode.DEMO to "GET", PublicSurfaceMode.FULL to "POST")) {
            val response = MockHttpServletResponse()
            PublicSurfaceGate(mode).doFilter(MockHttpServletRequest(method, path), response, MockFilterChain())
            assertEquals(404, response.status)
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
