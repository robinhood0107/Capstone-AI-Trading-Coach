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

    fun linkIdentityAndCreateSession(
        userId: String,
        issuer: String,
        subject: String,
        operatorSubject: Boolean,
        ttlSeconds: Int,
    ): AuthenticatedAccount

    fun listAuthenticationMethods(userId: String): List<AccountAuthenticationMethod>

    fun unlinkIdentity(
        userId: String,
        issuer: String,
    ): Boolean
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

    override fun linkIdentityAndCreateSession(
        userId: String,
        issuer: String,
        subject: String,
        operatorSubject: Boolean,
        ttlSeconds: Int,
    ): AuthenticatedAccount =
        JdbcTemplate(dataSource())
            .query(
                """
                select session_handle, actor_user_id, username, actor_role,
                       actor_security_version, expires_at
                from link_social_login_actor_v1(?,?,?,?,?)
                """.trimIndent(),
                AUTHENTICATED_ACCOUNT_ROW_MAPPER,
                userId,
                issuer,
                subject,
                operatorSubject,
                ttlSeconds,
            ).single()

    override fun listAuthenticationMethods(userId: String): List<AccountAuthenticationMethod> =
        JdbcTemplate(dataSource()).query(
            "select provider, email_normalized, linked_at from read_account_auth_methods_v1(?) order by provider",
            { row, _ ->
                AccountAuthenticationMethod(
                    provider = row.getString("provider"),
                    email = row.getString("email_normalized"),
                    linkedAt = row.getObject("linked_at", java.time.OffsetDateTime::class.java),
                )
            },
            userId,
        )

    override fun unlinkIdentity(
        userId: String,
        issuer: String,
    ): Boolean =
        JdbcTemplate(dataSource()).queryForObject(
            "select unlink_social_login_actor_v1(?,?)",
            Boolean::class.java,
            userId,
            issuer,
        ) ?: false

    private fun dataSource() =
        authDatabaseProvider.ifAvailable?.dataSource
            ?: throw IllegalStateException("Authentication database is unavailable.")

    private companion object {
        val AUTHENTICATED_ACCOUNT_ROW_MAPPER =
            org.springframework.jdbc.core.RowMapper { row, _ ->
                AuthenticatedAccount(
                    userId = row.getString("actor_user_id"),
                    username = row.getString("username"),
                    role = DemoRole.valueOf(row.getString("actor_role")),
                    securityVersion = row.getLong("actor_security_version"),
                    sessionHandle = row.getString("session_handle"),
                    expiresAt = row.getObject("expires_at", java.time.OffsetDateTime::class.java),
                )
            }
    }
}

data class AccountAuthenticationMethod(
    val provider: String,
    val email: String?,
    val linkedAt: java.time.OffsetDateTime,
)
