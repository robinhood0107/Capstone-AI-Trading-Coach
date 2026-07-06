package com.capstone.decision.infrastructure.web

import com.capstone.decision.api.common.RequestIds
import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.slf4j.LoggerFactory
import org.slf4j.MDC
import org.springframework.web.filter.OncePerRequestFilter

// 왜: requestId는 인증 성공 여부와 무관하게 모든 요청의 header/body/log에 먼저 실려야 한다.
class RequestIdFilter : OncePerRequestFilter() {
    private val log = LoggerFactory.getLogger(RequestIdFilter::class.java)

    override fun doFilterInternal(
        request: HttpServletRequest,
        response: HttpServletResponse,
        filterChain: FilterChain,
    ) {
        val requestId = request.getHeader(RequestIds.HEADER)?.takeIf { it.isNotBlank() } ?: RequestIds.generate()
        MDC.put(RequestIds.MDC_KEY, requestId)
        response.setHeader(RequestIds.HEADER, requestId)
        try {
            // 왜: JSON 로그에 requestId가 들어가는지 smoke/test가 확인할 관측 지점을 만든다.
            log.info("request.received")
            filterChain.doFilter(request, response)
        } finally {
            // 왜: thread 재사용 시 이전 요청의 MDC가 다음 요청 로그에 섞이지 않게 지운다.
            log.info("request.completed")
            MDC.remove(RequestIds.MDC_KEY)
        }
    }
}
