package com.capstone.decision.infrastructure.web

import com.capstone.decision.api.common.ApiResponseWriter
import com.capstone.decision.api.common.ErrorCode
import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.web.filter.OncePerRequestFilter

// Content-Length가 없거나 거짓이어도 실제 stream을 max+1까지만 읽어 JSON binding 전 메모리 증폭을 막는다.
class RequestBodyLimitFilter(
    private val properties: HttpRequestProperties,
    private val responseWriter: ApiResponseWriter,
) : OncePerRequestFilter() {
    override fun doFilterInternal(
        request: HttpServletRequest,
        response: HttpServletResponse,
        filterChain: FilterChain,
    ) {
        val boundedRequest =
            try {
                CachedBodyHttpServletRequest(request, properties.maxRequestBodyBytes)
            } catch (_: RequestBodyTooLargeException) {
                responseWriter.writeError(request, response, ErrorCode.PAYLOAD_TOO_LARGE)
                return
            }
        filterChain.doFilter(boundedRequest, response)
    }
}
