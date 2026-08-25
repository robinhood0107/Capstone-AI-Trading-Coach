package com.capstone.decision.infrastructure.security

import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Repository

// 인증 경계가 신뢰하는 user identity/status/version 조회 계약이며 request 값은 항상 bind parameter로 전달한다.
interface UserSecurityRepository {
    fun findDemoCredentials(): List<UserSecurityRecord>

    fun createAuthenticatedSession(
        username: String,
        password: String,
        ttlSeconds: Int,
    ): UserSecuritySessionRecord?

    fun findBySessionHandle(sessionHandle: String): UserSecuritySessionRecord?

    fun findByUserId(userId: String): UserSecurityActorRecord?
}

// DataSource를 제외한 web slice도 시작할 수 있게 provider를 지연 해석하되 production 조회는 DB 부재 시 fail-closed한다.
@Repository
class JdbcUserSecurityRepository(
    private val authDatabaseProvider: ObjectProvider<AuthDatabase>,
) : UserSecurityRepository {
    override fun findDemoCredentials(): List<UserSecurityRecord> =
        // 요청 username을 SQL에 전달하지 않고 고정된 두 row를 함께 읽어 peer BCrypt 검증을 강제한다.
        jdbcTemplate().query(
            """
            select user_id, username, password_hash, role, status, security_version
            from read_demo_credentials()
            """.trimIndent(),
            USER_SECURITY_ROW_MAPPER,
        )

    override fun createAuthenticatedSession(
        username: String,
        password: String,
        ttlSeconds: Int,
    ): UserSecuritySessionRecord? =
        jdbcTemplate()
            .query(
                """
                select session_handle, actor_user_id, username, actor_role, actor_security_version, expires_at
                from authenticate_demo_actor_session_v1(?,?,?)
                """.trimIndent(),
                USER_SECURITY_SESSION_ROW_MAPPER,
                username,
                password,
                ttlSeconds,
            ).singleOrNull()

    override fun findBySessionHandle(sessionHandle: String): UserSecuritySessionRecord? =
        jdbcTemplate()
            .query(
                """
                select ? as session_handle,
                       actor_user_id, username, actor_role, actor_security_version, expires_at
                from read_actor_auth_session_v1(?)
                """.trimIndent(),
                USER_SECURITY_SESSION_ROW_MAPPER,
                sessionHandle,
                sessionHandle,
            ).singleOrNull()

    override fun findByUserId(userId: String): UserSecurityActorRecord? =
        jdbcTemplate()
            .query(
                """
                select user_id, username, role, status, security_version
                from read_user_actor(?)
                """.trimIndent(),
                USER_SECURITY_ACTOR_ROW_MAPPER,
                userId,
            ).singleOrNull()

    private fun jdbcTemplate(): JdbcTemplate =
        JdbcTemplate(
            authDatabaseProvider.ifAvailable?.dataSource
                ?: throw IllegalStateException("Authentication user repository is unavailable."),
        )

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
        private val USER_SECURITY_SESSION_ROW_MAPPER =
            org.springframework.jdbc.core.RowMapper { result, _ ->
                UserSecuritySessionRecord(
                    sessionHandle = result.getString("session_handle"),
                    userId = result.getString("actor_user_id"),
                    username = result.getString("username"),
                    role = DemoRole.valueOf(result.getString("actor_role")),
                    securityVersion = result.getLong("actor_security_version"),
                    expiresAt = result.getObject("expires_at", java.time.OffsetDateTime::class.java),
                )
            }
        private val USER_SECURITY_ACTOR_ROW_MAPPER =
            org.springframework.jdbc.core.RowMapper { result, _ ->
                UserSecurityActorRecord(
                    userId = result.getString("user_id"),
                    username = result.getString("username"),
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

// 로그인과 매 요청 JWT 재검증은 raw session handle의 hash-bound DB row만 신뢰한다.
data class UserSecuritySessionRecord(
    val sessionHandle: String,
    val userId: String,
    val username: String,
    val role: DemoRole,
    val securityVersion: Long,
    val expiresAt: java.time.OffsetDateTime,
)

data class UserSecurityActorRecord(
    val userId: String,
    val username: String,
    val role: DemoRole,
    val status: String,
    val securityVersion: Long,
)
