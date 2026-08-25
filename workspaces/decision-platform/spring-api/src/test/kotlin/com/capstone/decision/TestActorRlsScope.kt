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
        actorRole: String = "USER",
        identityUsername: String = "decision_identity",
        identityPassword: String = "identity-test-secret-0001",
    ) {
        check(!connection.autoCommit) { "Actor RLS scope must be transaction-local." }
        val signature = (compactUuid() + compactUuid() + compactUuid()).take(86)
        val capability = "cap2_${compactUuid()}${compactUuid()}.$signature"
        val payloadHash = sha256(targetId)

        DriverManager.getConnection(jdbcUrl, identityUsername, identityPassword).use { identity ->
            identity
                .prepareStatement(
                    """
                    SELECT register_actor_request_capability_v2(
                      ?,?,?,1,?,?,?,?,?,?,?,statement_timestamp(),
                      statement_timestamp() + interval '15 seconds',?
                    )
                    """.trimIndent(),
                ).use { statement ->
                    statement.setString(1, capability)
                    statement.setString(2, actorUserId)
                    statement.setString(3, actorRole)
                    statement.setString(4, operation)
                    statement.setString(5, targetKind)
                    statement.setString(6, targetId)
                    statement.setString(7, payloadHash)
                    statement.setString(8, "req_${compactUuid()}")
                    statement.setString(9, "txn_${compactUuid()}")
                    statement.setString(10, compactUuid())
                    statement.setString(11, "ed25519:$signature")
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

    private fun sha256(value: String): String =
        "sha256:" +
            HexFormat
                .of()
                .formatHex(MessageDigest.getInstance("SHA-256").digest(value.toByteArray(Charsets.UTF_8)))

    private fun compactUuid(): String = UUID.randomUUID().toString().replace("-", "")
}
