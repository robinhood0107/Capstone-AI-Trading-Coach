package com.capstone.decision.api.rag

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.rag.RagGuardHistoryUnavailableException
import com.capstone.decision.application.rag.RagHistoryCorruptedException
import com.capstone.decision.application.rag.RagHistoryNotFoundException
import com.capstone.decision.application.rag.RagHistoryPersistFailedException
import com.capstone.decision.application.rag.RagIdempotencyConflictException
import com.capstone.decision.application.rag.RagIdempotencyInProgressException
import com.capstone.decision.application.rag.RagIdempotencyResultUnavailableException
import com.capstone.decision.application.rag.RagRateLimitedException
import com.capstone.decision.application.rag.RagSourceRegistryUnavailableException
import com.capstone.decision.application.rag.RagValidationException
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice

@RestControllerAdvice(assignableTypes = [RagController::class, RagConsentController::class])
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

    @ExceptionHandler(RagIdempotencyConflictException::class)
    fun handleIdempotencyConflict(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> =
        error(request, ErrorCode.IDEMPOTENCY_CONFLICT)

    @ExceptionHandler(RagIdempotencyInProgressException::class)
    fun handleIdempotencyInProgress(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> =
        error(request, ErrorCode.IDEMPOTENCY_IN_PROGRESS)

    @ExceptionHandler(RagIdempotencyResultUnavailableException::class)
    fun handleIdempotencyUnavailable(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> =
        error(request, ErrorCode.IDEMPOTENCY_RESULT_UNAVAILABLE)

    @ExceptionHandler(RagHistoryNotFoundException::class)
    fun handleNotFound(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.NOT_FOUND)

    @ExceptionHandler(RagHistoryPersistFailedException::class)
    fun handlePersistFailure(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> =
        error(request, ErrorCode.RAG_HISTORY_PERSIST_FAILED)

    @ExceptionHandler(RagHistoryCorruptedException::class)
    fun handleCorruption(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> =
        error(request, ErrorCode.RAG_HISTORY_CORRUPTED)

    @ExceptionHandler(RagRateLimitedException::class)
    fun handleRateLimit(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.RATE_LIMITED)

    @ExceptionHandler(RagGuardHistoryUnavailableException::class)
    fun handleGuardHistoryUnavailable(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> =
        error(request, ErrorCode.RAG_UNAVAILABLE)

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
