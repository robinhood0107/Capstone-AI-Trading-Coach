package com.capstone.decision.infrastructure.security

import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.Path
import java.security.MessageDigest
import java.sql.Connection
import java.sql.DriverManager
import java.util.HexFormat
import java.util.Locale
import kotlin.system.exitProcess

object DemoIdentityBootstrap {
    @JvmStatic
    fun main(args: Array<String>) {
        if (args.isNotEmpty()) {
            System.err.println("demo identity bootstrap failed: unexpected_arguments")
            exitProcess(1)
        }
        try {
            bootstrap(System.getenv())
            println("demo identity bootstrap completed")
        } catch (_: Exception) {
            System.err.println("demo identity bootstrap failed: bootstrap_transaction")
            exitProcess(1)
        }
    }

    fun bootstrap(environment: Map<String, String>) {
        val config = BootstrapConfig.from(environment)
        try {
            DriverManager.getConnection(config.jdbcUrl, MIGRATION_USER, config.migrationPassword).use { connection ->
                bootstrapInTransaction(connection, config)
            }
        } finally {
            config.clearSensitiveEvidence()
        }
    }

    private fun bootstrapInTransaction(
        connection: Connection,
        config: BootstrapConfig,
    ) {
        connection.autoCommit = false
        try {
            connection.createStatement().use { statement ->
                statement.queryTimeout = STATEMENT_TIMEOUT_SECONDS
                statement.execute("set local lock_timeout = '3s'")
                statement.execute("set local statement_timeout = '10s'")
                statement.execute("select pg_advisory_xact_lock(hashtextextended('p1-demo-identity-bootstrap', 8601))")
            }
            PostgreSqlCredentialLoggingPolicy.requireSafe(connection)
            val existing = readDemoRows(connection)
            if (existing.isEmpty()) {
                insertIdentity(connection, config.userBundle)
                insertIdentity(connection, config.adminBundle)
                insertAudit(connection, config)
            } else {
                require(existing.size == DemoAccounts.identities.size) { "Demo identity bootstrap is incomplete." }
                listOf(config.userBundle, config.adminBundle).forEach { bundle ->
                    val row = existing.singleOrNull { it.userId == bundle.identity.userId }
                    require(row != null && row.matches(bundle, config.separationKey)) {
                        "Demo identity bootstrap conflicts with the installed trust root."
                    }
                }
            }
            activateOfflineDemoAuthority(connection, config.bundleDigest)
            connection.commit()
        } catch (exception: Exception) {
            runCatching { connection.rollback() }
            throw exception
        } finally {
            runCatching { connection.autoCommit = true }
        }
    }

    private fun readDemoRows(connection: Connection): List<InstalledIdentity> =
        connection
            .prepareStatement(
                """
                select user_id,username,password_hash,role,status,security_version,
                       credential_reuse_tag,credential_bundle_mac,credential_policy_version
                from users
                where user_id in (?, ?) or username in (?, ?)
                order by user_id
                for update
                """.trimIndent(),
            ).use { statement ->
                statement.queryTimeout = STATEMENT_TIMEOUT_SECONDS
                statement.setString(1, DemoAccounts.identities[0].userId)
                statement.setString(2, DemoAccounts.identities[1].userId)
                statement.setString(3, DemoAccounts.identities[0].username)
                statement.setString(4, DemoAccounts.identities[1].username)
                statement.executeQuery().use { rows ->
                    buildList {
                        while (rows.next()) {
                            add(
                                InstalledIdentity(
                                    userId = rows.getString("user_id"),
                                    username = rows.getString("username"),
                                    passwordHash = rows.getString("password_hash"),
                                    role = rows.getString("role"),
                                    status = rows.getString("status"),
                                    securityVersion = rows.getLong("security_version"),
                                    reuseTag = rows.getBytes("credential_reuse_tag"),
                                    bundleMac = rows.getBytes("credential_bundle_mac"),
                                    policyVersion = rows.getInt("credential_policy_version"),
                                ),
                            )
                        }
                    }
                }
            }

    private fun insertIdentity(
        connection: Connection,
        bundle: VerifiedDemoCredentialBundle,
    ) {
        val reuseTag = bundle.reuseTag
        val bundleMac = bundle.bundleMac
        try {
            connection
                .prepareStatement(
                    """
                    insert into users (
                        user_id,username,role,password_hash,status,security_version,
                        credential_reuse_tag,credential_bundle_mac,credential_policy_version
                    ) values (?, ?, ?, ?, 'ACTIVE', 1, ?, ?, ?)
                    """.trimIndent(),
                ).use { statement ->
                    statement.queryTimeout = STATEMENT_TIMEOUT_SECONDS
                    statement.setString(1, bundle.identity.userId)
                    statement.setString(2, bundle.identity.username)
                    statement.setString(3, bundle.identity.role.name)
                    statement.setString(4, bundle.passwordHash)
                    statement.setBytes(5, reuseTag)
                    statement.setBytes(6, bundleMac)
                    statement.setInt(7, bundle.policyVersion)
                    check(statement.executeUpdate() == 1)
                }
        } finally {
            reuseTag.fill(0)
            bundleMac.fill(0)
        }
    }

    private fun insertAudit(
        connection: Connection,
        config: BootstrapConfig,
    ) {
        connection
            .prepareStatement(
                """
                insert into audit_logs (
                    audit_log_id,user_id,actor_role,action,target_type,target_id,payload_json
                ) values (?, ?, 'OPERATOR', 'DEMO_IDENTITY_BOOTSTRAPPED', 'SYSTEM', 'P1_OFFLINE_DEMO',
                          jsonb_build_object('bundleDigest', ?))
                """.trimIndent(),
            ).use { statement ->
                statement.queryTimeout = STATEMENT_TIMEOUT_SECONDS
                statement.setString(1, "aud_p1_bootstrap_${config.bundleDigest.take(32)}")
                statement.setString(2, DemoAccounts.identities[1].userId)
                statement.setString(3, config.bundleDigest)
                check(statement.executeUpdate() == 1)
            }
    }

    private fun activateOfflineDemoAuthority(
        connection: Connection,
        bundleDigest: String,
    ) {
        connection
            .prepareStatement(
                """
                insert into p1_offline_demo_authority (
                    authority_id,active,credential_bundle_digest
                ) values ('P1_OFFLINE_DEMO', true, ?)
                on conflict (authority_id) do update
                set active = excluded.active,
                    credential_bundle_digest = excluded.credential_bundle_digest
                """.trimIndent(),
            ).use { statement ->
                statement.queryTimeout = STATEMENT_TIMEOUT_SECONDS
                statement.setString(1, bundleDigest)
                statement.executeUpdate()
            }
        connection
            .prepareStatement(
                """
                select active,credential_bundle_digest
                from p1_offline_demo_authority
                where authority_id='P1_OFFLINE_DEMO'
                for update
                """.trimIndent(),
            ).use { statement ->
                statement.queryTimeout = STATEMENT_TIMEOUT_SECONDS
                statement.executeQuery().use { rows ->
                    require(
                        rows.next() &&
                            rows.getBoolean("active") &&
                            rows.getString("credential_bundle_digest") == bundleDigest &&
                            !rows.next(),
                    ) { "P1 offline demo authority conflicts with the installed trust root." }
                }
            }
    }

    private data class InstalledIdentity(
        val userId: String,
        val username: String,
        val passwordHash: String,
        val role: String,
        val status: String,
        val securityVersion: Long,
        val reuseTag: ByteArray,
        val bundleMac: ByteArray,
        val policyVersion: Int,
    ) {
        fun matches(
            bundle: VerifiedDemoCredentialBundle,
            separationKey: ByteArray,
        ): Boolean =
            runCatching {
                username == bundle.identity.username &&
                    role == bundle.identity.role.name &&
                    status == "ACTIVE" &&
                    securityVersion >= 1L &&
                    DemoCredentialBundlePolicy
                        .verifyStored(
                            identity = bundle.identity,
                            passwordHash = passwordHash,
                            reuseTag = reuseTag,
                            bundleMac = bundleMac,
                            policyVersion = policyVersion,
                            separationKey = separationKey,
                        ).let { installed ->
                            try {
                                installed.passwordHash == bundle.passwordHash &&
                                    MessageDigest.isEqual(installed.reuseTagInternal(), bundle.reuseTagInternal()) &&
                                    MessageDigest.isEqual(installed.bundleMacInternal(), bundle.bundleMacInternal())
                            } finally {
                                installed.clearEvidence()
                            }
                        }
            }.getOrDefault(false)
    }

    private data class BootstrapConfig(
        val jdbcUrl: String,
        val migrationPassword: String,
        val userBundle: VerifiedDemoCredentialBundle,
        val adminBundle: VerifiedDemoCredentialBundle,
        val separationKey: ByteArray,
        val bundleDigest: String,
    ) {
        fun clearSensitiveEvidence() {
            userBundle.clearEvidence()
            adminBundle.clearEvidence()
            separationKey.fill(0)
        }

        companion object {
            fun from(environment: Map<String, String>): BootstrapConfig {
                val host = required(environment, "POSTGRES_HOST")
                val offlineDemo = environment["P1_OFFLINE_DEMO"] == "true"
                require(
                    host.lowercase(Locale.ROOT) in LOOPBACK_HOSTS ||
                        (offlineDemo && host == P1_DATABASE_SERVICE),
                ) { "POSTGRES_HOST is outside the P1 bootstrap boundary." }
                val port = required(environment, "POSTGRES_PORT").toIntOrNull()
                require(port != null && port in 1..65_535)
                val database = required(environment, "POSTGRES_DB")
                require(DATABASE_PATTERN.matches(database))
                val migrationPassword =
                    secretValue(environment, "POSTGRES_MIGRATION_PASSWORD_FILE", "POSTGRES_MIGRATION_PASSWORD")
                val serializedUser =
                    secretValue(environment, "DEMO_USER_CREDENTIAL_BUNDLE_FILE", "DEMO_USER_CREDENTIAL_BUNDLE")
                val serializedAdmin =
                    secretValue(environment, "DEMO_ADMIN_CREDENTIAL_BUNDLE_FILE", "DEMO_ADMIN_CREDENTIAL_BUNDLE")
                val encodedKey =
                    secretValue(
                        environment,
                        "DEMO_CREDENTIAL_SEPARATION_KEY_FILE",
                        "DEMO_CREDENTIAL_SEPARATION_KEY",
                    )
                val key = DemoCredentialBundlePolicy.decodeSeparationKey(encodedKey)
                return try {
                    val user = DemoCredentialBundlePolicy.verify(serializedUser, DemoAccounts.identities[0], key)
                    val admin = DemoCredentialBundlePolicy.verify(serializedAdmin, DemoAccounts.identities[1], key)
                    DemoCredentialBundlePolicy.requireSeparated(user, admin)
                    val digest =
                        HexFormat.of().formatHex(
                            MessageDigest.getInstance("SHA-256").digest(
                                "$serializedUser\n$serializedAdmin".toByteArray(StandardCharsets.UTF_8),
                            ),
                        )
                    BootstrapConfig(
                        jdbcUrl =
                            "jdbc:postgresql://$host:$port/$database" +
                                "?connectTimeout=5&socketTimeout=15&tcpKeepAlive=true",
                        migrationPassword = migrationPassword,
                        userBundle = user,
                        adminBundle = admin,
                        separationKey = key,
                        bundleDigest = digest,
                    )
                } catch (exception: Exception) {
                    key.fill(0)
                    throw exception
                }
            }

            private fun required(
                environment: Map<String, String>,
                name: String,
            ): String = environment[name]?.takeIf(String::isNotBlank) ?: error("$name is required.")

            private fun readSecret(
                environment: Map<String, String>,
                name: String,
            ): String {
                val path = Path.of(required(environment, name))
                require(Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS) && !Files.isSymbolicLink(path))
                require(Files.size(path) in 1..MAX_SECRET_BYTES)
                val value = Files.readString(path, StandardCharsets.UTF_8).trimEnd('\n', '\r')
                require(value.isNotBlank() && value.length <= MAX_SECRET_BYTES)
                return value
            }

            private fun secretValue(
                environment: Map<String, String>,
                fileName: String,
                valueName: String,
            ): String =
                environment[fileName]?.takeIf(String::isNotBlank)?.let { readSecret(environment, fileName) }
                    ?: required(environment, valueName)
        }
    }

    private const val MIGRATION_USER = "flyway"
    private const val P1_DATABASE_SERVICE = "postgres"
    private const val STATEMENT_TIMEOUT_SECONDS = 10
    private const val MAX_SECRET_BYTES = 4_096L
    private val LOOPBACK_HOSTS = setOf("127.0.0.1", "localhost")
    private val DATABASE_PATTERN = Regex("^[A-Za-z_][A-Za-z0-9_]{0,62}$")
}
