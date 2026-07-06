package com.capstone.decision.api.common

import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.ResponseEntity
import org.springframework.http.converter.HttpMessageNotReadableException
import org.springframework.web.bind.MethodArgumentNotValidException
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice
import org.springframework.web.servlet.NoHandlerFoundException
import org.springframework.web.servlet.resource.NoResourceFoundException

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
