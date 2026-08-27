package com.capstone.decision.api.journal

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.journal.JournalAccessDeniedException
import com.capstone.decision.application.journal.JournalConflictException
import com.capstone.decision.application.journal.JournalIdempotencyConflictException
import com.capstone.decision.application.journal.JournalNotFoundException
import com.capstone.decision.application.journal.JournalStorageException
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice

@RestControllerAdvice(assignableTypes = [JournalController::class])
class JournalExceptionHandler {
    @ExceptionHandler(JournalIdempotencyConflictException::class)
    fun idempotency(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.IDEMPOTENCY_CONFLICT)

    @ExceptionHandler(JournalConflictException::class)
    fun conflict(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.CONFLICT)

    @ExceptionHandler(JournalNotFoundException::class)
    fun notFound(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.NOT_FOUND)

    @ExceptionHandler(JournalAccessDeniedException::class)
    fun denied(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.FORBIDDEN)

    @ExceptionHandler(JournalStorageException::class)
    fun unavailable(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.INTERNAL_ERROR)

    @ExceptionHandler(IllegalArgumentException::class)
    fun invalid(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.VALIDATION_ERROR)

    private fun error(
        request: HttpServletRequest,
        code: ErrorCode,
    ): ResponseEntity<ApiResponse<Nothing>> =
        ResponseEntity.status(code.status).body(ApiResponseFactory.error(RequestIds.currentOrCreate(request), code))
}
