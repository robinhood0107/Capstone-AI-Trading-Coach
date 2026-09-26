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
                    -> true
                    else ->
                        (request.method == "GET" || request.method == "DELETE") &&
                            FULL_RAG_HISTORY_DETAIL.matches(request.requestURI)
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
                    "GET" to "/api/v1/auth/options",
                    "GET" to "/api/v1/auth/identities",
                    "GET" to "/api/v1/brokerage/mock/credential",
                    "PUT" to "/api/v1/brokerage/mock/credential",
                    "DELETE" to "/api/v1/brokerage/mock/credential",
                    "POST" to "/api/v1/brokerage/mock/credential/connect",
                    "POST" to "/api/v1/brokerage/mock/credential/certify",
                    "POST" to "/api/v1/brokerage/mock/credential/certify/recovery-confirm",
                    "POST" to "/api/v1/brokerage/mock/credential/certify",
                    -> true
                    else ->
                        (request.method == "POST" && FULL_PROVIDER_LINK_START.matches(request.requestURI)) ||
                            (request.method == "DELETE" && FULL_PROVIDER_UNLINK.matches(request.requestURI))
                }
        val fullAutomationAllowed =
            mode == PublicSurfaceMode.FULL &&
                when (request.method to request.requestURI) {
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
        if (
            mode != PublicSurfaceMode.LOCAL &&
            !health &&
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
