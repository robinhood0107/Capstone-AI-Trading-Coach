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
    fun `clean database applies V1 through V16 migrations and creates required objects`() {
        val versions = queryStrings("select version from flyway_schema_history where success order by installed_rank")
        assertEquals((1..16).map(Int::toString), versions)

        val requiredTables =
            listOf(
                "users",
                "principles",
                "principle_versions",
                "decisions",
                "orders",
                "order_events",
                "order_fill_observations",
                "order_fill_application_receipts",
                "processed_event",
                "artifact_ingest_state",
                "rag_sources",
                "rag_source_revisions",
                "rag_source_checks",
                "rag_ingest_runs",
                "rag_chunk_revisions",
                "rag_corpus_generations",
                "rag_generation_chunks",
                "rag_chunk_embeddings",
                "rag_embedding_policy_state",
                "rag_embedding_policy_transitions",
                "rag_sources_v2_legacy",
                "rag_chunks_v2_legacy",
                "rag_answers_v2_legacy",
                "rag_citations_v2_legacy",
                "rag_answer_feedback_v2_legacy",
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
                "risk_kill_switch",
                "risk_kill_switch_transitions",
                "decision_invalidations",
                "brokerage_db_capability_keys",
                "mock_order_owner_projection",
                "kill_switch_user_projection",
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
        assertTrue(indexExists("rag_chunk_revisions_trgm_idx"), "expected pg_trgm index for Korean keyword search")
        assertFalse(
            indexDefinitionLike("rag_chunk_embeddings", "%ivfflat%"),
            "ivfflat must wait until real embeddings are loaded",
        )
    }

    @Test
    fun `V10 seeds one safe singleton and exposes only the sanitized user projection`() {
        val state =
            jdbcTemplate.queryForMap(
                """
                select active, reason_class, generation, changed_by, changed_by_role, request_id
                from risk_kill_switch
                """.trimIndent(),
            )
        assertEquals(false, state["active"])
        assertEquals("INITIAL_STATE", state["reason_class"])
        assertEquals(1L, state["generation"])
        assertEquals(null, state["changed_by"])
        assertEquals("SYSTEM", state["changed_by_role"])
        assertEquals(null, state["request_id"])
        assertEquals(
            listOf("active", "reason_class", "changed_at"),
            queryStrings(
                """
                select column_name
                from information_schema.columns
                where table_schema = 'public' and table_name = 'kill_switch_user_projection'
                order by ordinal_position
                """.trimIndent(),
            ),
        )
    }

    @Test
    fun `V10 indexes the global unused decision invalidation scan`() {
        assertTrue(indexExists("decisions_valid_until_invalidation_idx"))
        assertTrue(
            indexDefinitionLike(
                "decisions",
                "%(valid_until, decision_id)%",
            ),
        )
    }

    @Test
    fun `decision application role receives only V15 brokerage capabilities`() {
        assertTrue(hasTablePrivilege("decision_app", "risk_kill_switch", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "risk_kill_switch", "INSERT"))
        assertFalse(hasTablePrivilege("decision_app", "risk_kill_switch", "DELETE"))
        assertFalse(hasTablePrivilege("decision_app", "risk_kill_switch", "TRUNCATE"))
        assertTrue(hasTablePrivilege("decision_app", "risk_kill_switch_transitions", "INSERT"))
        assertTrue(hasTablePrivilege("decision_app", "kill_switch_user_projection", "SELECT"))
        listOf("orders", "order_events", "mock_order_owner_projection", "brokerage_db_capability_keys").forEach { table ->
            listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(hasTablePrivilege("decision_app", table, privilege), "unexpected $privilege on $table")
            }
        }
        listOf("paper_accounts", "paper_positions", "paper_order_events").forEach { table ->
            listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(hasTablePrivilege("decision_app", table, privilege), "unexpected $privilege on $table")
            }
        }
        assertTrue(hasTablePrivilege("decision_app", "paper_margin_owner_projection", "SELECT"))
        listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
            assertFalse(
                hasTablePrivilege("decision_app", "decision_invalidations", privilege),
                "unexpected $privilege on decision_invalidations",
            )
        }
        listOf("UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
            assertFalse(hasTablePrivilege("decision_app", "orders", privilege), "unexpected $privilege on orders")
            assertFalse(hasTablePrivilege("decision_app", "order_events", privilege), "unexpected $privilege on order_events")
        }
        listOf("SELECT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
            assertFalse(
                hasTablePrivilege("decision_app", "risk_kill_switch_transitions", privilege),
                "unexpected $privilege on risk_kill_switch_transitions",
            )
        }
        listOf(
            "read_kill_switch_gate()",
            "revalidate_kill_switch_admin(text,bigint)",
            "read_kill_switch_audit_projection()",
            "read_decision_usability()",
            "invalidate_unused_decisions_for_kill_switch(bigint,timestamp with time zone,text)",
            "read_mock_order_decision(text,text,text)",
            "find_mock_order_idempotency_result(text,text,timestamp with time zone,text)",
            "read_mock_order_owner_projection(text,text,text)",
            "create_mock_order(jsonb,text)",
            "request_mock_order_cancel(jsonb,text)",
            "record_mock_order_provider_outcome(jsonb,text)",
            "read_paper_order_context(text,text,text)",
            "find_paper_order_idempotency_result(text,text,timestamp with time zone,text)",
            "read_paper_balance_projection(text,text,text)",
            "create_paper_order(jsonb,text)",
        ).forEach { function ->
            assertTrue(hasFunctionPrivilege("decision_app", function), "missing EXECUTE on $function")
        }
        assertFalse(
            hasFunctionPrivilege("decision_app", "assert_brokerage_database_capability(text)"),
            "runtime role must not call the private capability verifier directly",
        )
        assertFalse(
            hasFunctionPrivilege("decision_app", "rebuild_paper_state(text,text)"),
            "runtime role must not call the paper ledger rebuild verifier",
        )
        assertDecisionAppPermissionDenied("select * from orders")
        assertDecisionAppPermissionDenied("insert into orders default values")
        assertDecisionAppPermissionDenied("select * from order_events")
        assertDecisionAppPermissionDenied("insert into order_events default values")
        assertDecisionAppPermissionDenied("select * from mock_order_owner_projection")
        assertDecisionAppPermissionDenied("select * from paper_accounts")
        assertDecisionAppPermissionDenied("insert into paper_order_events default values")
        assertDecisionAppPermissionDenied("update paper_order_events set event_seq = event_seq")
        assertDecisionAppPermissionDenied("delete from paper_order_events")
        assertDecisionAppPermissionDenied("truncate table paper_order_events")
        assertDecisionAppPermissionDenied(
            "select * from read_paper_order_context('usr_demo_user', 'dec_${"0".repeat(32)}', '${"0".repeat(64)}')",
        )
    }

    @Test
    fun `V13 precondition rejects a preexisting paper ledger row and rolls back`() {
        val migrationUrl = createDatabase("v13_precondition_paper_row")
        flyway(migrationUrl, target = "12").migrate()
        DriverManager.getConnection(migrationUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.execute(
                    """
                    insert into paper_accounts (
                      account_id, user_id, name, cash_balance, currency, status
                    ) values (
                      'acct_00000000000000000000000000000013',
                      'usr_demo_user', 'V13 precondition fixture', 1, 'KRW', 'ACTIVE'
                    )
                    """.trimIndent(),
                )
            }
        }

        val failure = assertThrows<FlywayException> { flyway(migrationUrl).migrate() }

        assertTrue(failure.stackTraceToString().contains("S3.2 V13 precondition failed"))
        DriverManager.getConnection(migrationUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement
                    .executeQuery("select count(*) from flyway_schema_history where version = '13'")
                    .use { result ->
                        assertTrue(result.next())
                        assertEquals(0, result.getInt(1))
                    }
                statement
                    .executeQuery("select count(*) from paper_accounts")
                    .use { result ->
                        assertTrue(result.next())
                        assertEquals(1, result.getInt(1))
                    }
            }
        }
    }

    @Test
    fun `V13 mode prefix와 paper ledger checks reject cross wired and mutable rows`() {
        insertOrderFixture()
        jdbcTemplate.update(
            """
            insert into orders (
              order_id, user_id, account_id, account_scope_hash, decision_id,
              decision_evaluation_id, brokerage_mode, idempotency_scope_hash,
              idempotency_owner_scope_hash, request_hash, symbol, side, order_type,
              quantity, submitted_price_krw, status, order_intent_json,
              result_canonical_json, acknowledged_by, acknowledged_at, submitted_at
            ) values (
              'ord_mock_00000000000000000000000000000013', 'usr-flyway',
              'acct_00000000000000000000000000000013', repeat('1', 64),
              'dec-flyway', 'eval-flyway', 'KIS_MOCK', repeat('2', 64),
              repeat('3', 64), repeat('4', 64), '005930', 'BUY', 'MARKET',
              1, null, 'SUBMITTED',
              '{"symbol":"005930","side":"BUY","orderType":"MARKET","quantity":"1","estimatedPrice":"10000","estimatedAmount":"10000","timeframe":"1d","strategyId":"v13-check"}'::jsonb,
              '{"orderId":"ord_mock_00000000000000000000000000000013","accountId":"acct_00000000000000000000000000000013","brokerageMode":"KIS_MOCK","status":"SUBMITTED","submittedAt":"2030-01-02T03:04:05Z"}',
              'usr-flyway', now(), now()
            )
            """.trimIndent(),
        )
        assertCheckViolation {
            jdbcTemplate.update(
                "update orders set brokerage_mode = 'INTERNAL_PAPER' where decision_id = 'dec-flyway'",
            )
        }
        assertCheckViolation {
            jdbcTemplate.update(
                "update orders set order_id = 'ord_paper_00000000000000000000000000000013' where decision_id = 'dec-flyway'",
            )
        }
        assertCheckViolation {
            jdbcTemplate.update("update orders set brokerage_mode = 'KIS_LIVE' where decision_id = 'dec-flyway'")
        }
        assertCheckViolation {
            jdbcTemplate.update(
                "update orders set order_id = 'ord_mock_A0000000000000000000000000000013' where decision_id = 'dec-flyway'",
            )
        }

        jdbcTemplate.update(
            """
            insert into paper_accounts (
              account_id, user_id, name, cash_balance, currency, status,
              owner_scope_hash, margin_requirement_krw
            ) values (
              'acct_00000000000000000000000000000013', 'usr-flyway',
              'V13 ledger fixture', 100000, 'KRW', 'ACTIVE', repeat('5', 64), 0
            )
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into paper_positions (
              position_id, account_id, symbol, quantity, average_price, market_value
            ) values (
              'ppos_00000000000000000000000000000013',
              'acct_00000000000000000000000000000013',
              '005930', 1, 10000, 10000
            )
            """.trimIndent(),
        )
        assertCheckViolation {
            jdbcTemplate.update(
                "update paper_accounts set cash_balance = -1 where account_id = 'acct_00000000000000000000000000000013'",
            )
        }
        assertCheckViolation {
            jdbcTemplate.update(
                "update paper_positions set quantity = -1 where position_id = 'ppos_00000000000000000000000000000013'",
            )
        }

        jdbcTemplate.update(
            """
            update orders
            set order_id = 'ord_paper_00000000000000000000000000000013',
                brokerage_mode = 'INTERNAL_PAPER',
                status = 'FILLED',
                filled_quantity = quantity,
                leaves_quantity = 0,
                unfilled_terminated_quantity = 0,
                average_fill_price_krw = 10000,
                result_canonical_json =
                  '{"orderId":"ord_paper_00000000000000000000000000000013","accountId":"acct_00000000000000000000000000000013","brokerageMode":"INTERNAL_PAPER","status":"FILLED","submittedAt":"2030-01-02T03:04:05Z","fill":null}'
            where decision_id = 'dec-flyway'
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into paper_order_events (
              paper_order_event_id, account_id, order_id, event_type,
              payload_json, event_seq
            ) values (
              'pev_00000000000000000000000000000013',
              'acct_00000000000000000000000000000013',
              'ord_paper_00000000000000000000000000000013',
              'PAPER_ORDER_FILLED',
              jsonb_build_object(
                'orderId', 'ord_paper_00000000000000000000000000000013',
                'symbol', '005930', 'side', 'BUY',
                'fillQuantity', 1, 'fillPriceKrw', 10000, 'fillAmountKrw', 10000,
                'priceBasis', 'LAST_QUOTE', 'slippageBps', 5, 'feeModel', 'NONE_V1',
                'observedAt', '2030-01-02T03:04:05Z',
                'beforeCashKrw', 110000, 'afterCashKrw', 100000,
                'beforeQuantity', 0, 'afterQuantity', 1,
                'beforeAveragePriceKrw', 0, 'afterAveragePriceKrw', 10000,
                'beforeMarketValueKrw', 0, 'afterMarketValueKrw', 10000
              ),
              1
            )
            """.trimIndent(),
        )
        assertCheckViolation {
            jdbcTemplate.update(
                "update paper_order_events set event_seq = 0 where paper_order_event_id = 'pev_00000000000000000000000000000013'",
            )
        }
        assertCheckViolation {
            jdbcTemplate.update(
                """
                update paper_order_events
                set payload_json = payload_json - 'feeModel'
                where paper_order_event_id = 'pev_00000000000000000000000000000013'
                """.trimIndent(),
            )
        }
        assertUniqueViolation {
            jdbcTemplate.update(
                """
                insert into paper_order_events (
                  paper_order_event_id, account_id, order_id, event_type,
                  payload_json, event_seq
                )
                select
                  'pev_00000000000000000000000000000014',
                  account_id, order_id, event_type, payload_json, 2
                from paper_order_events
                where paper_order_event_id = 'pev_00000000000000000000000000000013'
                """.trimIndent(),
            )
        }
    }

    @Test
    fun `V10 precondition rejects a conflicting Kill Switch object without changing V9 state`() {
        val migrationUrl = createDatabase("v10_precondition_conflict")
        flyway(migrationUrl, target = "9").migrate()
        DriverManager.getConnection(migrationUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.execute("create table risk_kill_switch (fixture_id integer primary key)")
            }
        }

        val failure = assertThrows<FlywayException> { flyway(migrationUrl).migrate() }

        assertTrue(requireNotNull(failure.message).contains("V10 precondition failed"))
        DriverManager.getConnection(migrationUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.executeQuery("select count(*) from flyway_schema_history where success").use { result ->
                    assertTrue(result.next())
                    assertEquals(9, result.getInt(1))
                }
                assertTrue(
                    statement
                        .executeQuery("select to_regclass('public.decisions') is not null")
                        .use { result -> result.next() && result.getBoolean(1) },
                )
            }
        }
    }

    @Test
    fun `V12 order events use monotonic sequence and coupled type status constraints`() {
        assertEquals(
            listOf("event_seq"),
            queryStrings(
                """
                select column_name
                from information_schema.columns
                where table_schema = 'public'
                  and table_name = 'order_events'
                  and column_name = 'event_seq'
                  and is_nullable = 'NO'
                """.trimIndent(),
            ),
        )
        assertTrue(indexExists("order_events_order_sequence_unique"))
        val pairConstraint =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    """
                    select pg_get_constraintdef(oid)
                    from pg_constraint
                    where conname = 'order_events_type_status_pair_check'
                    """.trimIndent(),
                    String::class.java,
                ),
            )
        assertTrue(pairConstraint.contains("MOCK_ORDER_SUBMITTED"))
        assertTrue(pairConstraint.contains("SUBMITTED"))
        assertTrue(pairConstraint.contains("MOCK_ORDER_CANCEL_REQUESTED"))
        assertTrue(pairConstraint.contains("CANCEL_REQUESTED"))
        val projectionDefinition =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    "select pg_get_functiondef('read_mock_order_owner_projection(text,text,text)'::regprocedure)",
                    String::class.java,
                ),
            )
        assertTrue(projectionDefinition.contains("event.event_seq DESC"))
        assertFalse(projectionDefinition.contains("current_setting('app.actor_user_id'"))
    }

    @Test
    fun `V12 brokerage evidence contracts pin writer identity status values and live expiry clock`() {
        val auditConstraint =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    """
                    select pg_get_constraintdef(oid)
                    from pg_constraint
                    where conname = 'audit_logs_brokerage_order_contract_check'
                    """.trimIndent(),
                    String::class.java,
                ),
            )
        assertTrue(auditConstraint.contains("brokerageMode"))
        assertTrue(auditConstraint.contains("KIS_MOCK"))
        assertTrue(auditConstraint.contains("SUBMITTED"))
        assertTrue(auditConstraint.contains("CANCEL_REQUESTED"))

        val outboxConstraint =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    """
                    select pg_get_constraintdef(oid)
                    from pg_constraint
                    where conname = 'event_outbox_brokerage_order_contract_check'
                    """.trimIndent(),
                    String::class.java,
                ),
            )
        assertTrue(outboxConstraint.contains("brokerageMode"))
        assertTrue(outboxConstraint.contains("KIS_MOCK"))
        assertTrue(outboxConstraint.contains("SUBMITTED"))
        assertTrue(outboxConstraint.contains("CANCEL_REQUESTED"))

        val guardDefinition =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    "select pg_get_functiondef('enforce_brokerage_evidence_writer()'::regprocedure)",
                    String::class.java,
                ),
            ).lowercase()
        assertTrue(guardDefinition.contains("pg_has_role"))
        assertTrue(guardDefinition.contains("current_user"))
        assertTrue(guardDefinition.contains("flyway"))
        assertTrue(guardDefinition.contains("42501"))

        val triggerDefinitions =
            queryStrings(
                """
                select pg_get_triggerdef(oid)
                from pg_trigger
                where not tgisinternal
                  and tgname in (
                    'audit_logs_brokerage_writer_guard',
                    'event_outbox_brokerage_writer_guard'
                  )
                order by tgname
                """.trimIndent(),
            )
        assertEquals(2, triggerDefinitions.size)
        val triggerText = triggerDefinitions.joinToString("\n").lowercase()
        assertTrue(triggerText.contains("before insert"))
        assertTrue(triggerText.contains("audit_logs"))
        assertTrue(triggerText.contains("target_type = 'order'"))
        assertTrue(triggerText.contains("event_outbox"))
        assertTrue(triggerText.contains("brokerage.mock-order-submitted.v1"))
        assertTrue(triggerText.contains("brokerage.mock-order-cancel-requested.v1"))

        val createOrderDefinition =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    "select pg_get_functiondef('create_mock_order(jsonb,text)'::regprocedure)",
                    String::class.java,
                ),
            )
        assertTrue(createOrderDefinition.contains("clock_timestamp()"))
        assertFalse(createOrderDefinition.contains("valid_until > requested_created_at"))
    }

    @Test
    fun `V10 singleton actor resume and audit constraints reject unsafe rows`() {
        assertCheckViolation {
            jdbcTemplate.update(
                """
                insert into risk_kill_switch (
                  kill_switch_id, active, reason_class, generation,
                  changed_by, changed_by_role, changed_at
                ) values (
                  'OTHER', true, 'USER_MANUAL_STOP', 2,
                  'usr_demo_user', 'USER', now()
                )
                """.trimIndent(),
            )
        }
        assertCheckViolation {
            jdbcTemplate.update(
                """
                update risk_kill_switch
                set active = false,
                    reason_class = 'ADMIN_RESUME',
                    changed_by = 'usr_demo_user',
                    changed_by_role = 'USER'
                where kill_switch_id = 'GLOBAL'
                """.trimIndent(),
            )
        }
        assertCheckViolation {
            jdbcTemplate.update(
                """
                update risk_kill_switch
                set active = true,
                    reason_class = 'OPERATOR_MANUAL_STOP',
                    changed_by = null,
                    changed_by_role = 'ADMIN'
                where kill_switch_id = 'GLOBAL'
                """.trimIndent(),
            )
        }

        listOf(
            "'KILL_SWITCH_CHANGED', jsonb_build_object(" +
                "'generation', 2, 'previousActive', false, 'nextActive', true, " +
                "'reasonClass', 'OPERATOR_MANUAL_STOP', 'changedBy', 'usr_demo_admin', " +
                "'changedByRole', 'ADMIN', 'correlationId', 'req-audit-missing')",
            "'KILL_SWITCH_CHANGED', jsonb_build_object(" +
                "'generation', 2, 'previousActive', false, 'nextActive', true, " +
                "'reasonClass', 'OPERATOR_MANUAL_STOP', 'changedBy', 'usr_demo_admin', " +
                "'changedByRole', 'ADMIN', 'correlationId', 'req-audit-extra', " +
                "'invalidatedDecisionCount', 0, 'rawReason', 'forbidden')",
            "'UNSAFE_ACTION', jsonb_build_object(" +
                "'generation', 2, 'previousActive', false, 'nextActive', true, " +
                "'reasonClass', 'OPERATOR_MANUAL_STOP', 'changedBy', 'usr_demo_admin', " +
                "'changedByRole', 'ADMIN', 'correlationId', 'req-audit-action', " +
                "'invalidatedDecisionCount', 0)",
        ).forEachIndexed { index, actionAndPayload ->
            assertCheckViolation {
                jdbcTemplate.update(
                    """
                    insert into audit_logs (
                      audit_log_id, user_id, actor_role, action, target_type,
                      target_id, request_id, payload_json, created_at
                    )
                    select
                      'aud-v10-denied-$index', 'usr_demo_admin', 'ADMIN',
                      unsafe.action, 'KILL_SWITCH', 'GLOBAL',
                      'req-audit-${listOf("missing", "extra", "action")[index]}',
                      unsafe.payload, now()
                    from (
                      select $actionAndPayload
                    ) as unsafe(action, payload)
                    """.trimIndent(),
                )
            }
        }
    }

    @Test
    fun `Kill Switch admin revalidation classifies current status role and security version`() {
        val original =
            jdbcTemplate.queryForMap(
                """
                select role, status, security_version
                from users
                where user_id = 'usr_demo_admin'
                """.trimIndent(),
            )
        val securityVersion = (original["security_version"] as Number).toLong()
        try {
            assertEquals("AUTHORIZED", revalidateKillSwitchAdmin(securityVersion))

            jdbcTemplate.update("update users set role = 'USER' where user_id = 'usr_demo_admin'")
            assertEquals("FORBIDDEN", revalidateKillSwitchAdmin(securityVersion))

            jdbcTemplate.update(
                "update users set role = 'ADMIN', status = 'DISABLED' where user_id = 'usr_demo_admin'",
            )
            assertEquals("UNAUTHORIZED", revalidateKillSwitchAdmin(securityVersion))

            jdbcTemplate.update(
                """
                update users
                set status = 'ACTIVE', security_version = security_version + 1
                where user_id = 'usr_demo_admin'
                """.trimIndent(),
            )
            assertEquals("UNAUTHORIZED", revalidateKillSwitchAdmin(securityVersion))
        } finally {
            jdbcTemplate.update(
                """
                update users
                set role = ?, status = ?, security_version = ?
                where user_id = 'usr_demo_admin'
                """.trimIndent(),
                original["role"],
                original["status"],
                original["security_version"],
            )
        }
    }

    @Test
    fun `global invalidation spans owners excludes consumed decisions and remains owner scoped`() {
        cleanupS24InvalidationFixtures()
        try {
            insertOrderFixture()
            insertSecondDecisionFixture()
            insertAdminDecisionFixture()
            jdbcTemplate.update(
                """
                update risk_kill_switch
                set active = true,
                    reason_class = 'OPERATOR_MANUAL_STOP',
                    generation = 7,
                    changed_by = 'usr_demo_admin',
                    changed_by_role = 'ADMIN',
                    changed_at = now(),
                    request_id = 'req-v10-global-invalidation'
                where kill_switch_id = 'GLOBAL'
                """.trimIndent(),
            )
            jdbcTemplate.update(
                """
                insert into orders (
                  order_id, user_id, account_id, account_scope_hash, decision_id,
                  decision_evaluation_id, brokerage_mode, idempotency_scope_hash,
                  idempotency_owner_scope_hash, request_hash, symbol, side, order_type,
                  quantity, submitted_price_krw, status, order_intent_json,
                  result_canonical_json, acknowledged_by, acknowledged_at, submitted_at
                ) values (
                  'ord_mock_0000000000000000000000000000000b', 'usr-flyway',
                  'acct_0000000000000000000000000000000b', repeat('b', 64),
                  'dec-flyway-b', 'eval-flyway-b', 'KIS_MOCK', repeat('1', 64),
                  repeat('2', 64), repeat('3', 64), '005930', 'BUY', 'MARKET',
                  1, null, 'SUBMITTED',
                  '{"symbol":"005930","side":"BUY","orderType":"MARKET","quantity":"1","estimatedPrice":"70000","estimatedAmount":"70000","timeframe":"1d","strategyId":"flyway-fixture"}'::jsonb,
                  '{"orderId":"ord_mock_0000000000000000000000000000000b","brokerageMode":"KIS_MOCK","status":"SUBMITTED","submittedAt":"2030-01-02T03:04:05Z"}',
                  'usr-flyway', now(), now()
                )
                """.trimIndent(),
            )

            DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { connection ->
                connection
                    .prepareStatement(
                        """
                        select invalidate_unused_decisions_for_kill_switch(
                          generation,
                          changed_at,
                          request_id
                        )
                        from risk_kill_switch
                        where kill_switch_id = 'GLOBAL'
                        """.trimIndent(),
                    ).use { statement ->
                        statement.executeQuery().use { result ->
                            assertTrue(result.next())
                            assertEquals(2, result.getInt(1))
                        }
                    }
                connection
                    .prepareStatement(
                        """
                        select invalidate_unused_decisions_for_kill_switch(
                          generation,
                          changed_at,
                          request_id
                        )
                        from risk_kill_switch
                        where kill_switch_id = 'GLOBAL'
                        """.trimIndent(),
                    ).use { statement ->
                        statement.executeQuery().use { result ->
                            assertTrue(result.next())
                            assertEquals(0, result.getInt(1))
                        }
                    }
            }

            assertEquals(1, countDecisionInvalidations("dec-flyway"))
            assertEquals(0, countDecisionInvalidations("dec-flyway-b"))
            assertEquals(1, countDecisionInvalidations("dec-v10-admin"))

            jdbcTemplate.execute("grant select on table decision_invalidations to decision_app")
            try {
                assertInvalidationOwnerScope("usr-flyway", 1)
                assertInvalidationOwnerScope("usr_demo_admin", 1)
                assertInvalidationOwnerScope("usr_demo_user", 0)
            } finally {
                jdbcTemplate.execute("revoke select on table decision_invalidations from decision_app")
            }
            assertDecisionUsability("usr-flyway", "dec-flyway", expectedRows = 1, invalidated = true, consumed = null)
            assertDecisionUsability(
                "usr-flyway",
                "dec-flyway-b",
                expectedRows = 1,
                invalidated = false,
                consumed = "ord_mock_0000000000000000000000000000000b",
            )
            assertDecisionUsability(
                "usr-flyway",
                "dec-v10-admin",
                expectedRows = 0,
                invalidated = false,
                consumed = null,
            )
        } finally {
            jdbcTemplate.update(
                """
                update risk_kill_switch
                set active = false,
                    reason_class = 'INITIAL_STATE',
                    generation = 1,
                    changed_by = null,
                    changed_by_role = 'SYSTEM',
                    changed_at = now(),
                    request_id = null
                where kill_switch_id = 'GLOBAL'
                """.trimIndent(),
            )
            cleanupS24InvalidationFixtures()
        }
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
            "current_corporation_registry_projection",
            "disclosure_event_observation_projection",
            "disclosure_collection_status_projection",
        ).forEach { table ->
            assertFalse(hasTablePrivilege("decision_app", table, "SELECT"), "unexpected source SELECT on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "INSERT"), "unexpected source INSERT on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "UPDATE"), "unexpected source UPDATE on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "DELETE"), "unexpected source DELETE on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "TRUNCATE"), "unexpected source TRUNCATE on $table")
        }
        assertFalse(hasTablePrivilege("decision_app", "rag_answers_v2_legacy", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "decision_idempotency_results", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "flyway_schema_history", "SELECT"))
        assertFalse(hasSchemaPrivilege("decision_app", "CREATE"))

        listOf(
            "current_corporation_registry_projection",
            "disclosure_event_observation_projection",
            "disclosure_collection_status_projection",
        ).forEach { table ->
            assertTrue(hasTablePrivilege("decision_disclosure_reader", table, "SELECT"), "missing reader SELECT on $table")
        }
        listOf(
            "decisions",
            "audit_logs",
            "market_quote_observations",
            "corporation_registry_observations",
            "flyway_schema_history",
        ).forEach { table ->
            listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(
                    hasTablePrivilege("decision_disclosure_reader", table, privilege),
                    "unexpected disclosure reader $privilege on $table",
                )
            }
        }
        assertFalse(hasSchemaPrivilege("decision_disclosure_reader", "CREATE"))
        assertRolePermissionDenied(
            "decision_disclosure_reader",
            DISCLOSURE_READER_PASSWORD,
            "insert into decisions (decision_id) values ('reader-forbidden')",
        )
        assertRolePermissionDenied(
            "decision_disclosure_reader",
            DISCLOSURE_READER_PASSWORD,
            "select * from flyway_schema_history",
        )

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
            "select * from rag_answers_v2_legacy limit 0",
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
    fun `V14 fill projection observation checks and writer role are fail closed`() {
        insertOrderFixture()
        val orderId = "ord_mock_${"e".repeat(32)}"
        jdbcTemplate.update(
            """
            insert into orders (
              order_id, user_id, account_id, account_scope_hash, decision_id,
              decision_evaluation_id, brokerage_mode, idempotency_scope_hash,
              idempotency_owner_scope_hash, request_hash, symbol, side, order_type,
              quantity, submitted_price_krw, status, order_intent_json,
              result_canonical_json, acknowledged_by, acknowledged_at, submitted_at
            ) values (
              ?, 'usr-flyway', 'acct_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', repeat('1', 64),
              'dec-flyway', 'eval-flyway', 'KIS_MOCK', repeat('2', 64),
              repeat('3', 64), repeat('4', 64), '005930', 'BUY', 'MARKET',
              10, null, 'SUBMITTED',
              '{"symbol":"005930","side":"BUY","orderType":"MARKET","quantity":"10","estimatedPrice":"10000","estimatedAmount":"100000","timeframe":"1d","strategyId":"s33-check"}'::jsonb,
              '{"orderId":"sanitized","brokerageMode":"KIS_MOCK","status":"SUBMITTED"}',
              'usr-flyway', now(), now()
            )
            """.trimIndent(),
            orderId,
        )
        val projection =
            jdbcTemplate.queryForMap(
                """
                select filled_quantity, leaves_quantity, unfilled_terminated_quantity,
                       average_fill_price_krw, reconciliation_status, reconciled_at
                from orders
                where order_id = ?
                """.trimIndent(),
                orderId,
            )
        assertEquals(0L, projection["filled_quantity"])
        assertEquals(10L, projection["leaves_quantity"])
        assertEquals(0L, projection["unfilled_terminated_quantity"])
        assertEquals(null, projection["average_fill_price_krw"])
        assertEquals("NOT_APPLICABLE", projection["reconciliation_status"])
        assertEquals(null, projection["reconciled_at"])

        assertCheckViolation {
            jdbcTemplate.update(
                "update orders set filled_quantity = 1 where order_id = ?",
                orderId,
            )
        }
        assertCheckViolation {
            jdbcTemplate.update(
                """
                insert into order_fill_observations (
                  observation_id, order_id, provider_exec_ref_hash, exec_type,
                  fill_quantity, fill_price_krw, cumulative_quantity, leaves_quantity,
                  average_fill_price_krw, observed_at, received_at, schema_version,
                  source_version, source_ref, completeness, artifact_hash
                ) values (
                  'ofo_ffffffffffffffffffffffffffffffff', ?, repeat('5', 64),
                  'FILL', 10, null, 10, 0, 10000, now(), now(), '1',
                  's3.3-fill-observation-v1', 'fixture-s33-invalid',
                  'COMPLETE', repeat('6', 64)
                )
                """.trimIndent(),
                orderId,
            )
        }

        assertTrue(hasTablePrivilege("decision_fill_writer", "order_fill_observations", "INSERT"))
        listOf("SELECT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
            assertFalse(
                hasTablePrivilege("decision_fill_writer", "order_fill_observations", privilege),
                "unexpected decision_fill_writer $privilege",
            )
        }
        assertFalse(hasTablePrivilege("decision_app", "order_fill_observations", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "order_fill_observations", "INSERT"))
        assertWriterInsert(
            "decision_fill_writer",
            FILL_WRITER_PASSWORD,
            """
            insert into order_fill_observations (
              observation_id, order_id, provider_exec_ref_hash, exec_type,
              fill_quantity, fill_price_krw, cumulative_quantity, leaves_quantity,
              average_fill_price_krw, observed_at, received_at, schema_version,
              source_version, source_ref, completeness, artifact_hash
            ) values (
              'ofo_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', '$orderId', repeat('7', 64),
              'PARTIAL_FILL', 4, 10000, 4, 6, 10000,
              '2031-01-01T00:00:00Z', '2031-01-01T00:00:01Z', '1',
              's3.3-fill-observation-v1', 'fixture-s33-valid',
              'COMPLETE', repeat('8', 64)
            )
            """.trimIndent(),
        )
        assertUniqueViolation {
            jdbcTemplate.update(
                """
                insert into order_fill_observations (
                  observation_id, order_id, provider_exec_ref_hash, exec_type,
                  fill_quantity, fill_price_krw, cumulative_quantity, leaves_quantity,
                  observed_at, received_at, schema_version, source_version,
                  source_ref, completeness, artifact_hash
                ) values (
                  'ofo_dddddddddddddddddddddddddddddddd', ?, repeat('7', 64),
                  'PARTIAL_FILL', 4, 10000, 4, 6, now(), now(), '1',
                  's3.3-fill-observation-v1', 'fixture-s33-duplicate',
                  'COMPLETE', repeat('9', 64)
                )
                """.trimIndent(),
                orderId,
            )
        }
        assertRolePermissionDenied(
            "decision_fill_writer",
            FILL_WRITER_PASSWORD,
            "select * from order_fill_observations",
        )
        assertRolePermissionDenied(
            "decision_fill_writer",
            FILL_WRITER_PASSWORD,
            "update orders set status = status where false",
        )
        assertRolePermissionDenied(
            "decision_app",
            APP_PASSWORD,
            "select * from apply_stored_order_fills('{}'::jsonb, 'invalid-capability')",
        )
    }

    @Test
    fun `internal paper uses only explicit margin and never synthesizes position classification`() {
        jdbcTemplate.update(
            """
            insert into paper_accounts (
              account_id, user_id, name, cash_balance, currency, status,
              created_at, updated_at, owner_scope_hash, margin_requirement_krw
            )
            values (
              'acct_00000000000000000000000000000023',
              'usr_demo_admin', 'Paper S2.3', 900000, 'KRW', 'ACTIVE',
              '2031-02-03T04:00:00Z', '2031-02-03T04:05:06Z',
              repeat('2', 64), null
            )
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into paper_positions (
              position_id, account_id, symbol, quantity, average_price, market_value, updated_at
            )
            values (
              'position-s23', 'acct_00000000000000000000000000000023',
              '999999', 1, 100000, 100000, '2031-02-03T04:05:06Z'
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

        jdbcTemplate.update(
            """
            update paper_accounts
            set margin_requirement_krw = 0
            where account_id = 'acct_00000000000000000000000000000023'
            """.trimIndent(),
        )
        val explicitResolution =
            portfolioContextAdapter.resolve("usr_demo_admin", PortfolioSource.INTERNAL_PAPER)
                as PortfolioContextResolution.Available
        val explicitRequest = request.copy(portfolioContext = explicitResolution.context)
        val explicitMargin = storedMarginAdapter.load(explicitRequest) as MetricCell.Available
        assertEquals(0L, (explicitMargin.value as MetricValue.Whole).value)
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
    fun `previous close only observation never becomes a zero current price metric`() {
        jdbcTemplate.update(
            """
            insert into market_quote_observations (
              observation_id, symbol, source, price_krw, previous_close_krw,
              bid_krw, ask_krw, completeness, observed_at, received_at,
              schema_version, source_version, payload_json, source_ref, artifact_hash
            ) values (
              'quote-previous-close-only-s32', '035720', 'KIS_MOCK', null, 45000,
              44950, 45000, 'COMPLETE',
              '2026-06-24T02:59:00Z', '2026-06-24T02:59:01Z',
              'market-quote-observation.v1', 'previous-close-only-v1',
              '{"symbol":"035720","priceKrw":null,"previousCloseKrw":45000}'::jsonb,
              repeat('b', 64), repeat('c', 64)
            )
            """.trimIndent(),
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
                        symbol = "035720",
                        side = "BUY",
                        orderType = "MARKET",
                        quantity = 1,
                        estimatedPrice = 45_000,
                        estimatedAmount = 45_000,
                        timeframe = "1d",
                        strategyId = "previous-close-only-test",
                    ),
                evaluationAsOf = Instant.parse("2026-06-24T03:00:00Z"),
            )

        assertTrue(marketQuoteAdapter.load(request) is MetricCell.Missing)
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
                order_id, user_id, account_id, account_scope_hash, decision_id,
                decision_evaluation_id, brokerage_mode, idempotency_scope_hash,
                idempotency_owner_scope_hash, request_hash, symbol, side, order_type,
                quantity, submitted_price_krw, status, order_intent_json,
                result_canonical_json, acknowledged_by, acknowledged_at, submitted_at
            )
            values (
                'ord_mock_00000000000000000000000000000001', 'usr-flyway',
                'acct_00000000000000000000000000000001', repeat('1', 64),
                'dec-flyway', 'eval-flyway', 'KIS_MOCK', repeat('2', 64),
                repeat('3', 64), repeat('4', 64), '005930', 'BUY', 'MARKET',
                1, null, 'SUBMITTED',
                '{"symbol":"005930","side":"BUY","orderType":"MARKET","quantity":"1","estimatedPrice":"70000","estimatedAmount":"70000","timeframe":"1d","strategyId":"flyway-fixture"}'::jsonb,
                '{"orderId":"ord_mock_00000000000000000000000000000001","brokerageMode":"KIS_MOCK","status":"SUBMITTED","submittedAt":"2030-01-02T03:04:05Z"}',
                'usr-flyway', now(), now()
            )
            """.trimIndent(),
        )

        assertUniqueViolation {
            jdbcTemplate.update(
                """
                insert into orders (
                    order_id, user_id, account_id, account_scope_hash, decision_id,
                    decision_evaluation_id, brokerage_mode, idempotency_scope_hash,
                    idempotency_owner_scope_hash, request_hash, symbol, side, order_type,
                    quantity, submitted_price_krw, status, order_intent_json,
                    result_canonical_json, acknowledged_by, acknowledged_at, submitted_at
                )
                values (
                    'ord_mock_00000000000000000000000000000002', 'usr-flyway',
                    'acct_00000000000000000000000000000001', repeat('1', 64),
                    'dec-flyway', 'eval-flyway', 'KIS_MOCK', repeat('5', 64),
                    repeat('6', 64), repeat('7', 64), '005930', 'BUY', 'MARKET',
                    1, null, 'SUBMITTED',
                    '{"symbol":"005930","side":"BUY","orderType":"MARKET","quantity":"1","estimatedPrice":"70000","estimatedAmount":"70000","timeframe":"1d","strategyId":"flyway-fixture"}'::jsonb,
                    '{"orderId":"ord_mock_00000000000000000000000000000002","brokerageMode":"KIS_MOCK","status":"SUBMITTED","submittedAt":"2030-01-02T03:04:05Z"}',
                    'usr-flyway', now(), now()
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

    private fun assertInvalidationOwnerScope(
        actorUserId: String,
        expectedRows: Int,
    ) {
        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { connection ->
            connection.autoCommit = false
            connection.prepareStatement("select set_config('app.actor_user_id', ?, true)").use { statement ->
                statement.setString(1, actorUserId)
                statement.executeQuery().close()
            }
            connection.createStatement().use { statement ->
                statement.executeQuery("select count(*) from decision_invalidations").use { result ->
                    assertTrue(result.next())
                    assertEquals(expectedRows, result.getInt(1))
                }
            }
            connection.rollback()
        }
    }

    private fun revalidateKillSwitchAdmin(securityVersion: Long): String =
        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { connection ->
            connection.prepareStatement("select revalidate_kill_switch_admin('usr_demo_admin', ?)").use { statement ->
                statement.setLong(1, securityVersion)
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    result.getString(1)
                }
            }
        }

    private fun countDecisionInvalidations(decisionId: String): Int =
        jdbcTemplate.queryForObject(
            "select count(*) from decision_invalidations where decision_id = ?",
            Int::class.java,
            decisionId,
        ) ?: 0

    private fun assertDecisionUsability(
        actorUserId: String,
        decisionId: String,
        expectedRows: Int,
        invalidated: Boolean,
        consumed: String?,
    ) {
        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { connection ->
            connection.autoCommit = false
            connection.prepareStatement("select set_config('app.actor_user_id', ?, true)").use { statement ->
                statement.setString(1, actorUserId)
                statement.executeQuery().close()
            }
            connection.prepareStatement("select set_config('app.requested_decision_id', ?, true)").use { statement ->
                statement.setString(1, decisionId)
                statement.executeQuery().close()
            }
            connection.createStatement().use { statement ->
                statement.executeQuery("select * from read_decision_usability()").use { result ->
                    var rows = 0
                    while (result.next()) {
                        rows += 1
                        assertEquals(invalidated, result.getBoolean("invalidated"))
                        assertEquals(consumed, result.getString("consumed_by_order_id"))
                    }
                    assertEquals(expectedRows, rows)
                }
            }
            connection.rollback()
        }
    }

    private fun insertAdminDecisionFixture() {
        jdbcTemplate.update(
            """
            insert into principles (
              principle_id, user_id, preset_id, title, mode, status, current_version
            ) values (
              'prn-v10-admin', 'usr_demo_admin', 'balanced',
              'V10 Admin Principle', 'GUIDE', 'ACTIVE', 1
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
              'prv-v10-admin-v1', 'prn-v10-admin', 1, 'balanced',
              'V10 Admin Principle', 'GUIDE', 'ACTIVE', rules_json,
              array['presetId', 'title', 'mode', 'status', 'rules'], 'usr_demo_admin'
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
            ) values (
              'dec-v10-admin', 'eval-v10-admin', 'usr_demo_admin',
              'prn-v10-admin', 'prv-v10-admin-v1', 1, 'INTERNAL_PAPER',
              '005930', 'BUY', 'ALLOW', 'GUIDE', true, 'NONE',
              now(), now(), now() + interval '10 minutes',
              'risk-decision.v1', 's2.2-metric-snapshot-v2', 1,
              's2.3-readiness-v1', '{}'::jsonb, repeat('e', 64),
              repeat('f', 64), '{}'::jsonb
            )
            """.trimIndent(),
        )
    }

    private fun cleanupS24InvalidationFixtures() {
        deleteOrderFillFixtures("('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')")
        jdbcTemplate.update(
            "delete from orders where decision_id in ('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')",
        )
        jdbcTemplate.update(
            "delete from decision_invalidations where decision_id in ('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')",
        )
        jdbcTemplate.update(
            "delete from decision_idempotency_results where decision_id in ('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')",
        )
        jdbcTemplate.update(
            "delete from decision_traces where decision_id in ('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')",
        )
        jdbcTemplate.update(
            "delete from decision_artifacts where decision_id in ('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')",
        )
        jdbcTemplate.update(
            "delete from decision_violations where decision_id in ('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')",
        )
        jdbcTemplate.update(
            "delete from audit_logs where target_id in ('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')",
        )
        jdbcTemplate.update(
            "delete from decisions where decision_id in ('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')",
        )
        jdbcTemplate.update(
            "delete from principle_versions where principle_id in ('prn-flyway', 'prn-v10-admin')",
        )
        jdbcTemplate.update("delete from principles where principle_id in ('prn-flyway', 'prn-v10-admin')")
        jdbcTemplate.update("delete from users where user_id = 'usr-flyway'")
    }

    private fun insertOrderFixture() {
        deleteOrderFillFixtures("('dec-flyway', 'dec-flyway-b')")
        jdbcTemplate.update("delete from orders where decision_id in ('dec-flyway', 'dec-flyway-b')")
        jdbcTemplate.update(
            "delete from decision_invalidations where decision_id in ('dec-flyway', 'dec-flyway-b')",
        )
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

    private fun deleteOrderFillFixtures(decisionIds: String) {
        // append-only 운영 계약은 유지하되, Testcontainers superuser만 격리 fixture를 역순 정리한다.
        jdbcTemplate.update(
            """
            delete from order_fill_application_receipts
            where order_id in (
              select order_id from orders where decision_id in $decisionIds
            )
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            delete from order_fill_observations
            where order_id in (
              select order_id from orders where decision_id in $decisionIds
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
                .placeholders(
                    mapOf(
                        "brokerageDbCapabilityTokenSha256" to TEST_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256,
                    ),
                ).javaMigrations(s21ActorTrustMigration())
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
        private const val DISCLOSURE_READER_PASSWORD = "disclosure-reader-test"
        private const val MARKET_WRITER_PASSWORD = "market-writer-test"
        private const val PORTFOLIO_WRITER_PASSWORD = "portfolio-writer-test"
        private const val RISK_WRITER_PASSWORD = "risk-writer-test"
        private const val FILL_WRITER_PASSWORD = "fill-writer-test"
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
            registry.add("app.decision.grpc.shared-secret") { SpringApiIntegrationTestBase.TEST_GRPC_SHARED_SECRET }
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
