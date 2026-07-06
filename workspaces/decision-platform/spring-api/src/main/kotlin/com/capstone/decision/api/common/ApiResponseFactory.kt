package com.capstone.decision.api.common

object ApiResponseFactory {
    fun <T> success(
        requestId: String,
        data: T?,
        warnings: List<ApiWarning> = emptyList(),
    ): ApiResponse<T> =
        ApiResponse(
            success = true,
            requestId = requestId,
            data = data,
            warnings = warnings,
            error = null,
        )

    fun error(
        requestId: String,
        code: ErrorCode,
        message: String = code.defaultMessage,
        details: Map<String, Any?> = emptyMap(),
    ): ApiResponse<Nothing> =
        ApiResponse(
            success = false,
            requestId = requestId,
            data = null,
            warnings = emptyList(),
            error =
                ApiError(
                    code = code.name,
                    message = message,
                    details = details,
                ),
        )
}
