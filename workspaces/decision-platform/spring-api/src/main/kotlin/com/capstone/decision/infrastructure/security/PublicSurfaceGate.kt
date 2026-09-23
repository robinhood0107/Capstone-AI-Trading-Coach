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
                    "POST" to "/api/v1/auth/oidc/exchange",
                    "POST" to "/api/v1/auth/logout",
                    "GET" to "/api/v1/brokerage/mock/credential",
                    "PUT" to "/api/v1/brokerage/mock/credential",
                    "DELETE" to "/api/v1/brokerage/mock/credential",
                    "POST" to "/api/v1/brokerage/mock/credential/connect",
                    "GET" to "/api/v1/admin/ai-budget",
                    "PUT" to "/api/v1/admin/ai-budget",
                    -> true
                    else -> false
                }
        if (mode != PublicSurfaceMode.LOCAL && !health && !fullAllowed && !fullAgentAllowed && !demoAllowed) {
            response.sendError(HttpServletResponse.SC_NOT_FOUND)
            return
        }
        filterChain.doFilter(request, response)
    }

    private companion object {
        val FULL_RAG_HISTORY_DETAIL = Regex("^/api/v2/rag/history/rag_[A-Za-z0-9_-]{12,96}$")
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
