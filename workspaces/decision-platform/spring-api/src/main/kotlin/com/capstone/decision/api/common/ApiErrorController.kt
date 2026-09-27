package com.capstone.decision.api.common

import jakarta.servlet.RequestDispatcher
import jakarta.servlet.http.HttpServletRequest
import org.springframework.boot.webmvc.error.ErrorController
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

// 서블릿 컨테이너가 만든 /error 응답도 API 영역에서는 빈 404/500이 아니라 envelope여야 한다.
@RestController
class ApiErrorController : ErrorController {
    @RequestMapping("/error")
    fun handleError(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> {
        val status = request.getAttribute(RequestDispatcher.ERROR_STATUS_CODE) as? Int ?: 500
        val errorCode = errorCodeFor(status)
        // 미매핑 경로를 details에 남겨 swagger/manual smoke에서 원인을 바로 볼 수 있게 한다.
        val details =
            mapOf(
                "path" to (
                    request.getAttribute(RequestDispatcher.ERROR_REQUEST_URI)
                        ?: request.requestURI
                ),
            )
        return ResponseEntity
            .status(status)
            .body(
                ApiResponseFactory.error(
                    requestId = RequestIds.currentOrCreate(request),
                    code = errorCode,
                    details = details,
                ),
            )
    }

    // 컨테이너 status를 공개 error.code로 재매핑해 클라이언트 분기 규칙을 유지한다.
    private fun errorCodeFor(status: Int): ErrorCode =
        when (status) {
            400 -> ErrorCode.VALIDATION_ERROR
            401 -> ErrorCode.UNAUTHORIZED
            403 -> ErrorCode.FORBIDDEN
            404 -> ErrorCode.NOT_FOUND
            // 메서드·미디어 타입 불일치는 요청 형식 문제다. CONFLICT 로 보내면 화면이 "다른 변경과
            // 충돌"이라는 원인과 무관한 문구를 띄운다.
            405, 415 -> ErrorCode.VALIDATION_ERROR
            409 -> ErrorCode.CONFLICT
            422 -> ErrorCode.RISK_BLOCKED
            429 -> ErrorCode.RATE_LIMITED
            // 처리되지 않은 예외는 서버 오류다. 충돌로 보내면 재시도 안내도 사라진다.
            500 -> ErrorCode.INTERNAL_ERROR
            503 -> ErrorCode.PYTHON_SERVICE_UNAVAILABLE
            else -> ErrorCode.CONFLICT
        }
}
