package com.capstone.decision.infrastructure.security

import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Repository

// 인증 경계가 신뢰하는 user identity/status/version 조회 계약이며 request 값은 항상 bind parameter로 전달한다.
interface UserSecurityRepository {
    fun findByUsername(username: String): UserSecurityRecord?

    fun findByUserId(userId: String): UserSecurityRecord?
}

// DataSource를 제외한 web slice도 시작할 수 있게 provider를 지연 해석하되 production 조회는 DB 부재 시 fail-closed한다.
@Repository
class JdbcUserSecurityRepository(
    private val jdbcTemplateProvider: ObjectProvider<JdbcTemplate>,
) : UserSecurityRepository {
    override fun findByUsername(username: String): UserSecurityRecord? =
        queryOne(
            """
            select user_id, username, password_hash, role, status, security_version
            from users
            where username = ?
            """.trimIndent(),
            username,
        )

    override fun findByUserId(userId: String): UserSecurityRecord? =
        queryOne(
            """
            select user_id, username, password_hash, role, status, security_version
            from users
            where user_id = ?
            """.trimIndent(),
            userId,
        )

    private fun queryOne(
        sql: String,
        value: String,
    ): UserSecurityRecord? =
        jdbcTemplate()
            .query(sql, USER_SECURITY_ROW_MAPPER, value)
            .singleOrNull()

    private fun jdbcTemplate(): JdbcTemplate =
        jdbcTemplateProvider.ifAvailable
            ?: throw IllegalStateException("Authentication user repository is unavailable.")

    companion object {
        private val USER_SECURITY_ROW_MAPPER =
            org.springframework.jdbc.core.RowMapper { result, _ ->
                UserSecurityRecord(
                    userId = result.getString("user_id"),
                    username = result.getString("username"),
                    passwordHash = result.getString("password_hash"),
                    role = DemoRole.valueOf(result.getString("role")),
                    status = result.getString("status"),
                    securityVersion = result.getLong("security_version"),
                )
            }
    }
}

// password hash는 login verifier 안에서만 사용하고 principal/token/로그로 전달하지 않는다.
data class UserSecurityRecord(
    val userId: String,
    val username: String,
    val passwordHash: String,
    val role: DemoRole,
    val status: String,
    val securityVersion: Long,
)
