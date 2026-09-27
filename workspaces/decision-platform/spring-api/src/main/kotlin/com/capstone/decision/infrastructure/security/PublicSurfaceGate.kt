package com.capstone.decision.infrastructure.security

import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.beans.factory.annotation.Value
import org.springframework.boot.web.servlet.FilterRegistrationBean
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.core.Ordered
import org.springframework.core.env.Environment
import org.springframework.core.env.Profiles
import org.springframework.web.filter.OncePerRequestFilter

/**
 * Public product modes stay closed until their separate authentication and demo routes are ready.
 * LOCAL preserves the existing private deployment while the product transition is in progress.
 */
internal enum class PublicSurfaceMode {
    LOCAL,
    DEMO,
    FULL,
}

internal class PublicSurfaceGate(
    private val mode: PublicSurfaceMode,
) : OncePerRequestFilter() {
    override fun doFilterInternal(
        request: HttpServletRequest,
        response: HttpServletResponse,
        filterChain: FilterChain,
    ) {
        val health = request.method == "GET" && request.requestURI == "/actuator/health"
        val demoAllowed =
            mode == PublicSurfaceMode.DEMO &&
                request.method == "POST" &&
                request.requestURI == "/api/v1/demo/agent/ask"
        val fullAgentAllowed =
            mode == PublicSurfaceMode.FULL &&
                when (request.method to request.requestURI) {
                    "GET" to "/api/v2/rag/corpus-status",
                    "GET" to "/api/v2/rag/consent",
                    "GET" to "/api/v2/rag/world-news",
                    "GET" to "/api/v2/rag/history",
                    "POST" to "/api/v2/rag/consents",
                    "POST" to "/api/v2/rag/vertex-preparations",
                    "POST" to "/api/v2/rag/ask",
                    "GET" to "/api/v1/rag/sources",
                    // 사용자 자기 Vertex 서비스 계정 등록. owner 범위 쓰기이고 응답 본문이 없다.
                    // 읽기는 corpus-status 가 마지막 네 글자만 싣는다.
                    "PUT" to "/api/v2/strong-llm/settings",
                    -> true
                    else ->
                        (
                            (request.method == "GET" || request.method == "DELETE") &&
                                FULL_RAG_HISTORY_DETAIL.matches(request.requestURI)
                        ) ||
                            (request.method == "POST" && FULL_RAG_ANSWER_FEEDBACK.matches(request.requestURI))
                }
        val fullAllowed =
            mode == PublicSurfaceMode.FULL &&
                when (request.method to request.requestURI) {
                    "GET" to "/api/v1/auth/oidc/start/google",
                    "GET" to "/api/v1/auth/oidc/callback/google",
                    "GET" to "/api/v1/auth/oidc/start/kakao",
                    "GET" to "/api/v1/auth/oidc/callback/kakao",
                    "POST" to "/api/v1/auth/oidc/exchange",
                    "POST" to "/api/v1/auth/logout",
                    "POST" to "/api/v1/auth/login",
                    "POST" to "/api/v1/auth/signup",
                    "PUT" to "/api/v1/auth/password",
                    "POST" to "/api/v1/auth/password",
                    "GET" to "/api/v1/auth/options",
                    "GET" to "/api/v1/auth/identities",
                    "GET" to "/api/v1/brokerage/mock/credential",
                    "PUT" to "/api/v1/brokerage/mock/credential",
                    "DELETE" to "/api/v1/brokerage/mock/credential",
                    "POST" to "/api/v1/brokerage/mock/credential/connect",
                    "POST" to "/api/v1/brokerage/mock/credential/certify",
                    "POST" to "/api/v1/brokerage/mock/credential/certify/recovery-confirm",
                    -> true
                    else ->
                        (request.method == "POST" && FULL_PROVIDER_LINK_START.matches(request.requestURI)) ||
                            (request.method == "DELETE" && FULL_PROVIDER_UNLINK.matches(request.requestURI))
                }
        val fullAutomationAllowed =
            mode == PublicSurfaceMode.FULL &&
                when (request.method to request.requestURI) {
                    "GET" to "/api/v1/automation/status",
                    "GET" to "/api/v2/automation/status",
                    "GET" to "/api/v2/automation/positions",
                    "GET" to "/api/v3/automation/status",
                    "PUT" to "/api/v3/automation/policy",
                    "POST" to "/api/v3/automation/arm",
                    "GET" to "/api/v3/automation/runs",
                    "GET" to "/api/v3/automation/positions",
                    "POST" to "/api/v1/automation/disarm",
                    "GET" to "/api/v4/automation/capital-policy",
                    "PUT" to "/api/v4/automation/capital-policy",
                    "GET" to "/api/v4/automation/capital-status",
                    -> true
                    else -> FULL_AUTOMATION_RUN_DETAIL.matches(request.requestURI) && request.method == "GET"
                }
        // FULL 사용자 기능(개인 스택과 같은 기능). 모두 인증이 필요하고 데이터는 owner 범위로만 읽는다.
        // 관리자 경로는 SecurityConfig와 method security가 ADMIN을 강제한다.
        val fullUserFeatureAllowed =
            mode == PublicSurfaceMode.FULL &&
                FULL_USER_FEATURES.any { (methods, prefix) ->
                    request.method in methods && (request.requestURI == prefix || request.requestURI.startsWith("$prefix/"))
                }
        if (
            mode != PublicSurfaceMode.LOCAL &&
            !health &&
            !fullUserFeatureAllowed &&
            !fullAllowed &&
            !fullAgentAllowed &&
            !fullAutomationAllowed &&
            !demoAllowed
        ) {
            response.sendError(HttpServletResponse.SC_NOT_FOUND)
            return
        }
        filterChain.doFilter(request, response)
    }

    private companion object {
        val FULL_RAG_HISTORY_DETAIL = Regex("^/api/v2/rag/history/rag_[A-Za-z0-9_-]{12,96}$")
        val FULL_RAG_ANSWER_FEEDBACK = Regex("^/api/v1/rag/answers/rag_[A-Za-z0-9_-]{12,96}/feedback$")
        private val READ = setOf("GET")
        private val READ_WRITE = setOf("GET", "POST", "PUT", "PATCH", "DELETE")
        val FULL_USER_FEATURES: List<Pair<Set<String>, String>> =
            listOf(
                READ to "/api/v1/principle-presets",
                READ_WRITE to "/api/v1/principles",
                READ to "/api/v1/dashboard",
                READ to "/api/v2/signals",
                READ to "/api/v3/signals",
                READ_WRITE to "/api/v1/decisions",
                READ to "/api/v1/risk/portfolio",
                READ_WRITE to "/api/v2/risk/kill-switch",
                READ_WRITE to "/api/v1/risk/kill-switch",
                READ_WRITE to "/api/v1/journals",
                READ to "/api/v1/instruments",
                READ to "/api/v2/market-evidence",
                READ to "/api/v1/system/health",
                // 내 Vertex 키 상태·자기 AI 검토 사용량. 호출자 본인 것만 읽는다.
                READ to "/api/v1/ai-review",
                // FULL 주문·잔고·체결은 호출자 본인의 암호화된 KIS 키와 본인 계좌로만 동작한다.
                READ_WRITE to "/api/v1/brokerage/mock/orders",
                READ_WRITE to "/api/v1/brokerage/orders",
                READ to "/api/v1/brokerage/mock/accounts",
                READ_WRITE to "/api/v1/brokerage/paper",
                READ_WRITE to "/api/v1/admin",
            )
        val FULL_PROVIDER_LINK_START = Regex("^/api/v1/auth/identities/(google|kakao)/link/start$")
        val FULL_PROVIDER_UNLINK = Regex("^/api/v1/auth/identities/(google|kakao)$")
        val FULL_AUTOMATION_RUN_DETAIL = Regex("^/api/v3/automation/runs/auto_run_[A-Za-z0-9_-]{8,96}$")
    }
}

@Configuration
internal class PublicSurfaceGateConfiguration {
    @Bean
    fun publicSurfaceGate(
        @Value("\${MARS_PUBLIC_SURFACE_MODE:LOCAL}") rawMode: String,
        environment: Environment,
    ): FilterRegistrationBean<PublicSurfaceGate> {
        val mode = PublicSurfaceMode.valueOf(rawMode)
        val fullProfile = environment.acceptsProfiles(Profiles.of("mars-full"))
        val demoProfile = environment.acceptsProfiles(Profiles.of("mars-demo"))
        require(!(fullProfile && demoProfile)) { "Only one MARS product profile can start." }
        require(fullProfile == (mode == PublicSurfaceMode.FULL)) { "FULL mode requires the matching product profile." }
        require(demoProfile == (mode == PublicSurfaceMode.DEMO)) { "DEMO mode requires the matching product profile." }
        return FilterRegistrationBean(PublicSurfaceGate(mode)).apply {
            order = Ordered.HIGHEST_PRECEDENCE
            addUrlPatterns("/*")
        }
    }
}
