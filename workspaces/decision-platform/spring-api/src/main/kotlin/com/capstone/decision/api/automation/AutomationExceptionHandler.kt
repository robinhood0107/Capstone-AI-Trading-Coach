package com.capstone.decision.api.automation

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.automation.AutomationAccessDeniedException
import com.capstone.decision.application.automation.AutomationBlockedException
import com.capstone.decision.application.automation.AutomationConflictException
import com.capstone.decision.application.automation.AutomationIdempotencyConflictException
import com.capstone.decision.application.automation.AutomationNotFoundException
import com.capstone.decision.application.automation.AutomationStorageException
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice

@RestControllerAdvice(assignableTypes = [AutomationController::class, AutomationV2Controller::class])
class AutomationExceptionHandler {
    @ExceptionHandler(AutomationIdempotencyConflictException::class)
    fun idempotency(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.IDEMPOTENCY_CONFLICT)

    @ExceptionHandler(AutomationConflictException::class)
    fun conflict(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.CONFLICT)

    @ExceptionHandler(AutomationBlockedException::class)
    fun blocked(
        exception: AutomationBlockedException,
        request: HttpServletRequest,
    ): ResponseEntity<ApiResponse<Nothing>> =
        ResponseEntity.status(ErrorCode.CONFLICT.status).body(
            ApiResponseFactory.error(
                RequestIds.currentOrCreate(request),
                ErrorCode.CONFLICT,
                details = mapOf("blocker" to exception.reason),
            ),
        )

    @ExceptionHandler(AutomationNotFoundException::class)
    fun notFound(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.NOT_FOUND)

    @ExceptionHandler(AutomationAccessDeniedException::class)
    fun denied(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.FORBIDDEN)

    @ExceptionHandler(AutomationStorageException::class)
    fun unavailable(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.INTERNAL_ERROR)

    @ExceptionHandler(IllegalArgumentException::class)
    fun invalid(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.VALIDATION_ERROR)

    private fun error(
        request: HttpServletRequest,
        code: ErrorCode,
    ): ResponseEntity<ApiResponse<Nothing>> =
        ResponseEntity.status(code.status).body(ApiResponseFactory.error(RequestIds.currentOrCreate(request), code))
}
