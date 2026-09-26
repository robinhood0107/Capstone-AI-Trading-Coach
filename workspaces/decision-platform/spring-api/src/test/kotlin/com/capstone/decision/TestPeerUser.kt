package com.capstone.decision

import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
import java.sql.Connection
import java.sql.DriverManager

/**
 * V213 이후 demo-admin이 사라져 owner 격리 테스트의 두 번째 owner로 쓰는 일반 USER 계정이다.
 * 이메일 password identity로 로그인하므로 HTTP login과 actor capability 발급 모두 production 경로를 탄다.
 */
object TestPeerUser {
    const val USER_ID = "usr_isolation_peer_0001"
    const val USERNAME = "isolation_peer_0001"
    const val EMAIL = "isolation-peer-0001@example.test"
    val PASSWORD: String = "i" + "p".repeat(16)
    val PASSWORD_HASH: String by lazy { requireNotNull(BCryptPasswordEncoder(12).encode(PASSWORD)) }

    /** superuser 연결로 peer USER와 password identity를 멱등하게 만든다. */
    fun ensure(connection: Connection) {
        connection
            .prepareStatement(
                """
                insert into users(user_id, username, password_hash, role, status, security_version)
                values (?, ?, null, 'USER', 'ACTIVE', 1)
                on conflict (user_id) do nothing
                """.trimIndent(),
            ).use { statement ->
                statement.setString(1, USER_ID)
                statement.setString(2, USERNAME)
                statement.executeUpdate()
            }
        connection
            .prepareStatement(
                """
                insert into password_login_identities(email_normalized, user_id, password_hash)
                values (?, ?, ?)
                on conflict do nothing
                """.trimIndent(),
            ).use { statement ->
                statement.setString(1, EMAIL)
                statement.setString(2, USER_ID)
                statement.setString(3, PASSWORD_HASH)
                statement.executeUpdate()
            }
    }

    fun ensure(
        jdbcUrl: String,
        username: String,
        password: String,
    ) {
        DriverManager.getConnection(jdbcUrl, username, password).use { connection ->
            connection.autoCommit = true
            ensure(connection)
        }
    }

    fun ensure(jdbcTemplate: JdbcTemplate) {
        jdbcTemplate.execute { connection: Connection -> ensure(connection) }
    }
}
