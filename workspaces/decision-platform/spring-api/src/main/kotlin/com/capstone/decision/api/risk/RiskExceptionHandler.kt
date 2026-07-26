package com.capstone.decision.api.risk

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.risk.KillSwitchConflictException
import com.capstone.decision.application.risk.KillSwitchForbiddenException
import com.capstone.decision.application.risk.KillSwitchUnauthorizedException
import com.capstone.decision.application.risk.KillSwitchUnavailableException
import com.capstone.decision.application.risk.RiskValidationException
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice

@RestControllerAdvice(assignableTypes = [RiskController::class])
class RiskExceptionHandler {
    @ExceptionHandler(RiskValidationException::class)
    fun handleValidation(
        exception: RiskValidationException,
        request: HttpServletRequest,
    ): ResponseEntity<ApiResponse<Nothing>> =
        error(
            request,
            ErrorCode.VALIDATION_ERROR,
            mapOf("violations" to exception.violations),
        )

    @ExceptionHandler(KillSwitchUnauthorizedException::class)
    fun handleUnauthorized(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.UNAUTHORIZED)

    @ExceptionHandler(KillSwitchForbiddenException::class)
    fun handleForbidden(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.FORBIDDEN)

    @ExceptionHandler(KillSwitchConflictException::class)
    fun handleConflict(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.CONFLICT)

    @ExceptionHandler(KillSwitchUnavailableException::class)
    fun handleUnavailable(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.RISK_UNAVAILABLE)

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
