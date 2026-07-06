package com.capstone.decision.infrastructure.security

// JWT claim에서 복원한 최소 사용자 정보를 SecurityContext에 싣기 위한 내부 표현이다.
data class AppPrincipal(
    val userId: String,
    val username: String,
    val role: DemoRole,
)
