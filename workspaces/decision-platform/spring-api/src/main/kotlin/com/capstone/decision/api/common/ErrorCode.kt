package com.capstone.decision.api.common

import org.springframework.http.HttpStatus
import org.springframework.http.HttpStatusCode

// 명세의 오류 코드와 HTTP 상태 매핑을 한 곳에 모아 drift를 줄인다.
enum class ErrorCode(
    val status: HttpStatusCode,
    val defaultMessage: String,
) {
    VALIDATION_ERROR(HttpStatus.BAD_REQUEST, "Request validation failed."),
    UNAUTHORIZED(HttpStatus.UNAUTHORIZED, "Authentication is required."),
    FORBIDDEN(HttpStatus.FORBIDDEN, "Access is denied."),
    NOT_FOUND(HttpStatus.NOT_FOUND, "Resource was not found."),
    CONFLICT(HttpStatus.CONFLICT, "Resource conflict."),
    VERSION_EXHAUSTED(HttpStatus.CONFLICT, "Principle version limit was reached."),
    DECISION_EXPIRED(HttpStatus.CONFLICT, "Decision validity window has expired."),
    IDEMPOTENCY_CONFLICT(HttpStatus.CONFLICT, "Idempotency key was reused with a different payload."),
    IDEMPOTENCY_IN_PROGRESS(HttpStatus.CONFLICT, "A request with this idempotency key is already in progress."),
    PAYLOAD_TOO_LARGE(HttpStatusCode.valueOf(413), "Request payload exceeded the configured safety limit."),
    RISK_BLOCKED(HttpStatusCode.valueOf(422), "Request was blocked by risk controls."),
    RISK_UNAVAILABLE(HttpStatus.SERVICE_UNAVAILABLE, "Risk controls are unavailable."),
    RAG_UNAVAILABLE(HttpStatus.SERVICE_UNAVAILABLE, "RAG source registry is unavailable."),
    DATA_STALE(HttpStatus.CONFLICT, "Required data is stale."),
    RATE_LIMITED(HttpStatus.TOO_MANY_REQUESTS, "Rate limit exceeded."),
    PYTHON_SERVICE_UNAVAILABLE(HttpStatus.SERVICE_UNAVAILABLE, "Python service is unavailable."),
    BROKERAGE_UNAVAILABLE(HttpStatus.SERVICE_UNAVAILABLE, "Brokerage service is unavailable."),
    INTERNAL_ERROR(HttpStatus.INTERNAL_SERVER_ERROR, "The request failed closed."),
}
