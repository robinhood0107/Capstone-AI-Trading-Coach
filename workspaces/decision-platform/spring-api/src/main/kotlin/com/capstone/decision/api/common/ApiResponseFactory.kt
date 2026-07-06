package com.capstone.decision.api.common

// controller/advice/filter가 같은 envelope 생성 규칙을 공유해 응답 모양이 흩어지지 않게 한다.
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
    ): ApiResponse<Nothing> {
        // error.code는 enum name 그대로 내려 API 문서와 테스트가 같은 문자열을 보게 한다.
        val error =
            ApiError(
                code = code.name,
                message = message,
                details = details,
            )
        return ApiResponse(
            success = false,
            requestId = requestId,
            data = null,
            warnings = emptyList(),
            error = error,
        )
    }
}
