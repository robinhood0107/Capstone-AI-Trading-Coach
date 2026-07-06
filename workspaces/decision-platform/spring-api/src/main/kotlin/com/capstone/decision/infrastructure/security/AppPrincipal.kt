package com.capstone.decision.infrastructure.security

data class AppPrincipal(
    val userId: String,
    val username: String,
    val role: DemoRole,
)
