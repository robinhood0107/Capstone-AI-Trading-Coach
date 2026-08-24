package com.capstone.decision.infrastructure.mcp

import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.web.filter.OncePerRequestFilter

/** Keeps the one-request refresh claim from leaking across pooled servlet threads. */
class McpRefreshClaimCleanupFilter(
    private val claims: McpRefreshClaimContext,
) : OncePerRequestFilter() {
    override fun doFilterInternal(
        request: HttpServletRequest,
        response: HttpServletResponse,
        filterChain: FilterChain,
    ) {
        claims.clear()
        try {
            filterChain.doFilter(request, response)
        } finally {
            claims.clear()
        }
    }
}
