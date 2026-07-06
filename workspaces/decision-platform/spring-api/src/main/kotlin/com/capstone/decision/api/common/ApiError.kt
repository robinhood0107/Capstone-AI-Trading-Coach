package com.capstone.decision.api.common

data class ApiError(
    val code: String,
    val message: String,
    val details: Map<String, Any?> = emptyMap(),
)
