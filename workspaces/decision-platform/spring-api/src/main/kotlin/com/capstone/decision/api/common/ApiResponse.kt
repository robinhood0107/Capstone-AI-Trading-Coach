package com.capstone.decision.api.common

data class ApiResponse<T>(
    val success: Boolean,
    val requestId: String,
    val data: T?,
    val warnings: List<ApiWarning> = emptyList(),
    val error: ApiError? = null,
)
