package com.capstone.decision.infrastructure.security

import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.beans.factory.annotation.Value
import org.springframework.boot.web.servlet.FilterRegistrationBean
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.core.Ordered
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
        if (mode != PublicSurfaceMode.LOCAL && !health) {
            response.sendError(HttpServletResponse.SC_NOT_FOUND)
            return
        }
        filterChain.doFilter(request, response)
    }
}

@Configuration
internal class PublicSurfaceGateConfiguration {
    @Bean
    fun publicSurfaceGate(
        @Value("\${MARS_PUBLIC_SURFACE_MODE:LOCAL}") rawMode: String,
    ): FilterRegistrationBean<PublicSurfaceGate> {
        val mode = PublicSurfaceMode.valueOf(rawMode)
        return FilterRegistrationBean(PublicSurfaceGate(mode)).apply {
            order = Ordered.HIGHEST_PRECEDENCE
            addUrlPatterns("/*")
        }
    }
}
