package com.capstone.decision.api.common

import org.springframework.http.HttpStatus
import org.springframework.http.HttpStatusCode

enum class ErrorCode(
    val status: HttpStatusCode,
    val defaultMessage: String,
) {
    VALIDATION_ERROR(HttpStatus.BAD_REQUEST, "Request validation failed."),
    UNAUTHORIZED(HttpStatus.UNAUTHORIZED, "Authentication is required."),
    FORBIDDEN(HttpStatus.FORBIDDEN, "Access is denied."),
    NOT_FOUND(HttpStatus.NOT_FOUND, "Resource was not found."),
    CONFLICT(HttpStatus.CONFLICT, "Resource conflict."),
    IDEMPOTENCY_CONFLICT(HttpStatus.CONFLICT, "Idempotency key was reused with a different payload."),
    RISK_BLOCKED(HttpStatusCode.valueOf(422), "Request was blocked by risk controls."),
    DATA_STALE(HttpStatus.CONFLICT, "Required data is stale."),
    RATE_LIMITED(HttpStatus.TOO_MANY_REQUESTS, "Rate limit exceeded."),
    PYTHON_SERVICE_UNAVAILABLE(HttpStatus.SERVICE_UNAVAILABLE, "Python service is unavailable."),
    BROKERAGE_UNAVAILABLE(HttpStatus.SERVICE_UNAVAILABLE, "Brokerage service is unavailable."),
}
