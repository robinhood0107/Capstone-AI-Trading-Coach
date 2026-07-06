package com.capstone.decision.api.common

import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.ResponseEntity
import org.springframework.http.converter.HttpMessageNotReadableException
import org.springframework.web.bind.MethodArgumentNotValidException
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice
import org.springframework.web.servlet.NoHandlerFoundException
import org.springframework.web.servlet.resource.NoResourceFoundException

// controller에서 발생한 오류를 모두 API 명세의 공통 envelope로 수렴시킨다.
@RestControllerAdvice
class GlobalExceptionHandler {
    @ExceptionHandler(ApiException::class)
    fun handleApiException(
        exception: ApiException,
        request: HttpServletRequest,
    ): ResponseEntity<ApiResponse<Nothing>> =
        errorResponse(
            request = request,
            code = exception.errorCode,
            message = exception.message,
            details = exception.details,
        )

    @ExceptionHandler(MethodArgumentNotValidException::class)
    fun handleValidation(
        exception: MethodArgumentNotValidException,
        request: HttpServletRequest,
    ): ResponseEntity<ApiResponse<Nothing>> {
        // 필드별 사유를 details에 보존해야 프론트가 입력 오류를 위치별로 표시할 수 있다.
        val fieldErrors =
            exception.bindingResult.fieldErrors.associate { fieldError ->
                fieldError.field to (fieldError.defaultMessage ?: "Invalid value.")
            }
        return errorResponse(
            request = request,
            code = ErrorCode.VALIDATION_ERROR,
            details = mapOf("fields" to fieldErrors),
        )
    }

    @ExceptionHandler(HttpMessageNotReadableException::class)
    fun handleUnreadableBody(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> =
        errorResponse(
            request = request,
            code = ErrorCode.VALIDATION_ERROR,
            details = mapOf("body" to "Malformed JSON request body."),
        )

    @ExceptionHandler(NoHandlerFoundException::class, NoResourceFoundException::class)
    fun handleNotFound(request: HttpServletRequest): ResponseEntity<ApiResponse<Nothing>> =
        // 존재하지 않는 API도 HTML/빈 응답 대신 NOT_FOUND code를 반환해야 한다.
        errorResponse(
            request = request,
            code = ErrorCode.NOT_FOUND,
            details = mapOf("path" to request.requestURI),
        )

    private fun errorResponse(
        request: HttpServletRequest,
        code: ErrorCode,
        message: String = code.defaultMessage,
        details: Map<String, Any?> = emptyMap(),
    ): ResponseEntity<ApiResponse<Nothing>> =
        // HTTP status와 body error.code를 동시에 맞춰 운영 로그와 클라이언트 분기를 모두 만족한다.
        ResponseEntity
            .status(code.status)
            .body(
                ApiResponseFactory.error(
                    requestId = RequestIds.currentOrCreate(request),
                    code = code,
                    message = message,
                    details = details,
                ),
            )
}
