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
                "/api/v1/async-jobs",
                "/api/v1/rag/ask",
                "/internal/automation-runtime/run",
            )
        for (mode in listOf(PublicSurfaceMode.FULL)) {
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
    fun `full lets only the loopback automation runtime reach its bridge command`() {
        fun status(
            mode: PublicSurfaceMode,
            method: String,
            path: String,
            remote: String,
        ): Pair<Int, Boolean> {
            val request = MockHttpServletRequest(method, path).apply { remoteAddr = remote }
            val response = MockHttpServletResponse()
            val chain = MockFilterChain()
            PublicSurfaceGate(mode).doFilter(request, response, chain)
            return response.status to (chain.request != null)
        }
        for (loopback in listOf("127.0.0.1", "::1", "0:0:0:0:0:0:0:1")) {
            assertEquals(200 to true, status(PublicSurfaceMode.FULL, "POST", "/internal/automation-runtime/command", loopback))
        }
        // 웹 프록시·다른 container·다른 method·다른 경로는 모두 닫힌다.
        assertEquals(404 to false, status(PublicSurfaceMode.FULL, "POST", "/internal/automation-runtime/command", "172.18.0.5"))
        assertEquals(404 to false, status(PublicSurfaceMode.FULL, "GET", "/internal/automation-runtime/command", "127.0.0.1"))
        assertEquals(404 to false, status(PublicSurfaceMode.FULL, "POST", "/internal/automation-runtime/run", "127.0.0.1"))
    }

    @Test
    fun `full permits every owner scoped user feature and the admin console`() {
        val allowed =
            listOf(
                "GET" to "/api/v1/principle-presets",
                "POST" to "/api/v1/principles",
                "PUT" to "/api/v1/principles/prc_abcdefgh",
                "GET" to "/api/v1/dashboard/backtests/latest",
                "GET" to "/api/v3/signals/005930",
                "POST" to "/api/v1/decisions/evaluate-order",
                "GET" to "/api/v1/risk/portfolio",
                "POST" to "/api/v2/risk/kill-switch",
                "PATCH" to "/api/v1/journals/jrn_abcdefgh",
                "GET" to "/api/v2/market-evidence/005930/foreign-news-sentiment",
                "POST" to "/api/v1/brokerage/mock/orders",
                "POST" to "/api/v1/brokerage/orders/ord_abcdefgh/cancel",
                "GET" to "/api/v1/brokerage/mock/accounts/acct_abcdefgh/balances",
                "GET" to "/api/v1/admin/users",
                "PUT" to "/api/v1/admin/limits",
            )
        for ((method, path) in allowed) {
            val fullChain = MockFilterChain()
            PublicSurfaceGate(PublicSurfaceMode.FULL).doFilter(MockHttpServletRequest(method, path), MockHttpServletResponse(), fullChain)
            assertEquals(path, (fullChain.request as MockHttpServletRequest).requestURI, "$method $path")
        }
        val readOnly = MockHttpServletResponse()
        PublicSurfaceGate(PublicSurfaceMode.FULL).doFilter(
            MockHttpServletRequest("POST", "/api/v3/signals/005930"),
            readOnly,
            MockFilterChain(),
        )
        assertEquals(404, readOnly.status)
    }

    @Test
    fun `full permits account login signup and provider link routes`() {
        val allowed =
            listOf(
                "POST" to "/api/v1/auth/login",
                "POST" to "/api/v1/auth/refresh",
                "POST" to "/api/v1/auth/signup",
                "PUT" to "/api/v1/auth/password",
                "POST" to "/api/v1/auth/password",
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
        }
    }

    @Test
    fun `full agent permits only the owner scoped RAG routes`() {
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
                "GET" to "/api/v1/rag/sources",
                "POST" to "/api/v1/rag/answers/rag_ans_0123456789abcdef0123456789abcdef/feedback",
            )
        for ((method, path) in allowed) {
            val fullChain = MockFilterChain()
            PublicSurfaceGate(PublicSurfaceMode.FULL).doFilter(
                MockHttpServletRequest(method, path),
                MockHttpServletResponse(),
                fullChain,
            )
            assertEquals(path, (fullChain.request as MockHttpServletRequest).requestURI)
        }
        val deniedRag =
            listOf(
                "GET" to "/api/v2/rag/ask",
                "POST" to detail,
                "GET" to "$detail/extra",
                "POST" to "/api/v1/rag/sources",
                "GET" to "/api/v1/rag/answers/rag_ans_0123456789abcdef/feedback",
                "POST" to "/api/v1/rag/answers/invalid/feedback",
                // 웹이 호출하지 않는 비동기 작업·적재 상태 조회는 FULL에서 닫아 둔다.
                "GET" to "/api/v1/async-jobs",
                "GET" to "/api/v1/artifacts/ingest-status",
            )
        for ((method, path) in deniedRag) {
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
        for (mode in listOf(PublicSurfaceMode.FULL)) {
            val response = MockHttpServletResponse()
            val chain = MockFilterChain()
            PublicSurfaceGate(mode).doFilter(MockHttpServletRequest("GET", "/actuator/health"), response, chain)
            assertEquals(200, response.status)
            assertEquals("/actuator/health", (chain.request as MockHttpServletRequest).requestURI)
        }
    }

    @Test
    fun `full mode permits only its two social login handoffs`() {
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
        }
    }

    @Test
    fun `full mode permits owner credential setup`() {
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
        }
    }

    @Test
    fun `full automation surface allows exact owner routes and neighboring routes stay closed`() {
        val allowed =
            listOf(
                "GET" to "/api/v1/automation/status",
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
    fun `legacy demo Agent route is closed on the full API`() {
        val path = "/api/v1/demo/agent/ask"
        for (method in listOf("GET", "POST")) {
            val response = MockHttpServletResponse()
            PublicSurfaceGate(PublicSurfaceMode.FULL).doFilter(MockHttpServletRequest(method, path), response, MockFilterChain())
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
        assertThrows(IllegalArgumentException::class.java) {
            PublicSurfaceGateConfiguration().publicSurfaceGate("DEMO", MockEnvironment())
        }
        val fullEnvironment = MockEnvironment().apply { setActiveProfiles("mars-full") }
        assertThrows(IllegalArgumentException::class.java) {
            PublicSurfaceGateConfiguration().publicSurfaceGate("LOCAL", fullEnvironment)
        }
    }
}
