package com.capstone.decision.api.common

import jakarta.servlet.RequestDispatcher
import jakarta.servlet.http.HttpServletRequest
import org.springframework.boot.webmvc.error.ErrorController
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

@RestController
class ApiErrorController : ErrorController {
    @RequestMapping("/error")
    fun handleError(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> {
        val status = request.getAttribute(RequestDispatcher.ERROR_STATUS_CODE) as? Int ?: 500
        val errorCode = errorCodeFor(status)
        return ResponseEntity
            .status(status)
            .body(
                ApiResponseFactory.error(
                    requestId = RequestIds.currentOrCreate(request),
                    code = errorCode,
                    details =
                        mapOf(
                            "path" to (
                                request.getAttribute(RequestDispatcher.ERROR_REQUEST_URI)
                                    ?: request.requestURI
                            ),
                        ),
                ),
            )
    }

    private fun errorCodeFor(status: Int): ErrorCode =
        when (status) {
            400 -> ErrorCode.VALIDATION_ERROR
            401 -> ErrorCode.UNAUTHORIZED
            403 -> ErrorCode.FORBIDDEN
            404 -> ErrorCode.NOT_FOUND
            409 -> ErrorCode.CONFLICT
            422 -> ErrorCode.RISK_BLOCKED
            429 -> ErrorCode.RATE_LIMITED
            503 -> ErrorCode.PYTHON_SERVICE_UNAVAILABLE
            else -> ErrorCode.CONFLICT
        }
}
