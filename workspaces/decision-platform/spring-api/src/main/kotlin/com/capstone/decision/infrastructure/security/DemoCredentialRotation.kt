package com.capstone.decision.infrastructure.security

import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.sql.Connection
import java.sql.DriverManager
import java.util.HexFormat
import java.util.UUID
import kotlin.system.exitProcess

// 운영자가 승인된 demo row 하나만 회전하도록 DB credential과 새 hash를 env로 받는 one-shot entrypoint다.
object DemoCredentialRotation {
    @JvmStatic
    fun main(args: Array<String>) {
        if (args.isNotEmpty()) {
            System.err.println("demo credential rotation failed: unexpected_arguments")
            exitProcess(1)
        }
        try {
            rotate(System.getenv())
            println("demo credential rotation completed")
        } catch (exception: Exception) {
            // secret/hash/DB exception text는 출력하지 않고 운영자가 분기할 수 있는 단계만 남긴다.
            System.err.println("demo credential rotation failed: rotation_transaction")
            exitProcess(1)
        }
    }

    fun rotate(
        environment: Map<String, String>,
        auditId: String = "aud_demo_rotation_${UUID.randomUUID()}",
    ) {
        val config = RotationConfig.from(environment, auditId)
        DriverManager.getConnection(config.jdbcUrl, MIGRATION_USER, config.migrationPassword).use { connection ->
            rotateInTransaction(connection, config)
        }
    }

    private fun rotateInTransaction(
        connection: Connection,
        config: RotationConfig,
    ) {
        connection.autoCommit = false
        try {
            val newSecurityVersion = updateCredential(connection, config)
            insertAudit(connection, config, newSecurityVersion)
            connection.commit()
        } catch (exception: Exception) {
            runCatching { connection.rollback() }
            throw exception
        } finally {
            runCatching { connection.autoCommit = true }
        }
    }

    private fun updateCredential(
        connection: Connection,
        config: RotationConfig,
    ): Long =
        connection
            .prepareStatement(
                """
                update users
                set password_hash = ?, security_version = security_version + 1, updated_at = now()
                where user_id = ? and username = ?
                returning security_version
                """.trimIndent(),
            ).use { statement ->
                statement.setString(1, config.passwordHash)
                statement.setString(2, config.identity.userId)
                statement.setString(3, config.identity.username)
                statement.executeQuery().use { result ->
                    check(result.next()) { "Approved demo credential row was not found." }
                    val version = result.getLong("security_version")
                    check(!result.next()) { "Credential rotation affected more than one row." }
                    check(version > 1) { "Credential rotation did not advance security version." }
                    version
                }
            }

    private fun insertAudit(
        connection: Connection,
        config: RotationConfig,
        securityVersion: Long,
    ) {
        connection
            .prepareStatement(
                """
                insert into audit_logs (
                    audit_log_id, user_id, actor_role, action, target_type, target_id, payload_json
                )
                values (?, ?, 'OPERATOR', 'DEMO_CREDENTIAL_ROTATED', 'USER', ?,
                        jsonb_build_object('rotationActorDigest', ?, 'securityVersion', ?))
                """.trimIndent(),
            ).use { statement ->
                statement.setString(1, config.auditId)
                statement.setString(2, config.identity.userId)
                statement.setString(3, config.identity.userId)
                statement.setString(4, sha256(config.rotationActor))
                statement.setLong(5, securityVersion)
                check(statement.executeUpdate() == 1) { "Credential rotation audit was not written." }
            }
    }

    private fun sha256(value: String): String =
        HexFormat.of().formatHex(
            MessageDigest.getInstance("SHA-256").digest(value.toByteArray(StandardCharsets.UTF_8)),
        )

    private data class RotationConfig(
        val jdbcUrl: String,
        val migrationPassword: String,
        val identity: DemoAccountIdentity,
        val passwordHash: String,
        val rotationActor: String,
        val auditId: String,
    ) {
        companion object {
            fun from(
                environment: Map<String, String>,
                auditId: String,
            ): RotationConfig {
                val host = environment["POSTGRES_HOST"]?.takeIf { it.isNotBlank() } ?: DEFAULT_POSTGRES_HOST
                require(HOST_PATTERN.matches(host)) { "POSTGRES_HOST has an invalid format." }
                val port = (environment["POSTGRES_PORT"]?.takeIf { it.isNotBlank() } ?: DEFAULT_POSTGRES_PORT).toIntOrNull()
                require(port != null && port in 1..65_535) { "POSTGRES_PORT has an invalid format." }
                val database = required(environment, "POSTGRES_DB")
                require(DATABASE_PATTERN.matches(database)) { "POSTGRES_DB has an invalid format." }
                val targetUserId = required(environment, "DEMO_CREDENTIAL_USER_ID")
                val identity =
                    DemoAccounts.byUserId(targetUserId)
                        ?: throw IllegalArgumentException("DEMO_CREDENTIAL_USER_ID is not allowlisted.")
                val passwordHash =
                    DemoCredentialHashPolicy.requireValid(
                        required(environment, "DEMO_CREDENTIAL_PASSWORD_HASH"),
                    )
                val rotationActor = required(environment, "DEMO_CREDENTIAL_ROTATION_ACTOR")
                require(ROTATION_ACTOR_PATTERN.matches(rotationActor)) {
                    "DEMO_CREDENTIAL_ROTATION_ACTOR has an invalid format."
                }
                require(AUDIT_ID_PATTERN.matches(auditId)) { "audit id has an invalid format." }
                return RotationConfig(
                    jdbcUrl = "jdbc:postgresql://$host:$port/$database",
                    migrationPassword = required(environment, "POSTGRES_MIGRATION_PASSWORD"),
                    identity = identity,
                    passwordHash = passwordHash,
                    rotationActor = rotationActor,
                    auditId = auditId,
                )
            }

            private fun required(
                environment: Map<String, String>,
                name: String,
            ): String =
                environment[name]?.takeIf { it.isNotBlank() }
                    ?: throw IllegalArgumentException("$name is required.")

            private val HOST_PATTERN = Regex("^[A-Za-z0-9.-]{1,253}$")
            private val DATABASE_PATTERN = Regex("^[A-Za-z_][A-Za-z0-9_]{0,62}$")
            private val ROTATION_ACTOR_PATTERN = Regex("^[A-Za-z0-9._:/#-]{1,128}$")
            private val AUDIT_ID_PATTERN = Regex("^[A-Za-z0-9._:-]{1,160}$")
        }
    }

    private const val MIGRATION_USER = "flyway"
    private const val DEFAULT_POSTGRES_HOST = "127.0.0.1"
    private const val DEFAULT_POSTGRES_PORT = "5432"
}
