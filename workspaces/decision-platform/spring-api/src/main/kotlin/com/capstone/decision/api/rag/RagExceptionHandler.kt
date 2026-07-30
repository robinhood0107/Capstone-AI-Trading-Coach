package com.capstone.decision.api.rag

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.rag.RagSourceRegistryUnavailableException
import com.capstone.decision.application.rag.RagValidationException
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice

@RestControllerAdvice(assignableTypes = [RagController::class])
class RagExceptionHandler {
    @ExceptionHandler(RagValidationException::class)
    fun handleValidation(
        exception: RagValidationException,
        request: HttpServletRequest,
    ): ResponseEntity<ApiResponse<Nothing>> =
        error(
            request = request,
            code = ErrorCode.VALIDATION_ERROR,
            details = mapOf("violations" to exception.violations),
        )

    @ExceptionHandler(RagSourceRegistryUnavailableException::class)
    fun handleUnavailable(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> =
        error(request = request, code = ErrorCode.RAG_UNAVAILABLE)

    private fun error(
        request: HttpServletRequest,
        code: ErrorCode,
        details: Map<String, Any?> = emptyMap(),
    ): ResponseEntity<ApiResponse<Nothing>> =
        ResponseEntity
            .status(code.status)
            .body(
                ApiResponseFactory.error(
                    requestId = RequestIds.currentOrCreate(request),
                    code = code,
                    details = details,
                ),
            )
}
