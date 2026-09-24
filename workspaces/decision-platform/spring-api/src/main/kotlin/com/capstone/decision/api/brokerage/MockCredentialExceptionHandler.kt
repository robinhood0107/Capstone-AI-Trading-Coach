package com.capstone.decision.api.brokerage

import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import jakarta.servlet.http.HttpServletRequest
import org.springframework.dao.DataAccessException
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice
import java.sql.SQLException

/** Never echoes an input, ciphertext, account number, or JDBC error text. */
@RestControllerAdvice(assignableTypes = [MockCredentialController::class])
class MockCredentialExceptionHandler {
    @ExceptionHandler(IllegalArgumentException::class)
    fun invalid(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.VALIDATION_ERROR)

    @ExceptionHandler(IllegalStateException::class)
    fun unavailable(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> = error(request, ErrorCode.BROKERAGE_UNAVAILABLE)

    @ExceptionHandler(DataAccessException::class)
    fun database(
        exception: DataAccessException,
        request: HttpServletRequest,
    ): ResponseEntity<ApiResponse<Nothing>> =
        error(
            request,
            if ((exception.mostSpecificCause as? SQLException)?.sqlState ==
                "40001"
            ) {
                ErrorCode.CONFLICT
            } else {
                ErrorCode.BROKERAGE_UNAVAILABLE
            },
        )

    private fun error(
        request: HttpServletRequest,
        code: ErrorCode,
    ): ResponseEntity<ApiResponse<Nothing>> =
        ResponseEntity.status(code.status).body(ApiResponseFactory.error(RequestIds.currentOrCreate(request), code))
}
