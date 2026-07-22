package com.capstone.decision.infrastructure.security

import org.flywaydb.core.api.migration.BaseJavaMigration
import org.flywaydb.core.api.migration.Context
import java.sql.Connection

// BCrypt hash를 SQL text에 치환하지 않고 bind parameter로만 전달하는 S2.1 actor trust-root migration이다.
@Suppress("ktlint:standard:class-naming")
class V7__s2_1_actor_trust(
    private val userPasswordHash: String,
    private val adminPasswordHash: String,
) : BaseJavaMigration() {
    override fun getChecksum(): Int = MIGRATION_CHECKSUM

    override fun migrate(context: Context) {
        val userHash = DemoCredentialHashPolicy.requireValid(userPasswordHash)
        val adminHash = DemoCredentialHashPolicy.requireValid(adminPasswordHash)
        require(userHash != adminHash) { "Demo user and admin credential hashes must be different." }

        val connection = context.connection
        connection.createStatement().use { statement ->
            // 오류 시에도 JDBC bind parameter가 PostgreSQL server log에 남지 않도록 이 transaction에서 명시적으로 닫는다.
            statement.execute("set local log_parameter_max_length_on_error = 0")
            statement.execute(
                """
                alter table users
                    add column security_version bigint not null default 1,
                    add constraint users_security_version_positive_check check (security_version > 0)
                """.trimIndent(),
            )
        }

        val candidates = lockTrustRootCandidates(connection)
        ensureIdentity(connection, candidates, DemoAccounts.identities[0], userHash)
        ensureIdentity(connection, candidates, DemoAccounts.identities[1], adminHash)
        applyRuntimePrivileges(connection)
    }

    private fun lockTrustRootCandidates(connection: Connection): List<ExistingUser> =
        connection
            .prepareStatement(
                """
                select user_id, username, password_hash, role, status, security_version
                from users
                where user_id in (?, ?) or username in (?, ?)
                for update
                """.trimIndent(),
            ).use { statement ->
                statement.setString(1, DemoAccounts.identities[0].userId)
                statement.setString(2, DemoAccounts.identities[1].userId)
                statement.setString(3, DemoAccounts.identities[0].username)
                statement.setString(4, DemoAccounts.identities[1].username)
                statement.executeQuery().use { result ->
                    buildList {
                        while (result.next()) {
                            add(
                                ExistingUser(
                                    userId = result.getString("user_id"),
                                    username = result.getString("username"),
                                    passwordHash = result.getString("password_hash"),
                                    role = result.getString("role"),
                                    status = result.getString("status"),
                                    securityVersion = result.getLong("security_version"),
                                ),
                            )
                        }
                    }
                }
            }

    private fun ensureIdentity(
        connection: Connection,
        candidates: List<ExistingUser>,
        identity: DemoAccountIdentity,
        expectedHash: String,
    ) {
        val matching = candidates.filter { it.userId == identity.userId || it.username == identity.username }
        if (matching.isNotEmpty()) {
            check(
                matching.size == 1 &&
                    matching.single().matches(identity, expectedHash),
            ) { "Demo identity conflicts with the approved S2.1 trust root." }
            return
        }

        connection
            .prepareStatement(
                """
                insert into users (user_id, username, role, password_hash, status, security_version)
                values (?, ?, ?, ?, 'ACTIVE', 1)
                """.trimIndent(),
            ).use { statement ->
                statement.setString(1, identity.userId)
                statement.setString(2, identity.username)
                statement.setString(3, identity.role.name)
                statement.setString(4, expectedHash)
                check(statement.executeUpdate() == 1) { "Demo identity seed did not affect exactly one row." }
            }
    }

    private fun applyRuntimePrivileges(connection: Connection) {
        val runtimeRoleExists =
            connection.prepareStatement("select 1 from pg_roles where rolname = 'decision_app'").use { statement ->
                statement.executeQuery().use { it.next() }
            }
        if (!runtimeRoleExists) return

        connection.createStatement().use { statement ->
            // runtime은 인증 재검증용 SELECT만 가지며 credential/status/version 변경은 operator role에 남긴다.
            statement.execute("revoke insert, update, delete, truncate on table users from decision_app")
            statement.execute("grant select on table users to decision_app")
        }
    }

    private data class ExistingUser(
        val userId: String,
        val username: String,
        val passwordHash: String,
        val role: String,
        val status: String,
        val securityVersion: Long,
    ) {
        fun matches(
            identity: DemoAccountIdentity,
            expectedHash: String,
        ): Boolean =
            userId == identity.userId &&
                username == identity.username &&
                passwordHash == expectedHash &&
                role == identity.role.name &&
                status == ACTIVE_STATUS &&
                securityVersion == 1L
    }

    companion object {
        // Java migration은 기본 checksum이 없으므로 source 변경 시 함께 갱신하는 고정 검증값을 둔다.
        private const val MIGRATION_CHECKSUM = 0x52100007
        private const val ACTIVE_STATUS = "ACTIVE"
    }
}
