package com.capstone.decision.infrastructure.security

import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Repository

@Repository
class FullPasswordAccountRepository(
    private val authDatabaseProvider: ObjectProvider<AuthDatabase>,
) {
    fun register(
        email: String,
        passwordHash: String,
        ttlSeconds: Int,
    ): AuthenticatedAccount =
        jdbcTemplate()
            .query(
                """
                select session_handle, actor_user_id, username, actor_role,
                       actor_security_version, expires_at
                from register_password_login_actor_v1(?,?,?)
                """.trimIndent(),
                ACCOUNT_ROW_MAPPER,
                email,
                passwordHash,
                ttlSeconds,
            ).single()

    fun authenticate(
        email: String,
        password: String,
        dummyPasswordHash: String,
        ttlSeconds: Int,
    ): AuthenticatedAccount? =
        jdbcTemplate()
            .query(
                """
                select session_handle, actor_user_id, username, actor_role,
                       actor_security_version, expires_at
                from authenticate_password_login_actor_v1(?,?,?,?)
                """.trimIndent(),
                ACCOUNT_ROW_MAPPER,
                email,
                password,
                dummyPasswordHash,
                ttlSeconds,
            ).singleOrNull()

    fun addPassword(
        userId: String,
        email: String,
        passwordHash: String,
        ttlSeconds: Int,
    ): AuthenticatedAccount =
        jdbcTemplate()
            .query(
                """
                select session_handle, actor_user_id, username, actor_role,
                       actor_security_version, expires_at
                from add_password_login_actor_v1(?,?,?,?)
                """.trimIndent(),
                ACCOUNT_ROW_MAPPER,
                userId,
                email,
                passwordHash,
                ttlSeconds,
            ).single()

    private fun jdbcTemplate(): JdbcTemplate =
        JdbcTemplate(
            authDatabaseProvider.ifAvailable?.dataSource
                ?: throw IllegalStateException("Password account database is unavailable."),
        )

    private companion object {
        val ACCOUNT_ROW_MAPPER =
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
