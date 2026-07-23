package com.capstone.decision.api.principle

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.domain.principle.PrincipleConflictException
import com.capstone.decision.domain.principle.PrincipleNotFoundException
import com.capstone.decision.domain.principle.PrincipleValidationException
import com.capstone.decision.domain.principle.PrincipleVersionExhaustedException
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice

// S2.1 오류는 exact allowlisted details만 내리고 target/owner/rejected value는 응답하지 않는다.
@RestControllerAdvice(assignableTypes = [PrincipleController::class])
class PrincipleExceptionHandler {
    @ExceptionHandler(PrincipleValidationException::class)
    fun handleValidation(
        exception: PrincipleValidationException,
        request: HttpServletRequest,
    ): ResponseEntity<ApiResponse<Nothing>> =
        error(
            request = request,
            code = ErrorCode.VALIDATION_ERROR,
            details = mapOf("violations" to exception.violations),
        )

    @ExceptionHandler(PrincipleNotFoundException::class)
    fun handleNotFound(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.NOT_FOUND)

    @ExceptionHandler(PrincipleConflictException::class)
    fun handleConflict(
        exception: PrincipleConflictException,
        request: HttpServletRequest,
    ): ResponseEntity<ApiResponse<Nothing>> =
        error(
            request = request,
            code = ErrorCode.CONFLICT,
            details =
                mapOf(
                    "expectedVersion" to exception.expectedVersion,
                    "currentVersion" to exception.currentVersion,
                ),
        )

    @ExceptionHandler(PrincipleVersionExhaustedException::class)
    fun handleVersionExhausted(
        exception: PrincipleVersionExhaustedException,
        request: HttpServletRequest,
    ): ResponseEntity<ApiResponse<Nothing>> =
        error(
            request = request,
            code = ErrorCode.VERSION_EXHAUSTED,
            details = mapOf("currentVersion" to exception.currentVersion),
        )

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
