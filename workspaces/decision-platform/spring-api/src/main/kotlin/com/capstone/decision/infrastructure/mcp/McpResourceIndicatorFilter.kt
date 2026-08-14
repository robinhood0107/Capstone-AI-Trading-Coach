package com.capstone.decision.infrastructure.mcp

import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.web.filter.OncePerRequestFilter

/** authorization code와 refresh 교환 모두 exact `/mcp` resource indicator가 없으면 token 발급 전에 거부한다. */
internal class McpResourceIndicatorFilter(
    private val resourceUri: String,
) : OncePerRequestFilter() {
    override fun shouldNotFilter(request: HttpServletRequest): Boolean = request.requestURI !in setOf("/oauth2/authorize", "/oauth2/token")

    override fun doFilterInternal(
        request: HttpServletRequest,
        response: HttpServletResponse,
        filterChain: FilterChain,
    ) {
        val resources = request.parameterMap["resource"]?.toList().orEmpty()
        // Spring의 consent POST는 최초 GET에서 검증·저장한 authorization request를 세션에서 이어받는다.
        if (request.requestURI == "/oauth2/authorize" && request.method == "POST" && resources.isEmpty()) {
            filterChain.doFilter(request, response)
            return
        }
        if (resources.size != 1 || resources.single() != resourceUri) {
            response.sendError(HttpServletResponse.SC_BAD_REQUEST, "invalid_target")
            return
        }
        filterChain.doFilter(request, response)
    }
}
