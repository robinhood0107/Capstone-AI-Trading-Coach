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

    /** 현재 비밀번호가 틀리면 null. 비밀번호 로그인이 없는 계정은 P0002 로 거부된다. */
    fun changePassword(
        userId: String,
        currentPassword: String,
        newPasswordHash: String,
        ttlSeconds: Int,
    ): AuthenticatedAccount? =
        jdbcTemplate()
            .query(
                """
                select session_handle, actor_user_id, username, actor_role,
                       actor_security_version, expires_at
                from change_password_login_actor_v1(?,?,?,?)
                """.trimIndent(),
                ACCOUNT_ROW_MAPPER,
                userId,
                currentPassword,
                newPasswordHash,
                ttlSeconds,
            ).singleOrNull()

    /** FULL 자동운용 bridge 전용. 활성 소유자에게 짧은 세션을 연다. 비활성·없는 사용자는 null. */
    fun issueAutomationRuntimeSession(
        userId: String,
        ttlSeconds: Int,
    ): AuthenticatedAccount? =
        jdbcTemplate()
            .query(
                """
                select session_handle, actor_user_id, username, actor_role,
                       actor_security_version, expires_at
                from issue_automation_runtime_session_v1(?,?)
                """.trimIndent(),
                ACCOUNT_ROW_MAPPER,
                userId,
                ttlSeconds,
            ).singleOrNull()

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
