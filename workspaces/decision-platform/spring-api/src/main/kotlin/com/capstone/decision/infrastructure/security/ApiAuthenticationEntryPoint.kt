package com.capstone.decision.infrastructure.security

import com.capstone.decision.api.common.ApiResponseWriter
import com.capstone.decision.api.common.ErrorCode
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.security.core.AuthenticationException
import org.springframework.security.web.AuthenticationEntryPoint

// 왜: 인증 실패를 Spring 기본 HTML/빈 응답 대신 API 명세의 UNAUTHORIZED envelope로 바꾼다.
class ApiAuthenticationEntryPoint(
    private val responseWriter: ApiResponseWriter,
) : AuthenticationEntryPoint {
    override fun commence(
        request: HttpServletRequest,
        response: HttpServletResponse,
        authException: AuthenticationException,
    ) {
        responseWriter.writeError(
            request = request,
            response = response,
            code = ErrorCode.UNAUTHORIZED,
        )
    }
}
