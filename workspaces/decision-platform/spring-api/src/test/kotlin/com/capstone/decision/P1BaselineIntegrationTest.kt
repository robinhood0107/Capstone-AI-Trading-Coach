package com.capstone.decision

import com.capstone.decision.infrastructure.security.DemoAccounts
import com.capstone.decision.infrastructure.security.DemoCredentialBundlePolicy
import com.capstone.decision.infrastructure.security.DemoCredentialRotation
import com.capstone.decision.infrastructure.security.DemoIdentityBootstrap
import com.capstone.decision.infrastructure.security.P1DatabaseRoleBootstrap
import com.capstone.decision.infrastructure.security.P1FlywayMigrate
import org.flywaydb.core.Flyway
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.TestInstance
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import java.nio.file.Files
import java.nio.file.Path
import java.sql.DriverManager
import java.util.Base64

@Testcontainers
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class P1BaselineIntegrationTest {
    @BeforeAll
    fun prepareDatabases() {
        listOf(
            HISTORICAL_DB,
            BASELINE_DB,
            UPGRADE_DB,
            BOOTSTRAP_DB,
            HISTORICAL_CANONICAL_DB,
            BASELINE_CANONICAL_DB,
        ).forEach(::createDatabase)
        migrate(HISTORICAL_DB, locations = arrayOf("classpath:db/migration")).migrate()
        P1FlywayMigrate.migrate(p1MigrationEnvironment(BASELINE_DB))
        migrate(
            UPGRADE_DB,
            target = "85",
            locations = arrayOf("classpath:db/migration"),
        ).migrate()
        assertTrue(roleExists("decision_auth"))
        assertTrue(roleExists("decision_automation_runtime"))
        P1DatabaseRoleBootstrap.bootstrap(p1RoleEnvironment(UPGRADE_DB))
        assertTrue(roleExists("decision_auth"))
        assertTrue(roleCanConnect("decision_automation_runtime", UPGRADE_DB))
        assertTrue(roleExists("decision_outbox_publisher"))
        assertTrue(roleExists("decision_poison_recorder"))
        P1FlywayMigrate.migrate(p1MigrationEnvironment(UPGRADE_DB))
        P1FlywayMigrate.migrate(p1MigrationEnvironment(BOOTSTRAP_DB))
    }

    @Test
    fun `fresh database applies B86 through V106 while existing database applies V1 through V106`() {
        assertEquals(
            listOf(
                "86" to "SQL_BASELINE",
                "87" to "SQL",
                "88" to "SQL",
                "89" to "SQL",
                "90" to "SQL",
                "91" to "SQL",
                "92" to "SQL",
                "93" to "SQL",
                "94" to "SQL",
                "95" to "SQL",
                "96" to "SQL",
                "97" to "SQL",
                "98" to "SQL",
                "99" to "SQL",
                "100" to "SQL",
                "101" to "SQL",
                "102" to "SQL",
                "103" to "SQL",
                "104" to "SQL",
                "105" to "SQL",
                "106" to "SQL",
            ),
            history(BASELINE_DB),
        )
        assertEquals(106, history(HISTORICAL_DB).size)
        assertEquals("106" to "SQL", history(HISTORICAL_DB).last())
        assertEquals("106" to "SQL", history(UPGRADE_DB).last())
        assertTrue(history(UPGRADE_DB).none { it.second == "SQL_BASELINE" })
    }

    @Test
    fun `fresh baseline retires the historical brokerage bearer without restoring it`() {
        assertEquals(0L, count(BASELINE_DB, "brokerage_db_capability_keys"))
        connection(BASELINE_DB, "decision_app", "app-test").use { connection ->
            val denied =
                org.junit.jupiter.api.assertThrows<java.sql.SQLException> {
                    connection
                        .prepareStatement(
                            "select count(*) from read_mock_order_decision(?,?,?)",
                        ).use { statement ->
                            statement.setString(1, "usr_missing_actor")
                            statement.setString(2, "dec_missing_decision")
                            statement.setString(3, SpringApiIntegrationTestBase.TEST_BROKERAGE_DB_CAPABILITY_TOKEN)
                            statement.executeQuery()
                        }
                }
            assertEquals("42501", denied.sqlState)
        }
        val conflicting = p1MigrationEnvironment(BASELINE_DB).toMutableMap()
        conflicting["BROKERAGE_DB_CAPABILITY_TOKEN_SHA256"] = "0".repeat(64)
        P1FlywayMigrate.migrate(conflicting)
        assertEquals(0L, count(BASELINE_DB, "brokerage_db_capability_keys"))
    }

    @Test
    fun `historical and baseline paths install exact V87 capability constraints`() {
        listOf(HISTORICAL_DB, BASELINE_DB).forEach { database ->
            assertEquals(0L, count(database, "actor_request_capability"))
            assertEquals(
                "106",
                scalar(database, "select version from flyway_schema_history where success order by installed_rank desc limit 1"),
            )
        }
    }

    @Test
    fun `caller selected actor GUC cannot authorize a legacy owner function`() {
        listOf(HISTORICAL_DB, BASELINE_DB).forEach { database ->
            connection(database, "decision_app", "app-test").use { connection ->
                connection.autoCommit = false
                connection
                    .prepareStatement("select pg_catalog.set_config('app.actor_user_id',?,true)")
                    .use { statement ->
                        statement.setString(1, "usr_forged_owner")
                        statement.executeQuery().use { rows -> assertTrue(rows.next()) }
                    }
                connection
                    .createStatement()
                    .executeQuery(
                        "select public.current_setting('app.actor_user_id',true)," +
                            "public.actor_rls_scope_is_open_v1()",
                    ).use { rows ->
                        assertTrue(rows.next())
                        assertNull(rows.getString(1))
                        assertFalse(rows.getBoolean(2))
                    }
                val denied =
                    org.junit.jupiter.api.assertThrows<java.sql.SQLException> {
                        connection
                            .prepareStatement("select * from read_effective_rag_consent(?)")
                            .use { statement ->
                                statement.setString(1, "usr_forged_owner")
                                statement.executeQuery()
                            }
                    }
                assertEquals("42501", denied.sqlState)
                connection.rollback()
            }
        }
    }

    @Test
    fun `decision idempotency lifetime uses database time rather than caller timestamp`() {
        val definition =
            connection(HISTORICAL_DB, postgres.username, postgres.password).use { connection ->
                connection
                    .prepareStatement(
                        "select pg_get_functiondef('public.persist_decision_bundle_authorized(text,jsonb)'::regprocedure)",
                    ).use { statement ->
                        statement.executeQuery().use { rows ->
                            check(rows.next())
                            rows.getString(1)
                        }
                    }
            }
        assertTrue(definition.contains("database_now timestamptz := statement_timestamp()"))
        val compact = definition.filterNot(Char::isWhitespace)
        assertTrue(compact.contains("item.expires_at>database_now"))
        assertTrue(compact.contains("database_now,database_now+interval'24hours'"))
    }

    @Test
    fun `historical and baseline paths have canonical schema and static seed parity`() {
        val historicalSchema = canonicalSchemaDump(HISTORICAL_DB, HISTORICAL_CANONICAL_DB)
        val baselineSchema = canonicalSchemaDump(BASELINE_DB, BASELINE_CANONICAL_DB)
        val output = Path.of("build/p1-baseline-parity")
        Files.createDirectories(output)
        Files.writeString(output.resolve("historical.sql"), historicalSchema)
        Files.writeString(output.resolve("baseline.sql"), baselineSchema)
        assertEquals(historicalSchema, baselineSchema)
        assertEquals(staticSeedFingerprint(HISTORICAL_DB), staticSeedFingerprint(BASELINE_DB))
        listOf(
            "users",
            "audit_logs",
            "event_outbox",
            "async_job",
            "p1_offline_demo_authority",
            "rag_v2_public_corpus_state",
            "rag_v2_immutable_public_bundle_pointers",
        ).forEach { table ->
            assertEquals(0L, count(BASELINE_DB, table), "baseline must exclude runtime row $table")
        }
    }

    @Test
    fun `baseline identity bootstrap is idempotent and rotation compatible`() {
        val separationKey = ByteArray(32) { index -> (index + 17).toByte() }
        val userBundle = prepareBundle(0, "p1-synthetic-user-password", separationKey)
        val adminBundle = prepareBundle(1, "p1-synthetic-admin-password", separationKey)
        val rotatedAdminBundle = prepareBundle(1, "p1-rotated-admin-password", separationKey)
        val directory = Files.createTempDirectory("p1-bootstrap-test")
        val environment =
            mapOf(
                "POSTGRES_HOST" to postgres.host,
                "POSTGRES_PORT" to postgres.getMappedPort(5432).toString(),
                "POSTGRES_DB" to BOOTSTRAP_DB,
                "POSTGRES_MIGRATION_PASSWORD_FILE" to
                    secretFile(directory, "migration", FLYWAY_PASSWORD).toString(),
                "DEMO_USER_CREDENTIAL_BUNDLE_FILE" to secretFile(directory, "user", userBundle).toString(),
                "DEMO_ADMIN_CREDENTIAL_BUNDLE_FILE" to secretFile(directory, "admin", adminBundle).toString(),
                "DEMO_CREDENTIAL_SEPARATION_KEY_FILE" to
                    secretFile(
                        directory,
                        "key",
                        Base64.getUrlEncoder().withoutPadding().encodeToString(separationKey),
                    ).toString(),
            )
        try {
            DemoIdentityBootstrap.bootstrap(environment)
            DemoIdentityBootstrap.bootstrap(environment)
            assertEquals(2L, count(BOOTSTRAP_DB, "users"))
            assertEquals(1L, countWhere(BOOTSTRAP_DB, "audit_logs", "action='DEMO_IDENTITY_BOOTSTRAPPED'"))
            assertEquals(1L, countWhere(BOOTSTRAP_DB, "p1_offline_demo_authority", "active"))

            DemoCredentialRotation.rotate(
                mapOf(
                    "POSTGRES_HOST" to postgres.host,
                    "POSTGRES_PORT" to postgres.getMappedPort(5432).toString(),
                    "POSTGRES_DB" to BOOTSTRAP_DB,
                    "POSTGRES_MIGRATION_PASSWORD" to FLYWAY_PASSWORD,
                    "DEMO_CREDENTIAL_USER_ID" to DemoAccounts.identities[1].userId,
                    "DEMO_CREDENTIAL_ROTATION_ACTOR" to "p1-baseline-parity-test",
                    "DEMO_CREDENTIAL_BUNDLE" to
                        rotatedAdminBundle,
                    "DEMO_CREDENTIAL_SEPARATION_KEY" to
                        Base64.getUrlEncoder().withoutPadding().encodeToString(separationKey),
                ),
                auditId = "aud_p1_baseline_rotation_test",
            )
            assertEquals(1L, countWhere(BOOTSTRAP_DB, "users", "role='ADMIN' and security_version=2"))
            Files.writeString(Path.of(environment.getValue("DEMO_ADMIN_CREDENTIAL_BUNDLE_FILE")), rotatedAdminBundle)
            DemoIdentityBootstrap.bootstrap(environment)
            DemoIdentityBootstrap.bootstrap(environment)
            assertEquals(2L, count(BOOTSTRAP_DB, "users"))
            assertEquals(1L, countWhere(BOOTSTRAP_DB, "audit_logs", "action='DEMO_IDENTITY_BOOTSTRAPPED'"))
            assertEquals(1L, countWhere(BOOTSTRAP_DB, "p1_offline_demo_authority", "active"))
        } finally {
            separationKey.fill(0)
            directory.toFile().deleteRecursively()
        }
    }

    private fun createDatabase(database: String) {
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.execute("create database $database owner decision")
            }
        }
        connection(database, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.execute("create extension if not exists vector")
                statement.execute("create extension if not exists pg_trgm")
                statement.execute("create extension if not exists pgcrypto")
                statement.execute("revoke create on schema public from public")
                statement.execute("grant usage on schema public to flyway")
                statement.execute("grant create on schema public to flyway")
            }
        }
    }

    private fun migrate(
        database: String,
        target: String? = null,
        locations: Array<String>,
    ): Flyway {
        val configuration =
            Flyway
                .configure()
                .dataSource(jdbcUrl(database), "flyway", FLYWAY_PASSWORD)
                .locations(*locations)
                .placeholders(
                    mapOf(
                        "brokerageDbCapabilityTokenSha256" to
                            SpringApiIntegrationTestBase.TEST_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256,
                    ),
                ).javaMigrations(s21ActorTrustMigration())
        if (target != null) configuration.target(target)
        return configuration.load()
    }

    private fun p1MigrationEnvironment(database: String): Map<String, String> =
        mapOf(
            "POSTGRES_HOST" to postgres.host,
            "POSTGRES_PORT" to postgres.getMappedPort(5432).toString(),
            "POSTGRES_DB" to database,
            "POSTGRES_MIGRATION_PASSWORD" to FLYWAY_PASSWORD,
            "DEMO_CREDENTIAL_SEPARATION_KEY" to SpringApiIntegrationTestBase.TEST_CREDENTIAL_SEPARATION_KEY,
            "DEMO_USER_CREDENTIAL_BUNDLE" to SpringApiIntegrationTestBase.TEST_USER_CREDENTIAL_BUNDLE,
            "DEMO_ADMIN_CREDENTIAL_BUNDLE" to SpringApiIntegrationTestBase.TEST_ADMIN_CREDENTIAL_BUNDLE,
            "BROKERAGE_DB_CAPABILITY_TOKEN_SHA256" to
                SpringApiIntegrationTestBase.TEST_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256,
        )

    private fun p1RoleEnvironment(database: String): Map<String, String> =
        mapOf(
            "POSTGRES_HOST" to postgres.host,
            "POSTGRES_PORT" to postgres.getMappedPort(5432).toString(),
            "POSTGRES_DB" to database,
            "POSTGRES_ADMIN_USER" to postgres.username,
            "POSTGRES_PASSWORD" to "baseline-admin-test",
            "POSTGRES_AUTH_PASSWORD" to "a".repeat(64),
            "POSTGRES_AUTOMATION_RUNTIME_PASSWORD" to "d".repeat(64),
            "POSTGRES_OUTBOX_PUBLISHER_PASSWORD" to "b".repeat(64),
            "POSTGRES_POISON_RECORDER_PASSWORD" to "c".repeat(64),
        )

    private fun roleExists(role: String): Boolean =
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.prepareStatement("select exists(select 1 from pg_roles where rolname=?)").use { statement ->
                statement.setString(1, role)
                statement.executeQuery().use { rows ->
                    check(rows.next())
                    rows.getBoolean(1)
                }
            }
        }

    private fun roleCanConnect(
        role: String,
        database: String,
    ): Boolean =
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.prepareStatement("select has_database_privilege(?, ?, 'CONNECT')").use { statement ->
                statement.setString(1, role)
                statement.setString(2, database)
                statement.executeQuery().use { rows ->
                    check(rows.next())
                    rows.getBoolean(1)
                }
            }
        }

    private fun history(database: String): List<Pair<String, String>> =
        connection(database, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement
                    .executeQuery(
                        "select version,type from flyway_schema_history where success order by installed_rank",
                    ).use { rows ->
                        buildList {
                            while (rows.next()) add(rows.getString(1) to rows.getString(2))
                        }
                    }
            }
        }

    private fun schemaDump(database: String): String {
        val result =
            postgres.execInContainer(
                "pg_dump",
                "--dbname=$database",
                "--username=decision",
                "--schema-only",
                "--no-comments",
                "--no-publications",
                "--no-security-labels",
                "--no-subscriptions",
                "--exclude-table=public.flyway_schema_history",
                "--column-inserts",
                "--rows-per-insert=1",
            )
        assertEquals(0, result.exitCode)
        return result.stdout
            .lineSequence()
            .filterNot { it.startsWith("\\restrict ") || it.startsWith("\\unrestrict ") || it.startsWith("--") }
            .joinToString("\n")
            .trim()
    }

    private fun canonicalSchemaDump(
        sourceDatabase: String,
        targetDatabase: String,
    ): String {
        val dumpPath = "/tmp/$sourceDatabase-schema.sql"
        val dump =
            postgres.execInContainer(
                "pg_dump",
                "--dbname=$sourceDatabase",
                "--username=decision",
                "--schema-only",
                "--no-comments",
                "--no-publications",
                "--no-security-labels",
                "--no-subscriptions",
                "--exclude-table=public.flyway_schema_history",
                "--file=$dumpPath",
            )
        assertEquals(0, dump.exitCode)
        val restore =
            postgres.execInContainer(
                "psql",
                "--dbname=$targetDatabase",
                "--username=decision",
                "--set=ON_ERROR_STOP=1",
                "--file=$dumpPath",
            )
        assertEquals(0, restore.exitCode, restore.stderr)
        return schemaDump(targetDatabase)
    }

    private fun staticSeedFingerprint(database: String): Map<String, Pair<Long, String>> =
        STATIC_TABLES.associateWith { table ->
            connection(database, postgres.username, postgres.password).use { connection ->
                val timestampColumns =
                    connection
                        .prepareStatement(
                            """
                            select column_name from information_schema.columns
                            where table_schema='public' and table_name=?
                              and data_type in ('timestamp with time zone','timestamp without time zone')
                            order by column_name
                            """.trimIndent(),
                        ).use { statement ->
                            statement.setString(1, table)
                            statement.executeQuery().use { rows ->
                                buildList { while (rows.next()) add(rows.getString(1)) }
                            }
                        }
                val strip =
                    if (timestampColumns.isEmpty()) {
                        "to_jsonb(seed)"
                    } else {
                        "to_jsonb(seed) - ARRAY[${timestampColumns.joinToString(",") { "'$it'" }}]"
                    }
                connection.createStatement().use { statement ->
                    statement
                        .executeQuery(
                            """
                            with canonical as (
                              select $strip payload from public."$table" seed
                            )
                            select count(*),encode(digest(coalesce(string_agg(payload::text,E'\\n' order by payload::text),''),'sha256'),'hex')
                            from canonical
                            """.trimIndent(),
                        ).use { rows ->
                            check(rows.next())
                            rows.getLong(1) to rows.getString(2)
                        }
                }
            }
        }

    private fun scalar(
        database: String,
        sql: String,
    ): String =
        connection(database, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.executeQuery(sql).use { rows ->
                    check(rows.next())
                    rows.getString(1)
                }
            }
        }

    private fun count(
        database: String,
        table: String,
    ): Long =
        connection(database, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.executeQuery("select count(*) from public.\"$table\"").use { rows ->
                    check(rows.next())
                    rows.getLong(1)
                }
            }
        }

    private fun countWhere(
        database: String,
        table: String,
        predicate: String,
    ): Long =
        connection(database, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.executeQuery("select count(*) from public.\"$table\" where $predicate").use { rows ->
                    check(rows.next())
                    rows.getLong(1)
                }
            }
        }

    private fun prepareBundle(
        identityIndex: Int,
        password: String,
        separationKey: ByteArray,
    ): String {
        val chars = password.toCharArray()
        return try {
            DemoCredentialBundlePolicy.prepare(
                DemoAccounts.identities[identityIndex],
                chars,
                separationKey,
                BCryptPasswordEncoder(12),
            )
        } finally {
            chars.fill('\u0000')
        }
    }

    private fun secretFile(
        directory: Path,
        name: String,
        value: String,
    ): Path = directory.resolve(name).also { Files.writeString(it, value) }

    private fun connection(
        database: String,
        username: String,
        password: String,
    ) = DriverManager.getConnection(jdbcUrl(database), username, password)

    private fun jdbcUrl(database: String): String =
        "jdbc:postgresql://${postgres.host}:${postgres.getMappedPort(5432)}/$database?loggerLevel=OFF"

    companion object {
        private const val FLYWAY_PASSWORD = "flyway-test"
        private const val HISTORICAL_DB = "p1_historical"
        private const val BASELINE_DB = "p1_baseline"
        private const val UPGRADE_DB = "p1_upgrade"
        private const val BOOTSTRAP_DB = "p1_bootstrap"
        private const val HISTORICAL_CANONICAL_DB = "p1_historical_canonical"
        private const val BASELINE_CANONICAL_DB = "p1_baseline_canonical"
        private val STATIC_TABLES =
            listOf(
                "async_event_registry",
                "principle_presets",
                "rag_embedding_policy_state",
                "rag_v2_immutable_exact30_source_allowlist",
                "rag_v2_immutable_external_exact30_source_allowlist",
                "rag_v2_immutable_oa_track_catalog",
                "risk_kill_switch",
                "trading_session_revisions",
                "trading_sessions",
            )
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            stablePostgresContainer(postgresImage)
                .withDatabaseName("trading")
                .withUsername("decision")
                .withPassword("baseline-admin-test")
                .withInitScript("db/test-init-calendar-roles.sql")
    }
}
