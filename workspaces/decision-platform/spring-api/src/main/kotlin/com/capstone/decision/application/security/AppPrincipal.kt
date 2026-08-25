package com.capstone.decision.application.security

import org.springframework.security.core.context.SecurityContextHolder

data class AuthenticatedActorRef(
    val sessionHandle: String,
    val expectedUserId: String,
    val securityVersion: Long,
) {
    init {
        require(sessionHandle.matches(Regex("^sid1_[0-9a-f]{64}$")))
        require(expectedUserId.matches(Regex("^usr_[A-Za-z0-9_-]{4,96}$")))
        require(securityVersion > 0)
    }

    override fun toString(): String =
        "AuthenticatedActorRef(sessionHandle=<redacted>, expectedUserId=$expectedUserId, securityVersion=$securityVersion)"

    companion object {
        fun current(
            expectedUserId: String,
            expectedSecurityVersion: Long? = null,
        ): AuthenticatedActorRef {
            val principal =
                SecurityContextHolder.getContext().authentication?.principal as? AppPrincipal
                    ?: throw IllegalStateException("Authenticated actor session is unavailable.")
            check(principal.userId == expectedUserId) { "Authenticated actor mismatch." }
            if (expectedSecurityVersion != null) {
                check(principal.securityVersion == expectedSecurityVersion) { "Authenticated actor version mismatch." }
            }
            return principal.actorRef
        }
    }
}

// 인증 infrastructure가 검증을 끝낸 뒤 API에 전달하는 최소 actor 계약이다.
data class AppPrincipal(
    val userId: String,
    val username: String,
    val role: String,
    val securityVersion: Long,
    val actorRef: AuthenticatedActorRef,
)
