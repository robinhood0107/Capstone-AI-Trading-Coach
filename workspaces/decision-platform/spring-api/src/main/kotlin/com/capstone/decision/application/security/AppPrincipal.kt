package com.capstone.decision.application.security

// 인증 infrastructure가 검증을 끝낸 뒤 API에 전달하는 최소 actor 계약이다.
data class AppPrincipal(
    val userId: String,
    val username: String,
    val role: String,
    val securityVersion: Long,
)
