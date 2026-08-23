package com.capstone.decision

import com.capstone.decision.infrastructure.security.DemoAccounts
import com.capstone.decision.infrastructure.security.DemoCredentialBundlePolicy
import com.capstone.decision.infrastructure.security.DemoCredentialRotation
import com.capstone.decision.infrastructure.security.DemoIdentityBootstrap
import com.capstone.decision.infrastructure.security.P1DatabaseRoleBootstrap
import com.capstone.decision.infrastructure.security.P1FlywayMigrate
import org.flywaydb.core.Flyway
import org.flywaydb.core.api.MigrationVersion
import org.flywaydb.core.api.migration.Context
import org.flywaydb.core.api.migration.JavaMigration
import org.junit.jupiter.api.Assertions.assertEquals
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
            NEXT_HISTORICAL_DB,
            NEXT_BASELINE_DB,
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
        P1DatabaseRoleBootstrap.bootstrap(p1RoleEnvironment(UPGRADE_DB))
        assertTrue(roleExists("decision_auth"))
        P1FlywayMigrate.migrate(p1MigrationEnvironment(UPGRADE_DB))
        P1FlywayMigrate.migrate(p1MigrationEnvironment(BOOTSTRAP_DB))
        migrate(NEXT_HISTORICAL_DB, locations = arrayOf("classpath:db/migration")).migrate()
        P1FlywayMigrate.migrate(p1MigrationEnvironment(NEXT_BASELINE_DB))
        migrateWithSyntheticNext(NEXT_HISTORICAL_DB)
        migrateWithSyntheticNext(NEXT_BASELINE_DB)
    }

    @Test
    fun `fresh database applies only B86 while existing database applies V86`() {
        assertEquals(listOf("86" to "SQL_BASELINE"), history(BASELINE_DB))
        assertEquals(86, history(HISTORICAL_DB).size)
        assertEquals("86" to "SQL", history(HISTORICAL_DB).last())
        assertEquals("86" to "SQL", history(UPGRADE_DB).last())
        assertTrue(history(UPGRADE_DB).none { it.second == "SQL_BASELINE" })
    }

    @Test
    fun `fresh baseline installs the exact brokerage capability without rotating historical state`() {
        assertEquals(
            SpringApiIntegrationTestBase.TEST_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256,
            scalar(BASELINE_DB, "select token_sha256 from brokerage_db_capability_keys where capability_id='S3_1_RUNTIME'"),
        )
        connection(BASELINE_DB, "decision_app", "app-test").use { connection ->
            connection
                .prepareStatement(
                    "select count(*) from read_mock_order_decision(?,?,?)",
                ).use { statement ->
                    statement.setString(1, "usr_missing_actor")
                    statement.setString(2, "dec_missing_decision")
                    statement.setString(3, SpringApiIntegrationTestBase.TEST_BROKERAGE_DB_CAPABILITY_TOKEN)
                    statement.executeQuery().use { rows ->
                        assertTrue(rows.next())
                        assertEquals(0L, rows.getLong(1))
                    }
                }
        }
        val conflicting = p1MigrationEnvironment(BASELINE_DB).toMutableMap()
        conflicting["BROKERAGE_DB_CAPABILITY_TOKEN_SHA256"] = "0".repeat(64)
        org.junit.jupiter.api.assertThrows<IllegalStateException> {
            P1FlywayMigrate.migrate(conflicting)
        }
        assertEquals(
            SpringApiIntegrationTestBase.TEST_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256,
            scalar(BASELINE_DB, "select token_sha256 from brokerage_db_capability_keys where capability_id='S3_1_RUNTIME'"),
        )
    }

    @Test
    fun `historical and baseline paths accept exactly one synthetic next migration`() {
        assertEquals("87" to "JDBC", history(NEXT_HISTORICAL_DB).last())
        assertEquals("87" to "JDBC", history(NEXT_BASELINE_DB).last())
        assertEquals("preserved", scalar(NEXT_HISTORICAL_DB, "select marker from p1_synthetic_v87"))
        assertEquals("preserved", scalar(NEXT_BASELINE_DB, "select marker from p1_synthetic_v87"))
        assertEquals(1L, count(NEXT_HISTORICAL_DB, "brokerage_db_capability_keys"))
        assertEquals(1L, count(NEXT_BASELINE_DB, "brokerage_db_capability_keys"))
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
                        prepareBundle(1, "p1-rotated-admin-password", separationKey),
                    "DEMO_CREDENTIAL_SEPARATION_KEY" to
                        Base64.getUrlEncoder().withoutPadding().encodeToString(separationKey),
                ),
                auditId = "aud_p1_baseline_rotation_test",
            )
            assertEquals(1L, countWhere(BOOTSTRAP_DB, "users", "role='ADMIN' and security_version=2"))
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

    private fun migrateWithSyntheticNext(database: String) {
        Flyway
            .configure()
            .dataSource(jdbcUrl(database), "flyway", FLYWAY_PASSWORD)
            .locations("classpath:db/migration", "classpath:db/baseline")
            .placeholders(
                mapOf(
                    "brokerageDbCapabilityTokenSha256" to
                        SpringApiIntegrationTestBase.TEST_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256,
                ),
            ).javaMigrations(s21ActorTrustMigration(), syntheticNextMigration())
            .load()
            .migrate()
    }

    private fun syntheticNextMigration(): JavaMigration =
        object : JavaMigration {
            override fun getVersion(): MigrationVersion = MigrationVersion.fromVersion("87")

            override fun getDescription(): String = "p1 synthetic next migration"

            override fun getChecksum(): Int = 8701

            override fun canExecuteInTransaction(): Boolean = true

            override fun migrate(context: Context) {
                context.connection.createStatement().use { statement ->
                    statement.execute(
                        """
                        create table public.p1_synthetic_v87(
                          singleton boolean primary key default true check(singleton),
                          marker text not null check(marker='preserved')
                        )
                        """.trimIndent(),
                    )
                    statement.execute(
                        "insert into public.p1_synthetic_v87(singleton,marker) values (true,'preserved')",
                    )
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
        private const val NEXT_HISTORICAL_DB = "p1_next_historical"
        private const val NEXT_BASELINE_DB = "p1_next_baseline"
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
