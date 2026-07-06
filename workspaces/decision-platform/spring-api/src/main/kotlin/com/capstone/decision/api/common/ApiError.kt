package com.capstone.decision.api.common

// 왜: 클라이언트가 HTTP 상태보다 error.code로 분기한다는 API 명세를 타입으로 고정한다.
data class ApiError(
    val code: String,
    val message: String,
    val details: Map<String, Any?> = emptyMap(),
)
