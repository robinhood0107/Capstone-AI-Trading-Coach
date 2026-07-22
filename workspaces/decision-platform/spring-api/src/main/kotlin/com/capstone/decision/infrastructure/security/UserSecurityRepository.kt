package com.capstone.decision.infrastructure.security

import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Repository

// 인증 경계가 신뢰하는 user identity/status/version 조회 계약이며 request 값은 항상 bind parameter로 전달한다.
interface UserSecurityRepository {
    fun findDemoCredentials(): List<UserSecurityRecord>

    fun findByUserId(userId: String): UserSecurityActorRecord?
}

// DataSource를 제외한 web slice도 시작할 수 있게 provider를 지연 해석하되 production 조회는 DB 부재 시 fail-closed한다.
@Repository
class JdbcUserSecurityRepository(
    private val jdbcTemplateProvider: ObjectProvider<JdbcTemplate>,
) : UserSecurityRepository {
    override fun findDemoCredentials(): List<UserSecurityRecord> =
        // 요청 username을 SQL에 전달하지 않고 고정된 두 row를 함께 읽어 peer BCrypt 검증을 강제한다.
        jdbcTemplate().query(
            """
            select user_id, username, password_hash, role, status, security_version
            from users
            where user_id in (?, ?)
            order by user_id
            """.trimIndent(),
            USER_SECURITY_ROW_MAPPER,
            DemoAccounts.identities[0].userId,
            DemoAccounts.identities[1].userId,
        )

    override fun findByUserId(userId: String): UserSecurityActorRecord? =
        jdbcTemplate()
            .query(
                """
                select user_id, username, role, status, security_version
                from users
                where user_id = ?
                """.trimIndent(),
                USER_SECURITY_ACTOR_ROW_MAPPER,
                userId,
            ).singleOrNull()

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

// 매 요청 JWT 재검증은 password hash를 읽지 않는 최소 actor projection만 사용한다.
data class UserSecurityActorRecord(
    val userId: String,
    val username: String,
    val role: DemoRole,
    val status: String,
    val securityVersion: Long,
)
