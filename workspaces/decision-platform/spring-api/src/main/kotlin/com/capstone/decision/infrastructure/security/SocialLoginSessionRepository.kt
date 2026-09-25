package com.capstone.decision.infrastructure.security

import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Repository

/** The callback passes only provider-verified immutable issuer and subject identifiers. */
interface SocialLoginSessionRepository {
    fun createSession(
        issuer: String,
        subject: String,
        operatorSubject: Boolean,
        ttlSeconds: Int,
    ): AuthenticatedAccount
}

@Repository
class JdbcSocialLoginSessionRepository(
    private val authDatabaseProvider: ObjectProvider<AuthDatabase>,
) : SocialLoginSessionRepository {
    override fun createSession(
        issuer: String,
        subject: String,
        operatorSubject: Boolean,
        ttlSeconds: Int,
    ): AuthenticatedAccount =
        JdbcTemplate(
            authDatabaseProvider.ifAvailable?.dataSource
                ?: throw IllegalStateException("Authentication database is unavailable."),
        ).query(
            """
            select session_handle, actor_user_id, username, actor_role,
                   actor_security_version, expires_at
            from authenticate_social_login_actor_v1(?,?,?,?)
            """.trimIndent(),
            { row, _ ->
                AuthenticatedAccount(
                    userId = row.getString("actor_user_id"),
                    username = row.getString("username"),
                    role = DemoRole.valueOf(row.getString("actor_role")),
                    securityVersion = row.getLong("actor_security_version"),
                    sessionHandle = row.getString("session_handle"),
                    expiresAt = row.getObject("expires_at", java.time.OffsetDateTime::class.java),
                )
            },
            issuer,
            subject,
            operatorSubject,
            ttlSeconds,
        ).single()
}
