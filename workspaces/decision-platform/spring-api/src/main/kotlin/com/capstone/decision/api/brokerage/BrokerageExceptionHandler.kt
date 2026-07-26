package com.capstone.decision.api.brokerage

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.brokerage.BrokerageDecisionConflictException
import com.capstone.decision.application.brokerage.BrokerageDecisionNotFoundException
import com.capstone.decision.application.brokerage.BrokerageIdempotencyConflictException
import com.capstone.decision.application.brokerage.BrokerageOrderNotFoundException
import com.capstone.decision.application.brokerage.BrokerageUnavailableException
import com.capstone.decision.application.brokerage.BrokerageValidationException
import com.capstone.decision.application.brokerage.DecisionExpiredException
import com.capstone.decision.application.risk.KillSwitchBlockedException
import com.capstone.decision.application.risk.KillSwitchUnavailableException
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice

@RestControllerAdvice(assignableTypes = [BrokerageController::class])
class BrokerageExceptionHandler {
    @ExceptionHandler(BrokerageValidationException::class)
    fun handleValidation(
        exception: BrokerageValidationException,
        request: HttpServletRequest,
    ): ResponseEntity<ApiResponse<Nothing>> =
        error(
            request,
            ErrorCode.VALIDATION_ERROR,
            details =
                mapOf(
                    "violations" to exception.violations.map { mapOf("field" to it.field, "reason" to it.reason) },
                ),
        )

    @ExceptionHandler(BrokerageDecisionNotFoundException::class, BrokerageOrderNotFoundException::class)
    fun handleNotFound(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.NOT_FOUND)

    @ExceptionHandler(DecisionExpiredException::class)
    fun handleDecisionExpired(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> =
        error(request, ErrorCode.DECISION_EXPIRED)

    @ExceptionHandler(BrokerageDecisionConflictException::class)
    fun handleDecisionConflict(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.CONFLICT)

    @ExceptionHandler(BrokerageIdempotencyConflictException::class)
    fun handleIdempotencyConflict(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> =
        error(request, ErrorCode.IDEMPOTENCY_CONFLICT)

    @ExceptionHandler(KillSwitchBlockedException::class)
    fun handleRiskBlocked(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.RISK_BLOCKED)

    @ExceptionHandler(KillSwitchUnavailableException::class)
    fun handleRiskUnavailable(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> =
        error(request, ErrorCode.RISK_UNAVAILABLE)

    @ExceptionHandler(BrokerageUnavailableException::class)
    fun handleBrokerageUnavailable(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> =
        error(request, ErrorCode.BROKERAGE_UNAVAILABLE)

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
