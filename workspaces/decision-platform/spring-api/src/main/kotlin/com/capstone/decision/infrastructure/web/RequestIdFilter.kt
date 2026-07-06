package com.capstone.decision.infrastructure.web

import com.capstone.decision.api.common.RequestIds
import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.slf4j.LoggerFactory
import org.slf4j.MDC
import org.springframework.web.filter.OncePerRequestFilter

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
            log.info("request.received")
            filterChain.doFilter(request, response)
        } finally {
            log.info("request.completed")
            MDC.remove(RequestIds.MDC_KEY)
        }
    }
}
