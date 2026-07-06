package com.capstone.decision.api.common

// 왜: Guide 모드 경고처럼 실패가 아닌 주의 신호를 error와 분리해 전달한다.
data class ApiWarning(
    val code: String,
    val message: String,
    val details: Map<String, Any?> = emptyMap(),
)
