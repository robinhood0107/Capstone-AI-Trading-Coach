package com.capstone.decision

import java.security.MessageDigest
import java.sql.Connection
import java.sql.DriverManager
import java.util.HexFormat
import java.util.UUID

/** Opens one production-equivalent actor RLS scope for non-Spring migration tests. */
object TestActorRlsScope {
    fun open(
        jdbcUrl: String,
        connection: Connection,
        actorUserId: String,
        operation: String,
        targetKind: String,
        targetId: String,
        actorRole: String? = null,
        identityUsername: String = "decision_identity",
        identityPassword: String = "identity-test-secret-0001",
        payloadValues: List<String?>? = null,
    ) {
        check(!connection.autoCommit) { "Actor RLS scope must be transaction-local." }
        // 역할을 지정하지 않으면 V213 기준 역할을 쓴다. 고정 운영자 demo-user만 ADMIN이다.
        val role = actorRole ?: if (actorUserId == "usr_demo_user") "ADMIN" else "USER"
        val signature = (compactUuid() + compactUuid() + compactUuid()).take(86)
        val capability = "cap2_${compactUuid()}${compactUuid()}.$signature"
        val payloadHash = payloadValues?.let(::payloadHash) ?: sha256(targetId)
        // V213이 demo-user security_version을 올리므로 고정값 대신 현재 행의 version을 쓴다.
        val securityVersion = currentSecurityVersion(jdbcUrl, actorUserId)

        DriverManager.getConnection(jdbcUrl, identityUsername, identityPassword).use { identity ->
            identity
                .prepareStatement(
                    """
                    SELECT register_actor_request_capability_v2(
                      ?,?,?,?,?,?,?,?,?,?,?,statement_timestamp(),
                      statement_timestamp() + interval '15 seconds',?
                    )
                    """.trimIndent(),
                ).use { statement ->
                    statement.setString(1, capability)
                    statement.setString(2, actorUserId)
                    statement.setString(3, role)
                    statement.setLong(4, securityVersion)
                    statement.setString(5, operation)
                    statement.setString(6, targetKind)
                    statement.setString(7, targetId)
                    statement.setString(8, payloadHash)
                    statement.setString(9, "req_${compactUuid()}")
                    statement.setString(10, "txn_${compactUuid()}")
                    statement.setString(11, compactUuid())
                    statement.setString(12, "ed25519:$signature")
                    statement.executeQuery().use { result -> check(result.next() && result.getBoolean(1)) }
                }
        }

        connection
            .prepareStatement("SELECT open_actor_rls_scope_v1(?,?,?,?,?,?)")
            .use { statement ->
                statement.setString(1, capability)
                statement.setString(2, actorUserId)
                statement.setString(3, operation)
                statement.setString(4, targetKind)
                statement.setString(5, targetId)
                statement.setString(6, payloadHash)
                statement.executeQuery().use { result -> check(result.next() && result.getBoolean(1)) }
            }
    }

    private fun currentSecurityVersion(
        jdbcUrl: String,
        actorUserId: String,
    ): Long =
        runCatching {
            DriverManager.getConnection(jdbcUrl, "decision_auth", "auth-test-secret-0001").use { auth ->
                auth.prepareStatement("select security_version from read_user_actor(?)").use { statement ->
                    statement.setString(1, actorUserId)
                    statement.executeQuery().use { result -> if (result.next()) result.getLong(1) else 1L }
                }
            }
        }.getOrDefault(1L)

    private fun sha256(value: String): String =
        "sha256:" +
            HexFormat
                .of()
                .formatHex(MessageDigest.getInstance("SHA-256").digest(value.toByteArray(Charsets.UTF_8)))

    private fun payloadHash(values: List<String?>): String =
        sha256(
            values.joinToString(separator = "") { value ->
                if (value == null) "-:\n" else "${value.toByteArray(Charsets.UTF_8).size}:$value\n"
            },
        )

    private fun compactUuid(): String = UUID.randomUUID().toString().replace("-", "")
}
