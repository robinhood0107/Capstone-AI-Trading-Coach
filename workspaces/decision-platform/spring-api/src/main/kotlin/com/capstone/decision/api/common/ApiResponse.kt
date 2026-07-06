package com.capstone.decision.api.common

// 모든 REST 응답을 같은 최상위 모양으로 맞춰 프론트가 성공/실패를 한 규칙으로 처리한다.
data class ApiResponse<T>(
    val success: Boolean,
    val requestId: String,
    val data: T?,
    val warnings: List<ApiWarning> = emptyList(),
    val error: ApiError? = null,
)
