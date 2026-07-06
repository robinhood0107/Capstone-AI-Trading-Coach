package com.capstone.decision.infrastructure.security

import com.capstone.decision.api.common.ApiResponseWriter
import com.capstone.decision.api.common.ErrorCode
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.security.access.AccessDeniedException
import org.springframework.security.web.access.AccessDeniedHandler

// 권한 실패는 MVC controller에 도달하지 않으므로 Security 전용 handler에서 envelope를 보장한다.
class ApiAccessDeniedHandler(
    private val responseWriter: ApiResponseWriter,
) : AccessDeniedHandler {
    override fun handle(
        request: HttpServletRequest,
        response: HttpServletResponse,
        accessDeniedException: AccessDeniedException,
    ) {
        responseWriter.writeError(
            request = request,
            response = response,
            code = ErrorCode.FORBIDDEN,
        )
    }
}
