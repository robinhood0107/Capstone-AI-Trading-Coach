package com.capstone.decision.infrastructure.web

import com.capstone.decision.api.common.RequestIds
import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.slf4j.LoggerFactory
import org.slf4j.MDC
import org.springframework.web.filter.OncePerRequestFilter
import java.util.UUID

// requestId는 인증 성공 여부와 무관하게 모든 요청의 header/body/log에 먼저 실려야 한다.
class RequestIdFilter : OncePerRequestFilter() {
    private val log = LoggerFactory.getLogger(RequestIdFilter::class.java)

    override fun doFilterInternal(
        request: HttpServletRequest,
        response: HttpServletResponse,
        filterChain: FilterChain,
    ) {
        val requestId = RequestIds.fromClientHeader(request.getHeader(RequestIds.HEADER)) ?: RequestIds.generate()
        val traceId = UUID.randomUUID().toString().replace("-", "")
        val spanId = traceId.takeLast(16)
        MDC.put(RequestIds.MDC_KEY, requestId)
        MDC.put(TRACE_ID_MDC_KEY, traceId)
        MDC.put(SPAN_ID_MDC_KEY, spanId)
        response.setHeader(RequestIds.HEADER, requestId)
        try {
            // JSON 로그에 requestId가 들어가는지 smoke/test가 확인할 관측 지점을 만든다.
            log.info("request.received")
            filterChain.doFilter(request, response)
        } finally {
            // thread 재사용 시 이전 요청의 MDC가 다음 요청 로그에 섞이지 않게 지운다.
            log.info("request.completed")
            MDC.remove(RequestIds.MDC_KEY)
            MDC.remove(TRACE_ID_MDC_KEY)
            MDC.remove(SPAN_ID_MDC_KEY)
        }
    }

    private companion object {
        const val TRACE_ID_MDC_KEY = "trace_id"
        const val SPAN_ID_MDC_KEY = "span_id"
    }
}
