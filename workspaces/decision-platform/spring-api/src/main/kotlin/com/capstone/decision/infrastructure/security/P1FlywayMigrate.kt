package com.capstone.decision.infrastructure.security

import org.flywaydb.core.Flyway
import java.sql.DriverManager
import kotlin.system.exitProcess

object P1FlywayMigrate {
    @JvmStatic
    fun main(args: Array<String>) {
        if (args.isNotEmpty()) {
            System.err.println("p1 migration failed: unexpected_arguments")
            exitProcess(1)
        }
        try {
            migrate(System.getenv())
            println("p1 migration completed")
        } catch (_: Exception) {
            System.err.println("p1 migration failed: migration_transaction")
            exitProcess(1)
        }
    }

    fun migrate(environment: Map<String, String>) {
        val host = required(environment, "POSTGRES_HOST")
        require(host in setOf("127.0.0.1", "localhost", "postgres"))
        val port = required(environment, "POSTGRES_PORT").toIntOrNull()
        require(port != null && port in 1..65_535)
        val database = required(environment, "POSTGRES_DB")
        require(Regex("^[A-Za-z_][A-Za-z0-9_]{0,62}$").matches(database))
        val password = required(environment, "POSTGRES_MIGRATION_PASSWORD")
        val capabilityDigest = required(environment, "BROKERAGE_DB_CAPABILITY_TOKEN_SHA256")
        require(Regex("^[0-9a-f]{64}$").matches(capabilityDigest))
        val jdbcUrl = "jdbc:postgresql://$host:$port/$database?connectTimeout=5&socketTimeout=30&tcpKeepAlive=true"
        Flyway
            .configure()
            .dataSource(jdbcUrl, "flyway", password)
            .locations("classpath:db/migration", "classpath:db/baseline")
            .placeholders(
                mapOf(
                    "brokerageDbCapabilityTokenSha256" to capabilityDigest,
                ),
            ).javaMigrations(actorTrustMigration(environment))
            .load()
            .migrate()
        ensureBrokerageCapability(jdbcUrl, password, capabilityDigest)
    }

    private fun actorTrustMigration(environment: Map<String, String>): V7__s2_1_actor_trust {
        val separationKey =
            DemoCredentialBundlePolicy.decodeSeparationKey(
                required(environment, "DEMO_CREDENTIAL_SEPARATION_KEY"),
            )
        return try {
            val user =
                DemoCredentialBundlePolicy.verify(
                    required(environment, "DEMO_USER_CREDENTIAL_BUNDLE"),
                    requireNotNull(DemoAccounts.byUserId("usr_demo_user")),
                    separationKey,
                )
            val admin =
                DemoCredentialBundlePolicy.verify(
                    required(environment, "DEMO_ADMIN_CREDENTIAL_BUNDLE"),
                    requireNotNull(DemoAccounts.byUserId("usr_demo_admin")),
                    separationKey,
                )
            DemoCredentialBundlePolicy.requireSeparated(user, admin)
            V7__s2_1_actor_trust(user, admin)
        } finally {
            separationKey.fill(0)
        }
    }

    private fun ensureBrokerageCapability(
        jdbcUrl: String,
        password: String,
        expectedDigest: String,
    ) {
        DriverManager.getConnection(jdbcUrl, "flyway", password).use { connection ->
            connection.autoCommit = false
            try {
                val retired =
                    connection
                        .createStatement()
                        .use { statement ->
                            statement
                                .executeQuery(
                                    "select to_regclass('public.brokerage_internal_scope') is not null",
                                ).use { rows ->
                                    check(rows.next())
                                    rows.getBoolean(1)
                                }
                        }
                if (retired) {
                    val remaining =
                        connection
                            .createStatement()
                            .use { statement ->
                                statement
                                    .executeQuery(
                                        "select count(*) from public.brokerage_db_capability_keys",
                                    ).use { rows ->
                                        check(rows.next())
                                        rows.getLong(1)
                                    }
                            }
                    check(remaining == 0L) { "Retired brokerage bearer registry must stay empty" }
                    connection.commit()
                    return
                }
                connection
                    .prepareStatement(
                        """
                        insert into public.brokerage_db_capability_keys(capability_id,token_sha256)
                        values ('S3_1_RUNTIME',?)
                        on conflict (capability_id) do nothing
                        """.trimIndent(),
                    ).use { statement ->
                        statement.setString(1, expectedDigest)
                        statement.executeUpdate()
                    }
                val storedDigest =
                    connection
                        .prepareStatement(
                            """
                            select token_sha256
                            from public.brokerage_db_capability_keys
                            where capability_id='S3_1_RUNTIME'
                            for update
                            """.trimIndent(),
                        ).use { statement ->
                            statement.executeQuery().use { rows ->
                                check(rows.next())
                                rows.getString(1)
                            }
                        }
                check(storedDigest == expectedDigest) {
                    "P1 brokerage database capability digest conflicts with the existing database"
                }
                connection.commit()
            } catch (error: Exception) {
                connection.rollback()
                throw error
            }
        }
    }

    private fun required(
        environment: Map<String, String>,
        name: String,
    ): String = environment[name]?.takeIf(String::isNotBlank) ?: error("$name is required")
}
