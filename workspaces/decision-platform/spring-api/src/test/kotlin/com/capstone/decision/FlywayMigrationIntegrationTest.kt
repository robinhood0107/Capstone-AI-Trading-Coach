package com.capstone.decision

import org.flywaydb.core.Flyway
import org.flywaydb.core.api.FlywayException
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.Arguments
import org.junit.jupiter.params.provider.MethodSource
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.dao.DataIntegrityViolationException
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import java.sql.DriverManager
import java.sql.SQLException
import java.util.Base64
import java.util.HexFormat
import java.util.stream.Stream

// pgvector/pg_trgm/Flyway 제약은 H2로 대체 검증할 수 없어 실제 PostgreSQL 컨테이너로 잠근다.
@Testcontainers
@SpringBootTest
class FlywayMigrationIntegrationTest(
    @Autowired private val jdbcTemplate: JdbcTemplate,
) : SpringApiIntegrationTestBase() {
    @Test
    fun `clean database applies V1 through V7 migrations and creates required objects`() {
        val versions = queryStrings("select version from flyway_schema_history where success order by installed_rank")
        assertEquals(listOf("1", "2", "3", "4", "5", "6", "7"), versions)

        val requiredTables =
            listOf(
                "users",
                "principles",
                "principle_versions",
                "decisions",
                "orders",
                "processed_event",
                "artifact_ingest_state",
                "rag_sources",
                "rag_chunks",
                "market_calendar",
                "opendart_quota_usage",
                "calendar_source_health",
                "calendar_observations",
                "trading_sessions",
                "trading_session_revisions",
                "calendar_events",
                "calendar_event_sources",
                "calendar_conflicts",
                "calendar_collection_cursors",
                "disclosure_risk_state_transitions",
            )
        requiredTables.forEach { tableName ->
            assertTrue(tableExists(tableName), "expected table $tableName to exist")
        }

        assertEquals(1, countMarketCalendarRows("KRX", "2026-06-23", true))
        assertEquals(1, countMarketCalendarRows("KRX", "2026-01-01", false))
        assertEquals("VIEW", tableType("market_calendar"))
        assertEquals(2, countRows("trading_sessions", "canonical_rule_version = 'V4_COMPAT_MIGRATION'"))
        assertEquals(2, countRows("trading_session_revisions", "canonical_rule_version = 'V4_COMPAT_MIGRATION'"))
        assertTrue(indexExists("idx_chunks_trgm"), "expected pg_trgm index for Korean keyword search")
        assertFalse(indexDefinitionLike("rag_chunks", "%ivfflat%"), "ivfflat must wait until real embeddings are loaded")
    }

    @Test
    fun `V7 seeds exact demo identities with attested separated credential bundles`() {
        val users =
            jdbcTemplate.query(
                """
                select user_id, username, role, status, security_version, password_hash
                from users
                where user_id in ('usr_demo_user', 'usr_demo_admin')
                order by user_id
                """.trimIndent(),
            ) { result, _ ->
                listOf(
                    result.getString("user_id"),
                    result.getString("username"),
                    result.getString("role"),
                    result.getString("status"),
                    result.getLong("security_version").toString(),
                    result.getString("password_hash"),
                )
            }
        assertEquals(listOf("usr_demo_admin", "demo-admin", "ADMIN", "ACTIVE", "1"), users[0].take(5))
        assertEquals(listOf("usr_demo_user", "demo-user", "USER", "ACTIVE", "1"), users[1].take(5))
        assertTrue(users.all { Regex("^\\$2[aby]\\$12\\$[./A-Za-z0-9]{53}$").matches(it.last()) })

        val evidence =
            jdbcTemplate.query(
                """
                select user_id,
                       octet_length(credential_reuse_tag),
                       octet_length(credential_bundle_mac),
                       credential_policy_version,
                       encode(credential_reuse_tag, 'hex')
                from users
                where user_id in ('usr_demo_user', 'usr_demo_admin')
                order by user_id
                """.trimIndent(),
            ) { result, _ ->
                listOf(
                    result.getString("user_id"),
                    result.getInt(2).toString(),
                    result.getInt(3).toString(),
                    result.getInt(4).toString(),
                    result.getString(5),
                )
            }
        assertTrue(evidence.all { it.subList(1, 4) == listOf("32", "32", "1") })
        assertNotEquals(evidence[0].last(), evidence[1].last())

        val sharedPlaintextAdminBundle =
            SpringApiIntegrationTestBase.prepareTestBundle("usr_demo_admin", TEST_USER_PASSWORD)
        assertThrows<IllegalArgumentException> {
            s21ActorTrustMigration(adminBundle = sharedPlaintextAdminBundle)
        }
        assertThrows<IllegalArgumentException> {
            s21ActorTrustMigration(userBundle = "not-a-credential-bundle")
        }
    }

    @Test
    fun `V7 permits statement logging when credential bind values are suppressed`() {
        val migrationUrl = createDatabase("migration_logging_safe")
        flyway(migrationUrl, target = "6").migrate()
        setMigrationLoggingPolicy(logStatement = "all", parameterMaxLength = 0, errorParameterMaxLength = 0)
        try {
            val logOffset = postgres.logs.length

            flyway(migrationUrl).migrate()

            val migrationLogs = postgres.logs.drop(logOffset)
            assertTrue(migrationLogs.contains("insert into users"), "statement logging did not observe V7 seed SQL")
            assertCredentialEvidenceAbsent(migrationLogs)
        } finally {
            setMigrationLoggingPolicy(logStatement = "none", parameterMaxLength = 0, errorParameterMaxLength = 0)
        }
    }

    @Test
    fun `V7 fails closed before credential binds when parameter logging is unsafe`() {
        val migrationUrl = createDatabase("migration_logging_unsafe")
        flyway(migrationUrl, target = "6").migrate()
        setMigrationLoggingPolicy(logStatement = "all", parameterMaxLength = -1, errorParameterMaxLength = -1)
        try {
            val logOffset = postgres.logs.length

            assertThrows<FlywayException> { flyway(migrationUrl).migrate() }

            assertCredentialEvidenceAbsent(postgres.logs.drop(logOffset))
            assertV7RolledBack(migrationUrl)
        } finally {
            setMigrationLoggingPolicy(logStatement = "none", parameterMaxLength = 0, errorParameterMaxLength = 0)
        }
    }

    @Test
    fun `V7 upgrade preserves unrelated users and rolls back identity conflicts without exposing hashes`() {
        val preservedUrl = createDatabase("existing_auth_user")
        flyway(preservedUrl, target = "6").migrate()
        DriverManager.getConnection(preservedUrl, postgres.username, postgres.password).use { connection ->
            connection
                .prepareStatement(
                    "insert into users (user_id, username, role, password_hash) values ('usr-existing', 'existing-user', 'USER', ?)",
                ).use { statement ->
                    statement.setString(1, TEST_USER_PASSWORD_HASH)
                    assertEquals(1, statement.executeUpdate())
                }
        }
        flyway(preservedUrl).migrate()
        DriverManager.getConnection(preservedUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.executeQuery("select count(*) from users where user_id = 'usr-existing'").use { result ->
                    assertTrue(result.next())
                    assertEquals(1, result.getInt(1))
                }
            }
        }

        val conflictUrl = createDatabase("conflicting_auth_identity")
        flyway(conflictUrl, target = "6").migrate()
        DriverManager.getConnection(conflictUrl, postgres.username, postgres.password).use { connection ->
            connection
                .prepareStatement(
                    "insert into users (user_id, username, role, password_hash) values ('usr_demo_user', 'conflicting-user', 'USER', ?)",
                ).use { statement ->
                    statement.setString(1, TEST_USER_PASSWORD_HASH)
                    assertEquals(1, statement.executeUpdate())
                }
        }

        val failure = assertThrows<FlywayException> { flyway(conflictUrl).migrate() }
        val failureText = failure.stackTraceToString()
        assertFalse(failureText.contains(TEST_USER_PASSWORD_HASH))
        assertFalse(
            failureText.contains(
                Base64.getEncoder().encodeToString(TEST_USER_PASSWORD_HASH.toByteArray()),
            ),
        )
        DriverManager.getConnection(conflictUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement
                    .executeQuery(
                        "select count(*) from information_schema.columns " +
                            "where table_schema = 'public' and table_name = 'users' " +
                            "and column_name in ('security_version', 'credential_reuse_tag', " +
                            "'credential_bundle_mac', 'credential_policy_version')",
                    ).use { result ->
                        assertTrue(result.next())
                        assertEquals(0, result.getInt(1))
                    }
                statement.executeQuery("select count(*) from flyway_schema_history where version = '7'").use { result ->
                    assertTrue(result.next())
                    assertEquals(0, result.getInt(1))
                }
            }
        }
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("remainingTrustRootConflicts")
    fun `V7 rejects every remaining demo identity conflict shape`(
        caseName: String,
        databaseName: String,
        userId: String,
        username: String,
        role: String,
        status: String,
        passwordHash: String,
    ) {
        val conflictUrl = createDatabase(databaseName)
        flyway(conflictUrl, target = "6").migrate()
        DriverManager.getConnection(conflictUrl, postgres.username, postgres.password).use { connection ->
            connection
                .prepareStatement(
                    """
                    insert into users (user_id, username, role, password_hash, status)
                    values (?, ?, ?, ?, ?)
                    """.trimIndent(),
                ).use { statement ->
                    statement.setString(1, userId)
                    statement.setString(2, username)
                    statement.setString(3, role)
                    statement.setString(4, passwordHash)
                    statement.setString(5, status)
                    assertEquals(1, statement.executeUpdate(), caseName)
                }
        }

        assertThrows<FlywayException> { flyway(conflictUrl).migrate() }
        assertV7RolledBack(conflictUrl)
    }

    @Test
    fun `calendar runtime roles receive exact allowlisted privileges`() {
        assertTrue(hasTablePrivilege("decision_collector", "opendart_quota_usage", "SELECT"))
        assertTrue(hasTablePrivilege("decision_collector", "opendart_quota_usage", "INSERT"))
        assertTrue(hasTablePrivilege("decision_collector", "opendart_quota_usage", "UPDATE"))
        assertTrue(hasTablePrivilege("decision_collector", "calendar_observations", "INSERT"))
        assertFalse(hasTablePrivilege("decision_collector", "calendar_observations", "UPDATE"))
        assertFalse(hasTablePrivilege("decision_collector", "calendar_observations", "DELETE"))
        assertTrue(hasTablePrivilege("decision_collector", "trading_session_revisions", "INSERT"))
        assertFalse(hasTablePrivilege("decision_collector", "trading_session_revisions", "UPDATE"))
        assertFalse(hasTablePrivilege("decision_collector", "users", "SELECT"))
        assertFalse(hasTablePrivilege("decision_collector", "flyway_schema_history", "SELECT"))
        assertFalse(hasSchemaPrivilege("decision_collector", "CREATE"))

        assertTrue(hasTablePrivilege("decision_app", "trading_sessions", "SELECT"))
        assertTrue(hasTablePrivilege("decision_app", "current_calendar_events", "SELECT"))
        assertTrue(hasTablePrivilege("decision_app", "active_disclosure_risk_states", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "calendar_observations", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "trading_session_revisions", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "opendart_quota_usage", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "trading_sessions", "INSERT"))
        assertFalse(hasTablePrivilege("decision_app", "flyway_schema_history", "SELECT"))
        assertFalse(hasSchemaPrivilege("decision_app", "CREATE"))
    }

    @Test
    fun `processed event rejects duplicate event per consumer`() {
        jdbcTemplate.update(
            """
            insert into processed_event (event_id, consumer_name, processed_at)
            values ('evt-duplicate', 'risk-consumer', now())
            """.trimIndent(),
        )

        assertUniqueViolation {
            jdbcTemplate.update(
                """
                insert into processed_event (event_id, consumer_name, processed_at)
                values ('evt-duplicate', 'risk-consumer', now())
                """.trimIndent(),
            )
        }
    }

    @Test
    fun `artifact ingest state rejects duplicate run file and schema version`() {
        jdbcTemplate.update(
            """
            insert into artifact_ingest_state (run_id, file_hash, schema_version, file_name, status)
            values ('run-2026-06-23', 'hash-abc', '1.0.0', 'lstm_signals.parquet', 'VALIDATED')
            """.trimIndent(),
        )

        assertUniqueViolation {
            jdbcTemplate.update(
                """
                insert into artifact_ingest_state (run_id, file_hash, schema_version, file_name, status)
                values ('run-2026-06-23', 'hash-abc', '1.0.0', 'lstm_signals.parquet', 'VALIDATED')
                """.trimIndent(),
            )
        }
    }

    @Test
    fun `orders reject reusing the same decision id`() {
        insertOrderFixture()
        jdbcTemplate.update(
            """
            insert into orders (
                order_id, user_id, account_id, decision_id, idempotency_key,
                symbol, side, order_type, quantity, status
            )
            values (
                'ord-1', 'usr-flyway', 'paper-account-1', 'dec-flyway', 'idem-order-1',
                '005930', 'BUY', 'LIMIT', 1, 'REQUESTED'
            )
            """.trimIndent(),
        )

        assertUniqueViolation {
            jdbcTemplate.update(
                """
                insert into orders (
                    order_id, user_id, account_id, decision_id, idempotency_key,
                    symbol, side, order_type, quantity, status
                )
                values (
                    'ord-2', 'usr-flyway', 'paper-account-1', 'dec-flyway', 'idem-order-2',
                    '005930', 'BUY', 'LIMIT', 1, 'REQUESTED'
                )
                """.trimIndent(),
            )
        }
    }

    private fun insertOrderFixture() {
        jdbcTemplate.update(
            """
            insert into users (user_id, username, role, password_hash)
            values ('usr-flyway', 'flyway-user', 'USER', 'test-password-hash')
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into principles (principle_id, user_id, name, mode, status)
            values ('prn-flyway', 'usr-flyway', 'Flyway Principle', 'GUIDE', 'ACTIVE')
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into principle_versions (principle_version_id, principle_id, version, rules_json)
            values ('prv-flyway-v1', 'prn-flyway', 1, '[]'::jsonb)
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into decisions (
                decision_id, user_id, account_id, principle_version_id,
                symbol, side, decision, reason_json, created_at, valid_until
            )
            values (
                'dec-flyway', 'usr-flyway', 'paper-account-1', 'prv-flyway-v1',
                '005930', 'BUY', 'ALLOW', '{}'::jsonb, now(), now() + interval '10 minutes'
            )
            """.trimIndent(),
        )
    }

    private fun createDatabase(name: String): String {
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement -> statement.execute("create database $name") }
        }
        return postgres.jdbcUrl.substringBeforeLast('/') + "/$name"
    }

    private fun setMigrationLoggingPolicy(
        logStatement: String,
        parameterMaxLength: Int,
        errorParameterMaxLength: Int,
    ) {
        require(postgres.username == "decision")
        require(logStatement in setOf("none", "all"))
        require(parameterMaxLength in setOf(-1, 0))
        require(errorParameterMaxLength in setOf(-1, 0))
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                // 다음 Flyway connection의 실제 role-level logging policy를 synthetic allowlist로 전환한다.
                statement.execute("alter role decision set log_statement = '$logStatement'")
                statement.execute("alter role decision set log_parameter_max_length = $parameterMaxLength")
                statement.execute("alter role decision set log_parameter_max_length_on_error = $errorParameterMaxLength")
            }
        }
    }

    private fun assertCredentialEvidenceAbsent(logs: String) {
        val bundles = listOf(TEST_USER_CREDENTIAL_BUNDLE, TEST_ADMIN_CREDENTIAL_BUNDLE)
        val decodedEvidence =
            bundles.flatMap { bundle ->
                val segments = bundle.split(':')
                check(segments.size == 5)
                listOf(
                    Base64.getUrlDecoder().decode(segments[2]),
                    Base64.getUrlDecoder().decode(segments[4]),
                )
            }
        try {
            val forbiddenEvidence =
                listOf(
                    TEST_USER_PASSWORD,
                    TEST_ADMIN_PASSWORD,
                    TEST_CREDENTIAL_SEPARATION_KEY,
                    TEST_USER_CREDENTIAL_BUNDLE,
                    TEST_ADMIN_CREDENTIAL_BUNDLE,
                    TEST_USER_PASSWORD_HASH,
                    TEST_ADMIN_PASSWORD_HASH,
                ) +
                    bundles.flatMap { bundle -> bundle.split(':').drop(2) } +
                    decodedEvidence.map(HexFormat.of()::formatHex)
            forbiddenEvidence.forEachIndexed { index, evidence ->
                assertFalse(logs.contains(evidence), "credential evidence index $index appeared in PostgreSQL logs")
            }
        } finally {
            decodedEvidence.forEach { it.fill(0) }
        }
    }

    private fun assertV7RolledBack(url: String) {
        DriverManager.getConnection(url, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement
                    .executeQuery(
                        "select count(*) from information_schema.columns " +
                            "where table_schema = 'public' and table_name = 'users' " +
                            "and column_name in ('security_version', 'credential_reuse_tag', " +
                            "'credential_bundle_mac', 'credential_policy_version')",
                    ).use { result ->
                        assertTrue(result.next())
                        assertEquals(0, result.getInt(1))
                    }
                statement.executeQuery("select count(*) from flyway_schema_history where version = '7'").use { result ->
                    assertTrue(result.next())
                    assertEquals(0, result.getInt(1))
                }
            }
        }
    }

    private fun flyway(
        url: String,
        target: String? = null,
    ): Flyway {
        val configuration =
            Flyway
                .configure()
                .dataSource(url, postgres.username, postgres.password)
                .locations("classpath:db/migration")
                .javaMigrations(s21ActorTrustMigration())
        target?.let(configuration::target)
        return configuration.load()
    }

    private fun tableExists(tableName: String): Boolean =
        jdbcTemplate.queryForObject(
            """
            select exists (
                select 1
                from information_schema.tables
                where table_schema = 'public' and table_name = ?
            )
            """.trimIndent(),
            Boolean::class.java,
            tableName,
        ) ?: false

    private fun indexExists(indexName: String): Boolean =
        jdbcTemplate.queryForObject(
            "select exists (select 1 from pg_indexes where schemaname = 'public' and indexname = ?)",
            Boolean::class.java,
            indexName,
        ) ?: false

    private fun indexDefinitionLike(
        tableName: String,
        pattern: String,
    ): Boolean =
        jdbcTemplate.queryForObject(
            "select exists (select 1 from pg_indexes where schemaname = 'public' and tablename = ? and indexdef like ?)",
            Boolean::class.java,
            tableName,
            pattern,
        ) ?: false

    private fun queryStrings(sql: String): List<String> = jdbcTemplate.query(sql) { rs, _ -> rs.getString(1) }

    private fun tableType(tableName: String): String =
        jdbcTemplate.queryForObject(
            "select table_type from information_schema.tables where table_schema = 'public' and table_name = ?",
            String::class.java,
            tableName,
        ) ?: ""

    private fun countRows(
        tableName: String,
        predicate: String,
    ): Int {
        require(predicate == "canonical_rule_version = 'V4_COMPAT_MIGRATION'")
        // 식별자를 SQL에 직접 보간하지 않고 이 테스트가 승인한 두 이관 대상만 조회한다.
        val sql =
            when (tableName) {
                "trading_sessions" ->
                    "select count(*) from trading_sessions where canonical_rule_version = 'V4_COMPAT_MIGRATION'"
                "trading_session_revisions" ->
                    "select count(*) from trading_session_revisions where canonical_rule_version = 'V4_COMPAT_MIGRATION'"
                else -> error("unsupported migration table: $tableName")
            }
        return jdbcTemplate.queryForObject(
            sql,
            Int::class.java,
        ) ?: 0
    }

    private fun hasTablePrivilege(
        role: String,
        table: String,
        privilege: String,
    ): Boolean =
        jdbcTemplate.queryForObject(
            "select has_table_privilege(?, 'public.' || ?, ?)",
            Boolean::class.java,
            role,
            table,
            privilege,
        ) ?: false

    private fun hasSchemaPrivilege(
        role: String,
        privilege: String,
    ): Boolean =
        jdbcTemplate.queryForObject(
            "select has_schema_privilege(?, 'public', ?)",
            Boolean::class.java,
            role,
            privilege,
        ) ?: false

    private fun countMarketCalendarRows(
        market: String,
        calendarDate: String,
        isTradingDay: Boolean,
    ): Int =
        jdbcTemplate.queryForObject(
            """
            select count(*)
            from market_calendar
            where market = ? and calendar_date = ?::date and is_trading_day = ?
            """.trimIndent(),
            Int::class.java,
            market,
            calendarDate,
            isTradingDay,
        ) ?: 0

    private fun assertUniqueViolation(block: () -> Unit) {
        val exception =
            org.junit.jupiter.api.assertThrows<DataIntegrityViolationException> {
                block()
            }
        val sqlException = exception.findSqlException()
        assertTrue(
            sqlException?.sqlState == "23505",
            "expected SQLState 23505 but was ${sqlException?.sqlState}: ${exception.mostSpecificCause.message}",
        )
    }

    private fun Throwable.findSqlException(): SQLException? {
        var current: Throwable? = this
        while (current != null) {
            if (current is SQLException) {
                return current
            }
            current = current.cause
        }
        return null
    }

    companion object {
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            PostgreSQLContainer(postgresImage)
                .withDatabaseName("decision")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")

        @DynamicPropertySource
        @JvmStatic
        fun postgresProperties(registry: DynamicPropertyRegistry) {
            registry.add("spring.datasource.url", postgres::getJdbcUrl)
            registry.add("spring.datasource.username", postgres::getUsername)
            registry.add("spring.datasource.password", postgres::getPassword)
            registry.add("spring.flyway.user", postgres::getUsername)
            registry.add("spring.flyway.password", postgres::getPassword)
        }

        @JvmStatic
        fun remainingTrustRootConflicts(): Stream<Arguments> =
            Stream.of(
                Arguments.of(
                    "username collision with another user id",
                    "auth_username_collision",
                    "usr-unrelated",
                    "demo-user",
                    "USER",
                    "ACTIVE",
                    TEST_USER_PASSWORD_HASH,
                ),
                Arguments.of(
                    "approved id and username with wrong role",
                    "auth_wrong_role",
                    "usr_demo_user",
                    "demo-user",
                    "ADMIN",
                    "ACTIVE",
                    TEST_USER_PASSWORD_HASH,
                ),
                Arguments.of(
                    "approved id and username with wrong status",
                    "auth_wrong_status",
                    "usr_demo_user",
                    "demo-user",
                    "USER",
                    "LOCKED",
                    TEST_USER_PASSWORD_HASH,
                ),
                Arguments.of(
                    "approved id and username with wrong hash",
                    "auth_wrong_hash",
                    "usr_demo_user",
                    "demo-user",
                    "USER",
                    "ACTIVE",
                    TEST_ADMIN_PASSWORD_HASH,
                ),
            )
    }
}
