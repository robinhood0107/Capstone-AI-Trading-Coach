package com.capstone.decision.api.common

import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.http.MediaType
import org.springframework.stereotype.Component
import tools.jackson.databind.ObjectMapper
import java.nio.charset.StandardCharsets

@Component
class ApiResponseWriter(
    private val objectMapper: ObjectMapper,
) {
    // 왜: Security filter 단계의 401/403은 MVC advice를 거치지 않아 직접 envelope를 써야 한다.
    fun writeError(
        request: HttpServletRequest,
        response: HttpServletResponse,
        code: ErrorCode,
        message: String = code.defaultMessage,
        details: Map<String, Any?> = emptyMap(),
    ) {
        val requestId = RequestIds.currentOrCreate(request)
        // 왜: 보안 오류에서도 requestId header와 body 값을 같게 맞춰 로그 추적을 끊지 않는다.
        response.status = code.status.value()
        response.contentType = MediaType.APPLICATION_JSON_VALUE
        response.characterEncoding = StandardCharsets.UTF_8.name()
        response.setHeader(RequestIds.HEADER, requestId)
        objectMapper.writeValue(
            response.outputStream,
            ApiResponseFactory.error(
                requestId = requestId,
                code = code,
                message = message,
                details = details,
            ),
        )
    }
}
