package com.capstone.decision.infrastructure.web

import com.capstone.decision.api.common.ApiResponseWriter
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.domain.risk.EvaluationBounds
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
        val maxBytes =
            if (
                request.method == "POST" &&
                request.requestURI == DECISION_EVALUATE_PATH
            ) {
                EvaluationBounds.MAX_REQUEST_BYTES
            } else {
                properties.maxRequestBodyBytes
            }
        val boundedRequest =
            try {
                CachedBodyHttpServletRequest(request, maxBytes)
            } catch (_: RequestBodyTooLargeException) {
                responseWriter.writeError(
                    request = request,
                    response = response,
                    code = ErrorCode.PAYLOAD_TOO_LARGE,
                    details = mapOf("maxBytes" to maxBytes),
                )
                return
            }
        filterChain.doFilter(boundedRequest, response)
    }

    private companion object {
        const val DECISION_EVALUATE_PATH = "/api/v1/decisions/evaluate-order"
    }
}
