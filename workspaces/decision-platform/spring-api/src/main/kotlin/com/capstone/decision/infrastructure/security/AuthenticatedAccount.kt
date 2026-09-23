package com.capstone.decision.infrastructure.security

import java.time.OffsetDateTime

/** Session-backed actor shared by OIDC and the private bootstrap path. */
data class AuthenticatedAccount(
    val userId: String,
    val username: String,
    val role: DemoRole,
    val securityVersion: Long,
    val sessionHandle: String,
    val expiresAt: OffsetDateTime,
) {
    override fun toString(): String = "AuthenticatedAccount(userId=$userId, role=$role, sessionHandle=<redacted>)"
}
