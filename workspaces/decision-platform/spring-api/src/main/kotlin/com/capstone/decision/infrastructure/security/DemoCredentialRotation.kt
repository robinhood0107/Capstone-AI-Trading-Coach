package com.capstone.decision.infrastructure.security

import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.sql.Connection
import java.sql.DriverManager
import java.util.HexFormat
import java.util.Locale
import java.util.UUID
import kotlin.system.exitProcess

// 운영자가 승인된 demo row 하나만 회전하도록 DB credential과 attested bundle을 env로 받는 one-shot entrypoint다.
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
            // secret/hash/evidence/DB exception text는 출력하지 않고 운영자가 분기할 수 있는 단계만 남긴다.
            System.err.println("demo credential rotation failed: rotation_transaction")
            exitProcess(1)
        }
    }

    fun rotate(
        environment: Map<String, String>,
        auditId: String = "aud_demo_rotation_${UUID.randomUUID()}",
    ) {
        val config = RotationConfig.from(environment, auditId)
        try {
            DriverManager.getConnection(config.jdbcUrl, MIGRATION_USER, config.migrationPassword).use { connection ->
                rotateInTransaction(connection, config)
            }
        } finally {
            config.clearSensitiveEvidence()
        }
    }

    private fun rotateInTransaction(
        connection: Connection,
        config: RotationConfig,
    ) {
        connection.autoCommit = false
        try {
            configureTransactionSafety(connection)
            val lockedCredentials = lockDemoCredentials(connection)
            val target = requireTargetAndPeerInvariant(lockedCredentials, config)
            val newSecurityVersion = updateCredential(connection, config, target)
            revokeRefreshFamilies(connection, config.identity.userId)
            insertAudit(connection, config, newSecurityVersion)
            connection.commit()
        } catch (exception: Exception) {
            runCatching { connection.rollback() }
            throw exception
        } finally {
            runCatching { connection.autoCommit = true }
        }
    }

    private fun configureTransactionSafety(connection: Connection) {
        connection.createStatement().use { statement ->
            statement.queryTimeout = STATEMENT_TIMEOUT_SECONDS
            statement.execute("set local lock_timeout = '3s'")
            statement.execute("set local statement_timeout = '10s'")
        }
        PostgreSqlCredentialLoggingPolicy.requireSafe(connection)
    }

    private fun lockDemoCredentials(connection: Connection): List<LockedCredential> =
        connection
            .prepareStatement(
                """
                select user_id, username, password_hash, role, status, security_version,
                       credential_reuse_tag, credential_bundle_mac, credential_policy_version
                from users
                where user_id in (?, ?)
                order by user_id
                for update nowait
                """.trimIndent(),
            ).use { statement ->
                statement.queryTimeout = STATEMENT_TIMEOUT_SECONDS
                statement.setString(1, DemoAccounts.identities[0].userId)
                statement.setString(2, DemoAccounts.identities[1].userId)
                statement.executeQuery().use { result ->
                    buildList {
                        while (result.next()) {
                            add(
                                LockedCredential(
                                    userId = result.getString("user_id"),
                                    username = result.getString("username"),
                                    passwordHash = result.getString("password_hash"),
                                    role = result.getString("role"),
                                    status = result.getString("status"),
                                    securityVersion = result.getLong("security_version"),
                                    reuseTag = result.getBytes("credential_reuse_tag"),
                                    bundleMac = result.getBytes("credential_bundle_mac"),
                                    policyVersion =
                                        result
                                            .getObject("credential_policy_version")
                                            ?.let { (it as Number).toInt() },
                                ),
                            )
                        }
                    }
                }
            }

    private fun requireTargetAndPeerInvariant(
        credentials: List<LockedCredential>,
        config: RotationConfig,
    ): LockedCredential {
        check(credentials.size == DemoAccounts.identities.size) { "Approved demo credential rows are incomplete." }
        val verifiedStored =
            DemoAccounts.identities.map { identity ->
                val credential = credentials.singleOrNull { it.userId == identity.userId }
                check(
                    credential != null &&
                        credential.username == identity.username &&
                        credential.role == identity.role.name &&
                        credential.status == ACTIVE_STATUS &&
                        credential.securityVersion > 0,
                ) { "Approved demo credential row is invalid." }
                DemoCredentialBundlePolicy.verifyStored(
                    identity = identity,
                    passwordHash = credential.passwordHash,
                    reuseTag = requireNotNull(credential.reuseTag),
                    bundleMac = requireNotNull(credential.bundleMac),
                    policyVersion = requireNotNull(credential.policyVersion),
                    separationKey = config.separationKey,
                )
            }
        val newTag = config.credentialBundle.reuseTag
        try {
            check(verifiedStored.none { MessageDigest.isEqual(it.reuseTagInternal(), newTag) }) {
                "Credential rotation must use plaintext distinct from both demo accounts."
            }
        } finally {
            newTag.fill(0)
        }
        check(credentials.none { it.passwordHash == config.credentialBundle.passwordHash }) {
            "Credential rotation must use a new hash distinct from both demo accounts."
        }
        return requireNotNull(credentials.singleOrNull { it.userId == config.identity.userId })
    }

    private fun updateCredential(
        connection: Connection,
        config: RotationConfig,
        current: LockedCredential,
    ): Long {
        val newTag = config.credentialBundle.reuseTag
        val newMac = config.credentialBundle.bundleMac
        val currentTag = requireNotNull(current.reuseTag)
        val currentMac = requireNotNull(current.bundleMac)
        val currentPolicyVersion = requireNotNull(current.policyVersion)
        return try {
            connection
                .prepareStatement(
                    """
                    update users
                    set password_hash = ?, credential_reuse_tag = ?, credential_bundle_mac = ?,
                        credential_policy_version = ?, security_version = security_version + 1, updated_at = now()
                    where user_id = ? and username = ? and role = ? and status = ?
                      and security_version = ? and password_hash = ?
                      and credential_reuse_tag = ? and credential_bundle_mac = ?
                      and credential_policy_version = ?
                    returning security_version
                    """.trimIndent(),
                ).use { statement ->
                    statement.queryTimeout = STATEMENT_TIMEOUT_SECONDS
                    statement.setString(1, config.credentialBundle.passwordHash)
                    statement.setBytes(2, newTag)
                    statement.setBytes(3, newMac)
                    statement.setInt(4, config.credentialBundle.policyVersion)
                    statement.setString(5, config.identity.userId)
                    statement.setString(6, config.identity.username)
                    statement.setString(7, config.identity.role.name)
                    statement.setString(8, ACTIVE_STATUS)
                    statement.setLong(9, current.securityVersion)
                    statement.setString(10, current.passwordHash)
                    statement.setBytes(11, currentTag)
                    statement.setBytes(12, currentMac)
                    statement.setInt(13, currentPolicyVersion)
                    statement.executeQuery().use { result ->
                        check(result.next()) { "Approved demo credential row was not found." }
                        val version = result.getLong("security_version")
                        check(!result.next()) { "Credential rotation affected more than one row." }
                        check(version == Math.addExact(current.securityVersion, 1L)) {
                            "Credential rotation did not advance security version exactly once."
                        }
                        version
                    }
                }
        } finally {
            newTag.fill(0)
            newMac.fill(0)
        }
    }

    private fun revokeRefreshFamilies(
        connection: Connection,
        ownerUserId: String,
    ) {
        connection
            .prepareStatement(
                """
                update s4_9_mcp_oauth_refresh_tokens
                set revoked_at = coalesce(revoked_at, transaction_timestamp())
                where owner_user_id = ? and revoked_at is null
                """.trimIndent(),
            ).use { statement ->
                statement.queryTimeout = STATEMENT_TIMEOUT_SECONDS
                statement.setString(1, ownerUserId)
                statement.executeUpdate()
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
                statement.queryTimeout = STATEMENT_TIMEOUT_SECONDS
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
        val credentialBundle: VerifiedDemoCredentialBundle,
        val separationKey: ByteArray,
        val rotationActor: String,
        val auditId: String,
    ) {
        fun clearSensitiveEvidence() {
            separationKey.fill(0)
            credentialBundle.clearEvidence()
        }

        companion object {
            fun from(
                environment: Map<String, String>,
                auditId: String,
            ): RotationConfig {
                val host = environment["POSTGRES_HOST"]?.takeIf { it.isNotBlank() } ?: DEFAULT_POSTGRES_HOST
                require(host.lowercase(Locale.ROOT) in LOOPBACK_HOSTS) {
                    "POSTGRES_HOST must be loopback for credential rotation."
                }
                val port =
                    (environment["POSTGRES_PORT"]?.takeIf { it.isNotBlank() } ?: DEFAULT_POSTGRES_PORT).toIntOrNull()
                require(port != null && port in 1..65_535) { "POSTGRES_PORT has an invalid format." }
                val database = required(environment, "POSTGRES_DB")
                require(DATABASE_PATTERN.matches(database)) { "POSTGRES_DB has an invalid format." }
                val targetUserId = required(environment, "DEMO_CREDENTIAL_USER_ID")
                val identity =
                    DemoAccounts.byUserId(targetUserId)
                        ?: throw IllegalArgumentException("DEMO_CREDENTIAL_USER_ID is not allowlisted.")
                val rotationActor = required(environment, "DEMO_CREDENTIAL_ROTATION_ACTOR")
                require(ROTATION_ACTOR_PATTERN.matches(rotationActor)) {
                    "DEMO_CREDENTIAL_ROTATION_ACTOR has an invalid format."
                }
                require(AUDIT_ID_PATTERN.matches(auditId)) { "audit id has an invalid format." }
                val migrationPassword = required(environment, "POSTGRES_MIGRATION_PASSWORD")
                val serializedBundle = required(environment, "DEMO_CREDENTIAL_BUNDLE")
                val separationKey =
                    DemoCredentialBundlePolicy.decodeSeparationKey(
                        required(environment, "DEMO_CREDENTIAL_SEPARATION_KEY"),
                    )
                return try {
                    val credentialBundle =
                        DemoCredentialBundlePolicy.verify(serializedBundle, identity, separationKey)
                    RotationConfig(
                        jdbcUrl =
                            "jdbc:postgresql://$host:$port/$database" +
                                "?connectTimeout=$CONNECT_TIMEOUT_SECONDS&socketTimeout=$SOCKET_TIMEOUT_SECONDS&tcpKeepAlive=true",
                        migrationPassword = migrationPassword,
                        identity = identity,
                        credentialBundle = credentialBundle,
                        separationKey = separationKey,
                        rotationActor = rotationActor,
                        auditId = auditId,
                    )
                } catch (exception: Exception) {
                    separationKey.fill(0)
                    throw exception
                }
            }

            private fun required(
                environment: Map<String, String>,
                name: String,
            ): String =
                environment[name]?.takeIf { it.isNotBlank() }
                    ?: throw IllegalArgumentException("$name is required.")

            private val DATABASE_PATTERN = Regex("^[A-Za-z_][A-Za-z0-9_]{0,62}$")
            private val ROTATION_ACTOR_PATTERN = Regex("^[A-Za-z0-9._:/#-]{1,128}$")
            private val AUDIT_ID_PATTERN = Regex("^[A-Za-z0-9._:-]{1,160}$")
        }
    }

    private data class LockedCredential(
        val userId: String,
        val username: String,
        val passwordHash: String,
        val role: String,
        val status: String,
        val securityVersion: Long,
        val reuseTag: ByteArray?,
        val bundleMac: ByteArray?,
        val policyVersion: Int?,
    )

    private const val MIGRATION_USER = "flyway"
    private const val DEFAULT_POSTGRES_HOST = "127.0.0.1"
    private const val DEFAULT_POSTGRES_PORT = "5432"
    private const val ACTIVE_STATUS = "ACTIVE"
    private const val CONNECT_TIMEOUT_SECONDS = 5
    private const val SOCKET_TIMEOUT_SECONDS = 15
    private const val STATEMENT_TIMEOUT_SECONDS = 10
    private val LOOPBACK_HOSTS = setOf("127.0.0.1", "localhost")
}
