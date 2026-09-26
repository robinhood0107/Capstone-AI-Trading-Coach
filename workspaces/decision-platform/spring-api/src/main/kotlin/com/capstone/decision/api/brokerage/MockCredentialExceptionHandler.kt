package com.capstone.decision.api.brokerage

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.brokerage.BrokerageUnavailableException
import jakarta.servlet.http.HttpServletRequest
import org.springframework.dao.DataAccessException
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice
import java.sql.SQLException

/** Never echoes an input, ciphertext, account number, or JDBC error text. */
@RestControllerAdvice(
    assignableTypes = [
        MockCredentialController::class,
        MockCredentialConnectionController::class,
        MockCredentialDisconnectController::class,
        MockCredentialCertificationController::class,
    ],
)
class MockCredentialExceptionHandler {
    @ExceptionHandler(IllegalArgumentException::class)
    fun invalid(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.VALIDATION_ERROR)

    @ExceptionHandler(IllegalStateException::class)
    fun unavailable(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.BROKERAGE_UNAVAILABLE)

    // 가짜·만료 키로 연결 확인을 누르면 gRPC 경계가 닫힌다. 예전에는 이 예외가 어느 handler 에도
    // 잡히지 않아 /error 로 넘어갔고, 화면은 "로그인이 필요합니다"(401)를 띄웠다.
    @ExceptionHandler(BrokerageUnavailableException::class)
    fun brokerage(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.BROKERAGE_UNAVAILABLE)

    @ExceptionHandler(DataAccessException::class)
    fun database(
        exception: DataAccessException,
        request: HttpServletRequest,
    ): ResponseEntity<ApiResponse<Nothing>> {
        val cause = exception.mostSpecificCause as? SQLException
        if (cause?.sqlState != "40001") return error(request, ErrorCode.BROKERAGE_UNAVAILABLE)
        // 사용자가 스스로 풀 수 있는 충돌은 이유를 details.reason 으로 알린다. 문구는 DB 함수가
        // 고정한 값만 대조하고, 원문은 응답에 싣지 않는다.
        val reason = CONFLICT_REASONS[serverMessage(cause)]
        return error(
            request,
            ErrorCode.CONFLICT,
            reason?.let { mapOf("reason" to it) } ?: emptyMap(),
        )
    }

    private fun error(
        request: HttpServletRequest,
        code: ErrorCode,
        details: Map<String, Any?> = emptyMap(),
    ): ResponseEntity<ApiResponse<Nothing>> =
        ResponseEntity
            .status(code.status)
            .body(ApiResponseFactory.error(RequestIds.currentOrCreate(request), code, details = details))

    internal companion object {
        /** PostgreSQL 드라이버는 "ERROR: <문구>\n  Where: ..." 로 싣는다. 첫 줄의 문구만 떼어 대조한다. */
        fun serverMessage(cause: SQLException): String? =
            cause.message
                ?.lineSequence()
                ?.firstOrNull()
                ?.removePrefix("ERROR: ")
                ?.trim()

        /** V199/V205 가 40001 로 올리는 사유 중 화면이 행동을 안내할 수 있는 것. */
        val CONFLICT_REASONS: Map<String, String> =
            mapOf(
                "mock credential cannot change while armed" to "AUTOMATION_ARMED",
                "mock credential has pending reconciliation" to "PENDING_RECONCILIATION",
                "mock credential has pending execution" to "PENDING_EXECUTION",
                "mock credential has pending provider reconciliation" to "PENDING_RECONCILIATION",
            )
    }
}
