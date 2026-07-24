package com.capstone.decision

import com.capstone.decision.application.risk.port.EvaluationSourceRequest
import com.capstone.decision.application.risk.port.InstrumentCatalogPort
import com.capstone.decision.application.risk.port.OrderMetricPort
import com.capstone.decision.application.risk.port.PortfolioContextResolution
import com.capstone.decision.application.risk.port.RiskSnapshotPort
import com.capstone.decision.domain.risk.MetricCell
import com.capstone.decision.domain.risk.MetricSource
import com.capstone.decision.domain.risk.MetricValue
import com.capstone.decision.domain.risk.OrderIntentSnapshot
import com.capstone.decision.domain.risk.PortfolioSource
import com.capstone.decision.infrastructure.risk.JdbcInternalPaperBalanceAdapter
import com.capstone.decision.infrastructure.risk.JdbcKisMockBalanceAdapter
import com.capstone.decision.infrastructure.risk.JdbcMarketQuoteAdapter
import com.capstone.decision.infrastructure.risk.JdbcPortfolioContextAdapter
import com.capstone.decision.infrastructure.risk.JdbcStoredMarginAdapter
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
import org.springframework.transaction.support.TransactionSynchronizationManager
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import java.sql.DriverManager
import java.sql.SQLException
import java.time.Instant
import java.util.Base64
import java.util.HexFormat
import java.util.stream.Stream

// pgvector/pg_trgm/Flyway 제약은 H2로 대체 검증할 수 없어 실제 PostgreSQL 컨테이너로 잠근다.
@Testcontainers
@SpringBootTest
class FlywayMigrationIntegrationTest(
    @Autowired private val jdbcTemplate: JdbcTemplate,
    @Autowired private val portfolioContextAdapter: JdbcPortfolioContextAdapter,
    @Autowired private val marketQuoteAdapter: JdbcMarketQuoteAdapter,
    @Autowired private val kisMockBalanceAdapter: JdbcKisMockBalanceAdapter,
    @Autowired private val internalPaperBalanceAdapter: JdbcInternalPaperBalanceAdapter,
    @Autowired private val storedMarginAdapter: JdbcStoredMarginAdapter,
    @Autowired private val instrumentCatalogPort: InstrumentCatalogPort,
    @Autowired private val orderMetricPort: OrderMetricPort,
    @Autowired private val riskSnapshotPort: RiskSnapshotPort,
) : SpringApiIntegrationTestBase() {
    @Test
    fun `clean database applies V1 through V9 migrations and creates required objects`() {
        val versions = queryStrings("select version from flyway_schema_history where success order by installed_rank")
        assertEquals(listOf("1", "2", "3", "4", "5", "6", "7", "8", "9"), versions)

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
                "market_quote_observations",
                "instrument_catalog_observations",
                "portfolio_balance_observations",
                "portfolio_position_observations",
                "deterministic_risk_observations",
                "daily_order_count_observations",
                "corporation_registry_observations",
                "decision_artifacts",
                "decision_traces",
                "decision_idempotency_results",
                "decision_owner_projection",
                "decision_audit_projection",
                "latest_market_quote_observations",
                "latest_instrument_catalog_observations",
                "latest_portfolio_balance_observations",
                "latest_deterministic_risk_observations",
                "latest_daily_order_count_observations",
                "current_corporation_registry_projection",
                "disclosure_event_observation_projection",
                "disclosure_collection_status_projection",
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
    fun `fresh V9 migration leaves every production source table empty`() {
        val migrationUrl = createDatabase("v9_source_seed_zero")
        flyway(migrationUrl).migrate()

        val sourceTables =
            listOf(
                "market_quote_observations",
                "instrument_catalog_observations",
                "portfolio_balance_observations",
                "portfolio_position_observations",
                "deterministic_risk_observations",
                "daily_order_count_observations",
                "corporation_registry_observations",
            )
        DriverManager.getConnection(migrationUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                sourceTables.forEach { tableName ->
                    statement.executeQuery("select count(*) from $tableName").use { result ->
                        assertTrue(result.next())
                        assertEquals(0, result.getInt(1), "$tableName must not receive production seed rows")
                    }
                }
            }
        }
    }

    @Test
    fun `V9 populated precondition failure preserves the complete V8 schema and row`() {
        val migrationUrl = createDatabase("v9_populated_precondition")
        flyway(migrationUrl, target = "8").migrate()
        DriverManager.getConnection(migrationUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.execute(
                    """
                    insert into principles (
                      principle_id, user_id, preset_id, title, mode, status, current_version
                    ) values (
                      'prn-v9-guard', 'usr_demo_user', 'balanced',
                      'V9 guard fixture', 'GUIDE', 'ACTIVE', 1
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into principle_versions (
                      principle_version_id, principle_id, version, preset_id, title,
                      mode, status, rules_json, changed_fields, created_by
                    )
                    select
                      'prv-v9-guard', 'prn-v9-guard', 1, preset_id, 'V9 guard fixture',
                      'GUIDE', 'ACTIVE', rules_json, array['title'], 'usr_demo_user'
                    from principle_presets
                    where preset_id = 'balanced'
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into decisions (
                      decision_id, user_id, account_id, principle_version_id,
                      symbol, side, decision, mode, reason_json,
                      signal_snapshot_json, created_at, valid_until
                    ) values (
                      'dec-v9-guard', 'usr_demo_user', 'sanitized-account-scope',
                      'prv-v9-guard', '005930', 'BUY', 'HOLD', 'GUIDE',
                      '{}'::jsonb, '{}'::jsonb, now(), now() + interval '10 minutes'
                    )
                    """.trimIndent(),
                )
            }
        }

        val failure = assertThrows<FlywayException> { flyway(migrationUrl).migrate() }
        assertTrue(failure.stackTraceToString().contains("S2.3 V9 precondition failed"))

        DriverManager.getConnection(migrationUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.executeQuery("select count(*) from decisions where decision_id = 'dec-v9-guard'").use {
                    assertTrue(it.next())
                    assertEquals(1, it.getInt(1))
                }
                statement
                    .executeQuery(
                        """
                        select
                          count(*) filter (where column_name = 'account_id'),
                          count(*) filter (where column_name = 'evaluation_id')
                        from information_schema.columns
                        where table_schema = 'public' and table_name = 'decisions'
                        """.trimIndent(),
                    ).use {
                        assertTrue(it.next())
                        assertEquals(1, it.getInt(1))
                        assertEquals(0, it.getInt(2))
                    }
                statement
                    .executeQuery(
                        "select version from flyway_schema_history where success order by installed_rank desc limit 1",
                    ).use {
                        assertTrue(it.next())
                        assertEquals("8", it.getString(1))
                    }
            }
        }
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
    fun `decision application role has exact append only V9 privileges`() {
        listOf(
            "decisions",
            "decision_violations",
            "decision_artifacts",
            "decision_traces",
            "audit_logs",
            "event_outbox",
            "decision_idempotency_results",
        ).forEach { table ->
            assertTrue(hasTablePrivilege("decision_app", table, "INSERT"), "missing INSERT on $table")
        }
        listOf(
            "decision_owner_projection",
            "decision_audit_projection",
            "latest_market_quote_observations",
            "latest_portfolio_balance_observations",
            "active_paper_portfolio_projection",
            "latest_instrument_catalog_observations",
            "latest_deterministic_risk_observations",
            "latest_daily_order_count_observations",
            "current_corporation_registry_projection",
            "disclosure_event_observation_projection",
            "disclosure_collection_status_projection",
        ).forEach { table ->
            assertTrue(hasTablePrivilege("decision_app", table, "SELECT"), "missing SELECT on $table")
        }
        listOf(
            "decisions",
            "decision_violations",
            "decision_artifacts",
            "decision_traces",
            "audit_logs",
            "event_outbox",
        ).forEach { table ->
            assertFalse(hasTablePrivilege("decision_app", table, "SELECT"), "unexpected SELECT on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "UPDATE"), "unexpected UPDATE on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "DELETE"), "unexpected DELETE on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "TRUNCATE"), "unexpected TRUNCATE on $table")
        }
        listOf(
            "market_quote_observations",
            "portfolio_balance_observations",
            "portfolio_position_observations",
            "instrument_catalog_observations",
            "deterministic_risk_observations",
            "daily_order_count_observations",
            "corporation_registry_observations",
        ).forEach { table ->
            assertFalse(hasTablePrivilege("decision_app", table, "SELECT"), "unexpected source SELECT on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "INSERT"), "unexpected source INSERT on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "UPDATE"), "unexpected source UPDATE on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "DELETE"), "unexpected source DELETE on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "TRUNCATE"), "unexpected source TRUNCATE on $table")
        }
        assertFalse(hasTablePrivilege("decision_app", "rag_answers", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "decision_idempotency_results", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "flyway_schema_history", "SELECT"))
        assertFalse(hasSchemaPrivilege("decision_app", "CREATE"))

        val roleFlags =
            jdbcTemplate.queryForMap(
                """
                select rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls
                from pg_roles
                where rolname = 'decision_app'
                """.trimIndent(),
            )
        assertTrue(roleFlags.values.all { it == false })
    }

    @Test
    fun `decision application role receives SQLSTATE 42501 for forbidden history source and schema operations`() {
        listOf(
            "update decisions set outcome = outcome where false",
            "delete from decisions where false",
            "truncate table decisions",
            "update audit_logs set action = action where false",
            "delete from audit_logs where false",
            "truncate table audit_logs",
            "update event_outbox set status = status where false",
            "delete from event_outbox where false",
            "truncate table event_outbox",
            "insert into market_quote_observations " +
                "(observation_id, symbol, source, price_krw, completeness, observed_at, received_at, " +
                "schema_version, source_version, payload_json, source_ref, artifact_hash) values " +
                "('forbidden', '005930', 'KIS_MOCK', 1, 'COMPLETE', now(), now(), 'v1', 'v1', " +
                "'{}'::jsonb, repeat('a', 64), repeat('b', 64))",
            "select * from rag_answers limit 0",
            "select * from decisions limit 0",
            "select * from audit_logs limit 0",
            "select * from event_outbox limit 0",
            "select * from decision_idempotency_results limit 0",
            "update flyway_schema_history set success = success where false",
            "create table s23_forbidden_schema_write (id integer)",
        ).forEach(::assertDecisionAppPermissionDenied)
    }

    @Test
    fun `V9 owner views use invoker mode while bounded functions keep base tables denied`() {
        listOf("decision_owner_projection", "decision_audit_projection").forEach { view ->
            val options =
                jdbcTemplate.queryForObject(
                    "select coalesce(array_to_string(reloptions, ','), '') from pg_class where oid = ?::regclass",
                    String::class.java,
                    view,
                ) ?: ""
            assertTrue(options.contains("security_invoker=true"), "$view must be security_invoker")
        }
        assertTrue(hasFunctionPrivilege("decision_app", "find_decision_idempotency_result(text,text,timestamp with time zone)"))
        assertFalse(hasTablePrivilege("decision_app", "decision_idempotency_results", "SELECT"))

        val probe = "s23_future_acl_probe"
        jdbcTemplate.execute("drop table if exists $probe")
        try {
            DriverManager.getConnection(postgres.jdbcUrl, "flyway", FLYWAY_PASSWORD).use { connection ->
                connection.createStatement().use { statement ->
                    statement.execute("create table $probe (id integer)")
                }
            }
            assertDecisionAppPermissionDenied("select * from $probe")
        } finally {
            jdbcTemplate.execute("drop table if exists $probe")
        }
    }

    @Test
    fun `source writer roles can append only their own bounded observations`() {
        assertWriterInsert(
            "decision_market_writer",
            MARKET_WRITER_PASSWORD,
            """
            insert into instrument_catalog_observations (
              observation_id, symbol, is_etf_etn, is_gold_etf_etn, product_risk_score,
              catalog_version, observed_at, received_at, completeness, schema_version,
              source_version, payload_json, source_ref, artifact_hash
            ) values (
              'ins-writer-role', 'WRITER01', true, false, 0.25, 'catalog-writer-v1',
              '2031-01-01T00:00:00Z', '2031-01-01T00:00:01Z', 'COMPLETE',
              'instrument-catalog-observation.v1', 'fixture-v1',
              '{"symbol":"WRITER01"}'::jsonb, repeat('1', 64), repeat('2', 64)
            )
            """.trimIndent(),
        )
        assertWriterInsert(
            "decision_market_writer",
            MARKET_WRITER_PASSWORD,
            """
            insert into market_quote_observations (
              observation_id, symbol, source, price_krw, bid_krw, ask_krw,
              observed_at, received_at, completeness, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) values (
              'quote-writer-role', 'WRITER02', 'KIS_MOCK', 1000, 990, 1010,
              '2031-01-01T00:00:00Z', '2031-01-01T00:00:01Z', 'COMPLETE',
              'market-quote-observation.v1', 'fixture-v1',
              '{"symbol":"WRITER02"}'::jsonb, repeat('b', 64), repeat('c', 64)
            )
            """.trimIndent(),
        )
        assertWriterInsert(
            "decision_risk_writer",
            RISK_WRITER_PASSWORD,
            """
            insert into deterministic_risk_observations (
              observation_id, owner_user_id, owner_scope_hash, portfolio_source,
              daily_loss_rate, max_drawdown, annualized_volatility, completeness,
              observed_at, received_at, schema_version, source_version, payload_json,
              source_ref, artifact_hash
            ) values (
              'risk-writer-role', 'usr_demo_user', repeat('3', 64), 'KIS_MOCK',
              -0.01, -0.05, 0.20, 'COMPLETE',
              '2026-06-23T06:31:00Z', '2026-06-23T06:31:01Z',
              'deterministic-risk-observation.v1', 'fixture-v1',
              '{"ownerScopeHash":"sanitized"}'::jsonb, repeat('4', 64), repeat('5', 64)
            )
            """.trimIndent(),
        )
        assertWriterInsert(
            "decision_risk_writer",
            RISK_WRITER_PASSWORD,
            """
            insert into daily_order_count_observations (
              observation_id, owner_user_id, owner_scope_hash, portfolio_source,
              trading_date, order_count, covered_through, completeness,
              observed_at, received_at, schema_version, source_version, payload_json,
              source_ref, artifact_hash
            ) values (
              'orders-writer-role', 'usr_demo_user', repeat('d', 64), 'KIS_MOCK',
              '2031-01-01', 0, '2031-01-01T00:00:00Z', 'COMPLETE',
              '2031-01-01T00:00:00Z', '2031-01-01T00:00:01Z',
              'daily-order-count-observation.v1', 'fixture-v1', '{}'::jsonb,
              repeat('e', 64), repeat('f', 64)
            )
            """.trimIndent(),
        )
        assertWriterInsert(
            "decision_portfolio_writer",
            PORTFOLIO_WRITER_PASSWORD,
            """
            insert into portfolio_balance_observations (
              observation_id, owner_user_id, account_scope_hash, source, context_status,
              cash_krw, portfolio_equity_krw, margin_requirement_krw, completeness,
              position_count, observed_at, received_at, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) values (
              'balance-writer-role', 'usr_demo_admin', repeat('6', 64), 'KIS_MOCK', 'ACTIVE',
              1, 1, 0, 'COMPLETE', 1, '2031-01-01T00:00:00Z', '2031-01-01T00:00:01Z',
              'portfolio-balance-observation.v1', 'fixture-v1', '{}'::jsonb,
              repeat('7', 64), repeat('8', 64)
            )
            """.trimIndent(),
        )
        assertWriterInsert(
            "decision_portfolio_writer",
            PORTFOLIO_WRITER_PASSWORD,
            """
            insert into portfolio_position_observations (
              balance_observation_id, symbol, quantity, market_value_krw, is_gold_etf_etn
            ) values ('balance-writer-role', 'WRITER03', 1, 1, false)
            """.trimIndent(),
        )
        assertWriterInsert(
            "decision_collector",
            COLLECTOR_PASSWORD,
            """
            insert into corporation_registry_observations (
              observation_id, symbol, corp_code, registry_status, completeness,
              observed_at, received_at, schema_version, source_version, payload_json,
              source_ref, artifact_hash
            ) values (
              'corp-writer-role', '999998', '12345678', 'ACTIVE', 'COMPLETE',
              '2031-01-01T00:00:00Z', '2031-01-01T00:00:01Z',
              'corporation-registry-observation.v1', 'fixture-v1',
              '{"symbol":"999998"}'::jsonb, repeat('9', 64), repeat('a', 64)
            )
            """.trimIndent(),
        )

        assertRolePermissionDenied(
            "decision_market_writer",
            MARKET_WRITER_PASSWORD,
            "select * from instrument_catalog_observations",
        )
        assertRolePermissionDenied(
            "decision_market_writer",
            MARKET_WRITER_PASSWORD,
            "insert into deterministic_risk_observations " +
                "(observation_id, owner_user_id, owner_scope_hash, portfolio_source, completeness, " +
                "observed_at, received_at, schema_version, source_version, payload_json, source_ref, artifact_hash) " +
                "values ('forbidden-risk', 'usr_demo_user', repeat('b',64), 'KIS_MOCK', 'PARTIAL', " +
                "now(), now(), 'v1', 'v1', '{}'::jsonb, repeat('c',64), repeat('d',64))",
        )
        assertRolePermissionDenied(
            "decision_market_writer",
            MARKET_WRITER_PASSWORD,
            "create table forbidden_writer_ddl (id integer)",
        )
        val writerOwnedTables =
            listOf(
                arrayOf(
                    "decision_market_writer",
                    MARKET_WRITER_PASSWORD,
                    "market_quote_observations",
                    "observation_id = observation_id",
                ),
                arrayOf(
                    "decision_market_writer",
                    MARKET_WRITER_PASSWORD,
                    "instrument_catalog_observations",
                    "observation_id = observation_id",
                ),
                arrayOf(
                    "decision_portfolio_writer",
                    PORTFOLIO_WRITER_PASSWORD,
                    "portfolio_balance_observations",
                    "observation_id = observation_id",
                ),
                arrayOf(
                    "decision_portfolio_writer",
                    PORTFOLIO_WRITER_PASSWORD,
                    "portfolio_position_observations",
                    "symbol = symbol",
                ),
                arrayOf(
                    "decision_risk_writer",
                    RISK_WRITER_PASSWORD,
                    "deterministic_risk_observations",
                    "observation_id = observation_id",
                ),
                arrayOf(
                    "decision_risk_writer",
                    RISK_WRITER_PASSWORD,
                    "daily_order_count_observations",
                    "observation_id = observation_id",
                ),
                arrayOf(
                    "decision_collector",
                    COLLECTOR_PASSWORD,
                    "corporation_registry_observations",
                    "observation_id = observation_id",
                ),
            )
        writerOwnedTables.forEach { (role, password, ownedTable, noOpAssignment) ->
            assertTrue(hasTablePrivilege(role, ownedTable, "INSERT"), "missing writer INSERT on $ownedTable")
            listOf("SELECT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(
                    hasTablePrivilege(role, ownedTable, privilege),
                    "unexpected writer $privilege on $ownedTable",
                )
            }
            assertRolePermissionDenied(role, password, "select * from $ownedTable")
            assertRolePermissionDenied(role, password, "update $ownedTable set $noOpAssignment")
            assertRolePermissionDenied(role, password, "delete from $ownedTable")
            assertRolePermissionDenied(role, password, "truncate table $ownedTable")
        }
        writerOwnedTables
            .map { it[0] to it[1] }
            .distinct()
            .forEach { (role, password) ->
                assertRolePermissionDenied(role, password, "insert into decisions (decision_id) values ('forbidden')")
                assertRolePermissionDenied(role, password, "create table forbidden_${role}_ddl (id integer)")
            }
    }

    @Test
    fun `stored quote and KIS mock balance are owner scoped exact observations outside the persistence transaction`() {
        val observedAt = "2031-02-03T04:05:06Z"
        jdbcTemplate.update(
            """
            insert into market_quote_observations (
              observation_id, symbol, source, price_krw, bid_krw, ask_krw,
              observed_at, received_at,
              completeness, schema_version, source_version, payload_json, source_ref, artifact_hash
            )
            values (
              'obs-quote-s23', '005930', 'KIS_MOCK', 70000, 69900, 70000,
              ?::timestamptz, ?::timestamptz,
              'COMPLETE', 'market-quote-observation.v1', 'kis-mock-fixture-v1',
              '{"symbol":"005930"}'::jsonb, repeat('a', 64), repeat('b', 64)
            )
            """.trimIndent(),
            observedAt,
            observedAt,
        )
        jdbcTemplate.update(
            """
            insert into portfolio_balance_observations (
              observation_id, owner_user_id, account_scope_hash, source, context_status,
              cash_krw, portfolio_equity_krw, margin_requirement_krw, completeness, position_count,
              observed_at, received_at, schema_version, source_version, payload_json,
              source_ref, artifact_hash
            )
            values (
              'obs-balance-s23', 'usr_demo_user', repeat('c', 64), 'KIS_MOCK', 'ACTIVE',
              500000, 1000000, 140000, 'COMPLETE', 1,
              ?::timestamptz, ?::timestamptz,
              'portfolio-balance-observation.v1', 'kis-mock-fixture-v1',
              '{"ownerScopeHash":"sanitized"}'::jsonb,
              repeat('d', 64), repeat('e', 64)
            )
            """.trimIndent(),
            observedAt,
            observedAt,
        )
        jdbcTemplate.update(
            """
            insert into portfolio_position_observations (
              balance_observation_id, symbol, quantity, market_value_krw, is_gold_etf_etn
            )
            values ('obs-balance-s23', '005930', 10, 700000, false)
            """.trimIndent(),
        )

        assertFalse(TransactionSynchronizationManager.isActualTransactionActive())
        val context =
            portfolioContextAdapter.resolve("usr_demo_user", PortfolioSource.KIS_MOCK)
                as PortfolioContextResolution.Available
        val sourceRequest =
            EvaluationSourceRequest(
                actorUserId = "usr_demo_user",
                portfolioContext = context.context,
                orderIntent =
                    OrderIntentSnapshot(
                        symbol = "005930",
                        side = "BUY",
                        orderType = "MARKET",
                        quantity = 2,
                        estimatedPrice = 70000,
                        estimatedAmount = 140000,
                        timeframe = "1d",
                        strategyId = "stored-source-test",
                    ),
                evaluationAsOf = java.time.Instant.parse(observedAt),
            )

        val price = marketQuoteAdapter.load(sourceRequest) as MetricCell.Available
        val balance = kisMockBalanceAdapter.load(sourceRequest) as MetricCell.Available
        val margin = storedMarginAdapter.load(sourceRequest) as MetricCell.Available
        assertEquals(70000L, (price.value as MetricValue.Whole).value)
        assertEquals(1000000L, balance.value.portfolioEquityKrw)
        assertEquals(listOf("005930"), balance.value.positions.map { it.symbol })
        assertEquals(140000L, (margin.value as MetricValue.Whole).value)
        assertEquals(java.time.Instant.parse("2031-02-03T04:10:06Z"), price.freshUntil)
        assertEquals(java.time.Instant.parse("2031-02-03T04:06:06Z"), balance.freshUntil)
        assertFalse(TransactionSynchronizationManager.isActualTransactionActive())

        assertTrue(
            portfolioContextAdapter.resolve("usr_missing_context", PortfolioSource.KIS_MOCK)
                is PortfolioContextResolution.Unavailable,
        )
    }

    @Test
    fun `internal paper never synthesizes margin zero or position classification`() {
        jdbcTemplate.update(
            """
            insert into paper_accounts (
              account_id, user_id, name, cash_balance, currency, status, created_at, updated_at
            )
            values (
              'paper-s23', 'usr_demo_admin', 'Paper S2.3', 900000, 'KRW', 'ACTIVE',
              '2031-02-03T04:00:00Z', '2031-02-03T04:05:06Z'
            )
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into paper_positions (
              position_id, account_id, symbol, quantity, average_price, market_value, updated_at
            )
            values (
              'position-s23', 'paper-s23', '999999', 1, 100000, 100000, '2031-02-03T04:05:06Z'
            )
            """.trimIndent(),
        )
        val resolution =
            portfolioContextAdapter.resolve("usr_demo_admin", PortfolioSource.INTERNAL_PAPER)
                as PortfolioContextResolution.Available
        val request =
            EvaluationSourceRequest(
                actorUserId = "usr_demo_admin",
                portfolioContext = resolution.context,
                orderIntent =
                    OrderIntentSnapshot(
                        symbol = "005930",
                        side = "BUY",
                        orderType = "LIMIT",
                        quantity = 1,
                        estimatedPrice = 70000,
                        estimatedAmount = 70000,
                        timeframe = "1d",
                        strategyId = "paper-source-test",
                    ),
                evaluationAsOf = java.time.Instant.parse("2031-02-03T04:05:06Z"),
            )

        assertTrue(internalPaperBalanceAdapter.load(request) is MetricCell.Incomplete)
        assertTrue(storedMarginAdapter.load(request) is MetricCell.Missing)
    }

    @Test
    fun `approved stored instrument risk and daily order sources are bounded production ports`() {
        val evaluationAsOf = Instant.parse("2026-06-24T03:00:00Z")
        jdbcTemplate.update(
            """
            insert into instrument_catalog_observations (
              observation_id, symbol, is_etf_etn, is_gold_etf_etn, product_risk_score,
              catalog_version, observed_at, received_at, completeness, schema_version,
              source_version, payload_json, source_ref, artifact_hash
            ) values (
              'ins-s23-v1', '005930', false, false, null, 'catalog-v1',
              '2026-06-24T01:00:00Z', '2026-06-24T01:00:01Z', 'COMPLETE',
              'instrument-catalog-observation.v1', 'sanitized-fixture-v1',
              '{"symbol":"005930","catalogVersion":"catalog-v1"}'::jsonb,
              repeat('1', 64), repeat('2', 64)
            ), (
              'ins-s23-v2', '005930', true, false, 0.35, 'catalog-v2',
              '2026-06-24T02:00:00Z', '2026-06-24T02:00:01Z', 'COMPLETE',
              'instrument-catalog-observation.v1', 'sanitized-fixture-v2',
              '{"symbol":"005930","catalogVersion":"catalog-v2"}'::jsonb,
              repeat('3', 64), repeat('4', 64)
            )
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into deterministic_risk_observations (
              observation_id, owner_user_id, owner_scope_hash, portfolio_source,
              daily_loss_rate, max_drawdown, annualized_volatility, completeness,
              observed_at, received_at, schema_version, source_version, payload_json,
              source_ref, artifact_hash
            ) values (
              'risk-s23-read', 'usr_demo_user', repeat('c', 64), 'KIS_MOCK',
              -0.0125, -0.0800, 0.2200, 'COMPLETE',
              '2026-06-23T06:31:00Z', '2026-06-23T06:31:01Z',
              'deterministic-risk-observation.v1', 'risk-fixture-v1',
              '{"ownerScopeHash":"sanitized"}'::jsonb, repeat('5', 64), repeat('6', 64)
            )
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into daily_order_count_observations (
              observation_id, owner_user_id, owner_scope_hash, portfolio_source,
              trading_date, order_count, covered_through, completeness,
              observed_at, received_at, schema_version, source_version, payload_json,
              source_ref, artifact_hash
            ) values (
              'orders-s23-read', 'usr_demo_user', repeat('c', 64), 'KIS_MOCK',
              '2026-06-24', 0, ?::timestamptz, 'COMPLETE',
              ?::timestamptz, ?::timestamptz,
              'daily-order-count-observation.v1', 'order-ledger-fixture-v1',
              '{"ownerScopeHash":"sanitized","orderCount":0}'::jsonb,
              repeat('7', 64), repeat('8', 64)
            )
            """.trimIndent(),
            java.time.OffsetDateTime.ofInstant(evaluationAsOf, java.time.ZoneOffset.UTC),
            java.time.OffsetDateTime.ofInstant(evaluationAsOf, java.time.ZoneOffset.UTC),
            java.time.OffsetDateTime.ofInstant(evaluationAsOf, java.time.ZoneOffset.UTC),
        )
        val request =
            EvaluationSourceRequest(
                actorUserId = "usr_demo_user",
                portfolioContext =
                    com.capstone.decision.application.risk.port.PortfolioContextRef(
                        opaqueRef = "c".repeat(64),
                        source = PortfolioSource.KIS_MOCK,
                        ownerScopeHash = "c".repeat(64),
                    ),
                orderIntent =
                    OrderIntentSnapshot(
                        symbol = "005930",
                        side = "BUY",
                        orderType = "MARKET",
                        quantity = 1,
                        estimatedPrice = 70000,
                        estimatedAmount = 70000,
                        timeframe = "1d",
                        strategyId = "stored-hard-source-test",
                    ),
                evaluationAsOf = evaluationAsOf,
            )

        val instrument = instrumentCatalogPort.load(request) as MetricCell.Available
        assertEquals("catalog-v2", instrument.value.catalogVersion)
        assertEquals(MetricSource.INSTRUMENT_CATALOG, instrument.source)
        assertEquals(
            "0.35",
            instrument.value.productRiskScore
                ?.stripTrailingZeros()
                ?.toPlainString(),
        )

        val risk = riskSnapshotPort.load(request)
        assertEquals("-0.0125", metricDecimal(risk.dailyLossRate))
        assertEquals("-0.08", metricDecimal(risk.maxDrawdown))
        assertEquals("0.22", metricDecimal(risk.annualizedVolatility))

        val orderCount = orderMetricPort.loadDailyOrderCount(request) as MetricCell.Available
        assertEquals(0L, (orderCount.value as MetricValue.Whole).value)
    }

    @Test
    fun `partial and inactive observations never become complete current source values`() {
        jdbcTemplate.update(
            """
            insert into market_quote_observations (
              observation_id, symbol, source, price_krw, completeness,
              observed_at, received_at, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) values (
              'quote-partial-s23', '000660', 'KIS_MOCK', 180000, 'PARTIAL',
              '2026-06-24T02:59:00Z', '2026-06-24T02:59:01Z',
              'market-quote-observation.v1', 'partial-fixture-v1',
              '{"symbol":"000660"}'::jsonb, repeat('1', 64), repeat('2', 64)
            )
            """.trimIndent(),
        )
        val sourceRequest =
            EvaluationSourceRequest(
                actorUserId = "usr_demo_user",
                portfolioContext =
                    com.capstone.decision.application.risk.port.PortfolioContextRef(
                        opaqueRef = "c".repeat(64),
                        source = PortfolioSource.KIS_MOCK,
                        ownerScopeHash = "c".repeat(64),
                    ),
                orderIntent =
                    OrderIntentSnapshot(
                        symbol = "000660",
                        side = "BUY",
                        orderType = "MARKET",
                        quantity = 1,
                        estimatedPrice = 180000,
                        estimatedAmount = 180000,
                        timeframe = "1d",
                        strategyId = "partial-source-test",
                    ),
                evaluationAsOf = Instant.parse("2026-06-24T03:00:00Z"),
            )
        assertTrue(marketQuoteAdapter.load(sourceRequest) is MetricCell.Incomplete)

        assertCheckViolation {
            jdbcTemplate.update(
                """
                insert into deterministic_risk_observations (
                  observation_id, owner_user_id, owner_scope_hash, portfolio_source,
                  daily_loss_rate, completeness, observed_at, received_at,
                  schema_version, source_version, payload_json, source_ref, artifact_hash
                ) values (
                  'risk-partial-invalid', 'usr_demo_user', repeat('d', 64), 'KIS_MOCK',
                  -2, 'PARTIAL', now(), now(), 'risk-v1', 'fixture-v1', '{}'::jsonb,
                  repeat('3', 64), repeat('4', 64)
                )
                """.trimIndent(),
            )
        }
        assertCheckViolation {
            jdbcTemplate.update(
                """
                insert into daily_order_count_observations (
                  observation_id, owner_user_id, owner_scope_hash, portfolio_source,
                  trading_date, order_count, covered_through, completeness,
                  observed_at, received_at, schema_version, source_version,
                  payload_json, source_ref, artifact_hash
                ) values (
                  'orders-partial-invalid', 'usr_demo_user', repeat('d', 64), 'KIS_MOCK',
                  current_date, -1, now(), 'PARTIAL', now(), now(),
                  'orders-v1', 'fixture-v1', '{}'::jsonb, repeat('5', 64), repeat('6', 64)
                )
                """.trimIndent(),
            )
        }

        jdbcTemplate.update(
            """
            insert into portfolio_balance_observations (
              observation_id, owner_user_id, account_scope_hash, source, context_status,
              cash_krw, portfolio_equity_krw, margin_requirement_krw, completeness,
              position_count, observed_at, received_at, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) values
              (
                'balance-active-old', 'usr_demo_user', repeat('e', 64), 'KIS_MOCK', 'ACTIVE',
                1, 1, 0, 'COMPLETE', 0, '2026-06-24T01:00:00Z', '2026-06-24T01:00:01Z',
                'balance-v1', 'fixture-v1', '{}'::jsonb, repeat('7', 64), repeat('8', 64)
              ),
              (
                'balance-inactive-new', 'usr_demo_user', repeat('e', 64), 'KIS_MOCK', 'INACTIVE',
                1, 1, 0, 'COMPLETE', 0, '2026-06-24T02:00:00Z', '2026-06-24T02:00:01Z',
                'balance-v1', 'fixture-v1', '{}'::jsonb, repeat('9', 64), repeat('a', 64)
              )
            """.trimIndent(),
        )
        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { connection ->
            connection.autoCommit = false
            connection
                .prepareStatement("select set_config('app.actor_user_id', ?, true)")
                .use { statement ->
                    statement.setString(1, "usr_demo_user")
                    statement.executeQuery().close()
                }
            connection
                .prepareStatement(
                    "select count(*) from latest_portfolio_balance_observations where account_scope_hash = ?",
                ).use { statement ->
                    statement.setString(1, "e".repeat(64))
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        assertEquals(0, result.getInt(1))
                    }
                }
            connection.rollback()
        }
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

    @Test
    fun `Decision child rows reject cross wired decision and evaluation identities`() {
        insertOrderFixture()
        insertSecondDecisionFixture()

        assertForeignKeyViolation {
            jdbcTemplate.update(
                """
                insert into decision_artifacts (
                  decision_id, evaluation_id, result_canonical_json,
                  snapshot_artifact_canonical_json, semantic_input_hash,
                  snapshot_artifact_hash, created_at
                ) values (
                  'dec-flyway', 'eval-flyway-b', '{}', '{}',
                  repeat('a', 64), repeat('b', 64), now()
                )
                """.trimIndent(),
            )
        }
        assertForeignKeyViolation {
            jdbcTemplate.update(
                """
                insert into decision_violations (
                  violation_id, decision_id, evaluation_id, ordinal, rule_id,
                  severity, message, created_at
                ) values (
                  'vio-cross-wire', 'dec-flyway', 'eval-flyway-b', 1,
                  'cross-wire-guard', 'INFO', 'sanitized fixture', now()
                )
                """.trimIndent(),
            )
        }
        assertForeignKeyViolation {
            jdbcTemplate.update(
                """
                insert into decision_traces (
                  trace_id, decision_id, evaluation_id, step, trace_type,
                  trace_json, created_at
                ) values (
                  'trc-cross-wire', 'dec-flyway', 'eval-flyway-b', 1,
                  'ORDER_VALIDATED', '{}'::jsonb, now()
                )
                """.trimIndent(),
            )
        }
        assertForeignKeyViolation {
            jdbcTemplate.update(
                """
                with clock as (select now() as created_at)
                insert into decision_idempotency_results (
                  idempotency_result_id, scope_hash, generation, request_hash,
                  owner_scope_hash, purpose_version, decision_id, evaluation_id,
                  http_status, content_type, result_canonical_json, created_at, expires_at
                )
                select
                  'idr-cross-wire', repeat('1', 64), 1, repeat('2', 64),
                  repeat('3', 64), 's2.3-idempotency-v1',
                  'dec-flyway', 'eval-flyway-b', 200, 'application/json',
                  '{}', created_at, created_at + interval '24 hours'
                from clock
                """.trimIndent(),
            )
        }
    }

    @Test
    fun `Decision audit target must equal its sanitized payload identity`() {
        insertOrderFixture()
        insertSecondDecisionFixture()

        assertCheckViolation {
            jdbcTemplate.update(
                """
                insert into audit_logs (
                  audit_log_id, user_id, actor_role, action, target_type,
                  target_id, request_id, payload_json, created_at
                ) values (
                  'aud-cross-wire', 'usr-flyway', 'USER', 'DECISION_EVALUATED',
                  'DECISION', 'dec-flyway', 'req-cross-wire',
                  jsonb_build_object(
                    'evaluationId', 'eval-flyway-b',
                    'decisionId', 'dec-flyway-b',
                    'outcome', 'ALLOW',
                    'principleVersionId', 'prv-flyway-v1',
                    'semanticInputHash', repeat('a', 64),
                    'snapshotArtifactHash', repeat('b', 64)
                  ),
                  now()
                )
                """.trimIndent(),
            )
        }
    }

    @Test
    fun `Decision validity constraint rejects an already expired persisted result`() {
        insertOrderFixture()

        assertCheckViolation {
            jdbcTemplate.update(
                "update decisions set valid_until = created_at where decision_id = 'dec-flyway'",
            )
        }
    }

    @Test
    fun `instrument latest projection resolves equal times by observation id only`() {
        jdbcTemplate.update(
            """
            insert into instrument_catalog_observations (
              observation_id, symbol, is_etf_etn, is_gold_etf_etn,
              product_risk_score, catalog_version, observed_at, received_at,
              completeness, schema_version, source_version, payload_json,
              source_ref, artifact_hash
            ) values
              (
                'ins-tie-a', 'ZZTIE', false, false, null, 'catalog-a',
                '2030-01-01T00:00:00Z', '2030-01-01T00:00:01Z',
                'COMPLETE', 'instrument-catalog-observation.v1', 'fixture-v1',
                '{}'::jsonb, repeat('1', 64), repeat('2', 64)
              ),
              (
                'ins-tie-b', 'ZZTIE', true, false, 0.25, 'catalog-z',
                '2030-01-01T00:00:00Z', '2030-01-01T00:00:01Z',
                'COMPLETE', 'instrument-catalog-observation.v1', 'fixture-v1',
                '{}'::jsonb, repeat('3', 64), repeat('4', 64)
              )
            """.trimIndent(),
        )

        assertEquals(
            "ins-tie-a",
            jdbcTemplate.queryForObject(
                "select observation_id from latest_instrument_catalog_observations where symbol = 'ZZTIE'",
                String::class.java,
            ),
        )
    }

    @Test
    fun `latest observation indexes match their exact projection partition order`() {
        val instrumentIndex =
            jdbcTemplate.queryForObject(
                "select pg_get_indexdef(indexrelid) from pg_index where indexrelid = 'instrument_catalog_latest_idx'::regclass",
                String::class.java,
            )
        val portfolioIndex =
            jdbcTemplate.queryForObject(
                "select pg_get_indexdef(indexrelid) from pg_index where indexrelid = 'portfolio_balance_latest_idx'::regclass",
                String::class.java,
            )

        assertTrue(
            requireNotNull(instrumentIndex).contains(
                "(symbol, observed_at DESC, received_at DESC, observation_id)",
            ),
        )
        assertTrue(
            requireNotNull(portfolioIndex).contains(
                "(owner_user_id, account_scope_hash, observed_at DESC, received_at DESC, observation_id)",
            ),
        )
    }

    @Test
    fun `Decision owner projections bind the requested id inside the definer function`() {
        listOf(
            "read_decision_owner_projection()",
            "read_decision_audit_projection()",
        ).forEach { functionName ->
            val definition =
                jdbcTemplate.queryForObject(
                    "select pg_get_functiondef(?::regprocedure)",
                    String::class.java,
                    functionName,
                )
            assertTrue(requireNotNull(definition).contains("app.requested_decision_id"))
        }
        assertTrue(
            indexExists("decision_audit_projection_target_idx"),
            "missing bounded Decision audit lookup index",
        )
    }

    private fun insertOrderFixture() {
        jdbcTemplate.update("delete from orders where decision_id in ('dec-flyway', 'dec-flyway-b')")
        jdbcTemplate.update(
            "delete from decision_idempotency_results where decision_id in ('dec-flyway', 'dec-flyway-b')",
        )
        jdbcTemplate.update("delete from decision_traces where decision_id in ('dec-flyway', 'dec-flyway-b')")
        jdbcTemplate.update("delete from decision_artifacts where decision_id in ('dec-flyway', 'dec-flyway-b')")
        jdbcTemplate.update("delete from decision_violations where decision_id in ('dec-flyway', 'dec-flyway-b')")
        jdbcTemplate.update("delete from audit_logs where target_id in ('dec-flyway', 'dec-flyway-b')")
        jdbcTemplate.update("delete from decisions where decision_id in ('dec-flyway', 'dec-flyway-b')")
        jdbcTemplate.update("delete from principle_versions where principle_id = 'prn-flyway'")
        jdbcTemplate.update("delete from principles where principle_id = 'prn-flyway'")
        jdbcTemplate.update("delete from users where user_id = 'usr-flyway'")
        jdbcTemplate.update(
            """
            insert into users (user_id, username, role, password_hash)
            values ('usr-flyway', 'flyway-user', 'USER', 'test-password-hash')
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into principles (
                principle_id, user_id, preset_id, title, mode, status, current_version
            )
            values (
                'prn-flyway', 'usr-flyway', 'balanced', 'Flyway Principle', 'GUIDE', 'ACTIVE', 1
            )
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into principle_versions (
                principle_version_id, principle_id, version, preset_id, title,
                mode, status, rules_json, changed_fields, created_by
            )
            select
                'prv-flyway-v1', 'prn-flyway', 1, 'balanced', 'Flyway Principle',
                'GUIDE', 'ACTIVE', rules_json,
                array['presetId', 'title', 'mode', 'status', 'rules'], 'usr-flyway'
            from principle_presets
            where preset_id = 'balanced'
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into decisions (
                decision_id, evaluation_id, user_id, principle_id, principle_version_id,
                principle_version, portfolio_source, symbol, side, outcome, mode,
                can_submit_order, enforcement_action, evaluation_as_of, created_at, valid_until,
                result_schema_version, snapshot_schema_version, catalog_version,
                readiness_policy_version, mapping_versions_json, semantic_input_hash,
                snapshot_artifact_hash, result_json
            )
            values (
                'dec-flyway', 'eval-flyway', 'usr-flyway', 'prn-flyway', 'prv-flyway-v1',
                1, 'INTERNAL_PAPER', '005930', 'BUY', 'ALLOW', 'GUIDE',
                true, 'NONE', now(), now(), now() + interval '10 minutes',
                'risk-decision.v1', 's2.2-metric-snapshot-v2', 1,
                's2.3-readiness-v1', '{}'::jsonb, repeat('a', 64),
                repeat('b', 64), '{}'::jsonb
            )
            """.trimIndent(),
        )
    }

    private fun insertSecondDecisionFixture() {
        jdbcTemplate.update(
            """
            insert into decisions (
              decision_id, evaluation_id, user_id, principle_id, principle_version_id,
              principle_version, portfolio_source, symbol, side, outcome, mode,
              can_submit_order, enforcement_action, evaluation_as_of, created_at, valid_until,
              result_schema_version, snapshot_schema_version, catalog_version,
              readiness_policy_version, mapping_versions_json, semantic_input_hash,
              snapshot_artifact_hash, result_json
            )
            select
              'dec-flyway-b', 'eval-flyway-b', user_id, principle_id, principle_version_id,
              principle_version, portfolio_source, symbol, side, outcome, mode,
              can_submit_order, enforcement_action, evaluation_as_of, created_at, valid_until,
              result_schema_version, snapshot_schema_version, catalog_version,
              readiness_policy_version, mapping_versions_json, repeat('c', 64),
              repeat('d', 64), result_json
            from decisions
            where decision_id = 'dec-flyway'
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

    private fun hasFunctionPrivilege(
        role: String,
        functionSignature: String,
    ): Boolean =
        jdbcTemplate.queryForObject(
            "select has_function_privilege(?, ?, 'EXECUTE')",
            Boolean::class.java,
            role,
            functionSignature,
        ) ?: false

    private fun assertDecisionAppPermissionDenied(sql: String) {
        assertRolePermissionDenied("decision_app", APP_PASSWORD, sql)
    }

    private fun assertRolePermissionDenied(
        role: String,
        password: String,
        sql: String,
    ) {
        DriverManager.getConnection(postgres.jdbcUrl, role, password).use { connection ->
            connection.createStatement().use { statement ->
                val exception = assertThrows<SQLException> { statement.execute(sql) }
                assertEquals("42501", exception.sqlState, "expected permission denial for: $sql")
            }
        }
    }

    private fun assertWriterInsert(
        role: String,
        password: String,
        sql: String,
    ) {
        DriverManager.getConnection(postgres.jdbcUrl, role, password).use { connection ->
            connection.createStatement().use { statement ->
                assertEquals(1, statement.executeUpdate(sql), "writer $role failed its exact INSERT")
            }
        }
    }

    private fun metricDecimal(cell: MetricCell<MetricValue>): String {
        val available = cell as MetricCell.Available
        return available.value
            .asBigDecimal()
            .stripTrailingZeros()
            .toPlainString()
    }

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

    private fun assertCheckViolation(block: () -> Unit) {
        val exception =
            org.junit.jupiter.api.assertThrows<DataIntegrityViolationException> {
                block()
            }
        val sqlException = exception.findSqlException()
        assertTrue(
            sqlException?.sqlState == "23514",
            "expected SQLState 23514 but was ${sqlException?.sqlState}: ${exception.mostSpecificCause.message}",
        )
    }

    private fun assertForeignKeyViolation(block: () -> Unit) {
        val exception =
            org.junit.jupiter.api.assertThrows<DataIntegrityViolationException> {
                block()
            }
        val sqlException = exception.findSqlException()
        assertTrue(
            sqlException?.sqlState == "23503",
            "expected SQLState 23503 but was ${sqlException?.sqlState}: ${exception.mostSpecificCause.message}",
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
        private const val APP_PASSWORD = "app-test"
        private const val COLLECTOR_PASSWORD = "collector-test"
        private const val MARKET_WRITER_PASSWORD = "market-writer-test"
        private const val PORTFOLIO_WRITER_PASSWORD = "portfolio-writer-test"
        private const val RISK_WRITER_PASSWORD = "risk-writer-test"
        private const val FLYWAY_PASSWORD = "flyway-test"
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
