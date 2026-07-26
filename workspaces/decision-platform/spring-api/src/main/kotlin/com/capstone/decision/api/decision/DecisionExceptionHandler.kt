package com.capstone.decision.api.decision

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.decision.DecisionIdempotencyConflictException
import com.capstone.decision.application.decision.DecisionIdempotencyInProgressException
import com.capstone.decision.application.decision.DecisionNotFoundException
import com.capstone.decision.application.decision.DecisionTechnicalException
import com.capstone.decision.application.decision.DecisionValidationException
import com.capstone.decision.application.decision.DecisionVersionConflictException
import com.capstone.decision.application.risk.KillSwitchBlockedException
import com.capstone.decision.application.risk.KillSwitchUnavailableException
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice

// S2.3 오류 응답에는 allowlisted code/field만 넣고 exception, owner, source detail은 반사하지 않는다.
@RestControllerAdvice(assignableTypes = [DecisionController::class])
class DecisionExceptionHandler {
    @ExceptionHandler(DecisionValidationException::class)
    fun handleValidation(
        exception: DecisionValidationException,
        request: HttpServletRequest,
    ): ResponseEntity<ApiResponse<Nothing>> =
        error(
            request = request,
            code = ErrorCode.VALIDATION_ERROR,
            details = mapOf("violations" to exception.violations),
        )

    @ExceptionHandler(DecisionNotFoundException::class)
    fun handleNotFound(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.NOT_FOUND)

    @ExceptionHandler(DecisionIdempotencyConflictException::class)
    fun handleIdempotencyConflict(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> =
        error(request, ErrorCode.IDEMPOTENCY_CONFLICT)

    @ExceptionHandler(DecisionIdempotencyInProgressException::class)
    fun handleIdempotencyInProgress(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> =
        error(request, ErrorCode.IDEMPOTENCY_IN_PROGRESS)

    @ExceptionHandler(DecisionVersionConflictException::class)
    fun handleVersionConflict(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.CONFLICT)

    @ExceptionHandler(DecisionTechnicalException::class)
    fun handleTechnicalFailure(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.INTERNAL_ERROR)

    @ExceptionHandler(KillSwitchBlockedException::class)
    fun handleKillSwitchBlocked(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.RISK_BLOCKED)

    @ExceptionHandler(KillSwitchUnavailableException::class)
    fun handleKillSwitchUnavailable(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> =
        error(request, ErrorCode.RISK_UNAVAILABLE)

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
