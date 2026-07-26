package com.capstone.decision

import com.capstone.decision.application.risk.KillSwitchActor
import com.capstone.decision.application.risk.KillSwitchMutationCommand
import com.capstone.decision.application.risk.KillSwitchMutationPort
import com.capstone.decision.domain.risk.KillSwitchActorRole
import com.capstone.decision.domain.risk.KillSwitchReasonClass
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.context.annotation.Import
import org.springframework.data.redis.core.StringRedisTemplate
import org.springframework.http.MediaType
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.jdbc.datasource.DriverManagerDataSource
import org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.MvcResult
import org.springframework.test.web.servlet.get
import org.springframework.test.web.servlet.post
import org.springframework.test.web.servlet.setup.DefaultMockMvcBuilder
import org.springframework.test.web.servlet.setup.MockMvcBuilders
import org.springframework.web.context.WebApplicationContext
import org.testcontainers.containers.GenericContainer
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.sql.DriverManager
import java.sql.SQLException
import java.sql.Timestamp
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import javax.sql.DataSource

// S3.1 mock order path는 실제 KIS/provider 호출 없이 PostgreSQL ledger와 Redis-free durable idempotency만 검증한다.
@Testcontainers
@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
@Import(DecisionApiFixedClockConfiguration::class)
class BrokerageApiIntegrationTest(
    @Autowired private val webApplicationContext: WebApplicationContext,
    @Autowired private val objectMapper: ObjectMapper,
    @Autowired private val redisTemplate: StringRedisTemplate,
    @Autowired private val killSwitchMutationPort: KillSwitchMutationPort,
    @Autowired private val appDataSource: DataSource,
) : SpringApiIntegrationTestBase() {
    private lateinit var mockMvc: MockMvc
    private val jdbcTemplate: JdbcTemplate by lazy {
        JdbcTemplate(
            DriverManagerDataSource(
                postgres.jdbcUrl,
                postgres.username,
                postgres.password,
            ),
        )
    }

    @BeforeEach
    fun setUp() {
        redisTemplate.keys("brokerage*").takeIf { it.isNotEmpty() }?.let(redisTemplate::delete)
        redisTemplate.keys("decision-idempotency:*").takeIf { it.isNotEmpty() }?.let(redisTemplate::delete)
        redisTemplate.keys("idempotency:*").takeIf { it.isNotEmpty() }?.let(redisTemplate::delete)
        jdbcTemplate.update("delete from order_events")
        jdbcTemplate.update("delete from orders")
        jdbcTemplate.update("delete from decision_invalidations")
        jdbcTemplate.update("delete from risk_kill_switch_transitions")
        jdbcTemplate.update("delete from event_outbox")
        jdbcTemplate.update("delete from audit_logs where target_type in ('DECISION', 'ORDER', 'KILL_SWITCH')")
        jdbcTemplate.update(
            """
            update risk_kill_switch
            set active = false,
                reason_class = 'INITIAL_STATE',
                generation = 1,
                changed_by = null,
                changed_by_role = 'SYSTEM',
                changed_at = ?::timestamptz,
                request_id = null
            where kill_switch_id = 'GLOBAL'
            """.trimIndent(),
            EVALUATION_AT,
        )
        jdbcTemplate.update("delete from decision_idempotency_results")
        jdbcTemplate.update("delete from decision_traces")
        jdbcTemplate.update("delete from decision_artifacts")
        jdbcTemplate.update("delete from decision_violations")
        jdbcTemplate.update("delete from decisions")
        jdbcTemplate.update("delete from portfolio_position_observations")
        jdbcTemplate.update("delete from portfolio_balance_observations")
        jdbcTemplate.update("delete from market_quote_observations")
        jdbcTemplate.update("delete from instrument_catalog_observations")
        jdbcTemplate.update("delete from deterministic_risk_observations")
        jdbcTemplate.update("delete from daily_order_count_observations")
        jdbcTemplate.update("delete from principle_versions where principle_id like 'prc_31%'")
        jdbcTemplate.update("delete from principles where principle_id like 'prc_31%'")
        mockMvc =
            MockMvcBuilders
                .webAppContextSetup(webApplicationContext)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    @Test
    fun `mock order submit consumes an ALLOW decision once and stores only sanitized ledger fields`() {
        val token = login("demo-user", userPassword())
        val decisionId = createDecision(token, suffix = "01", order = orderIntent())

        val first =
            submitMockOrder(
                token = token,
                idempotencyKey = "brokerage-submit-0001",
                requestId = "req-brokerage-submit",
                decisionId = decisionId,
                order = orderIntent(),
            )

        assertEquals(200, first.response.status, first.response.contentAsString)
        val data = json(first).at("/data")
        val orderId = data.path("orderId").stringValue()
        val accountId = data.path("accountId").stringValue()
        assertTrue(Regex("^ord_mock_[0-9a-f]{32}$").matches(orderId))
        assertEquals("acct_${"c".repeat(32)}", accountId)
        assertEquals("KIS_MOCK", data.path("brokerageMode").stringValue())
        assertEquals("SUBMITTED", data.path("status").stringValue())
        assertFalse(first.response.contentAsString.contains("accountNumber", ignoreCase = true))
        assertFalse(first.response.contentAsString.contains("account_scope_hash", ignoreCase = true))
        assertEquals(1, count("select count(*) from orders where decision_id = ?", decisionId))
        assertEquals(1, count("select count(*) from order_events where order_id = ?", orderId))
        assertEquals(1, count("select count(*) from audit_logs where target_type = 'ORDER' and target_id = ?", orderId))
        assertEquals(1, count("select count(*) from event_outbox where event_type = 'brokerage.mock-order-submitted.v1'"))

        val ledgerText =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    """
                    select idempotency_scope_hash || ' ' || idempotency_owner_scope_hash || ' ' ||
                           request_hash || ' ' || account_id || ' ' || account_scope_hash || ' ' ||
                           order_intent_json::text || ' ' || result_canonical_json
                    from orders
                    where order_id = ?
                    """.trimIndent(),
                    String::class.java,
                    orderId,
                ),
            )
        assertFalse(ledgerText.contains("brokerage-submit-0001"))
        assertTrue(ledgerText.contains(accountId))
        assertTrue(ledgerText.contains("c".repeat(64)))
        assertFalse(ledgerText.contains("raw"))
        assertFalse(ledgerText.contains("token", ignoreCase = true))
        assertFalse(
            requireNotNull(
                jdbcTemplate.queryForObject(
                    "select payload_json::text from event_outbox where aggregate_id = ?",
                    String::class.java,
                    orderId,
                ),
            ).contains("account", ignoreCase = true),
        )

        val replay =
            submitMockOrder(
                token = token,
                idempotencyKey = "brokerage-submit-0001",
                requestId = "req-brokerage-replay",
                decisionId = decisionId,
                order = orderIntent(),
            )
        assertEquals(200, replay.response.status)
        assertEquals(data, json(replay).at("/data"))
        assertEquals(1, count("select count(*) from orders"))

        val idempotencyConflict =
            submitMockOrder(
                token = token,
                idempotencyKey = "brokerage-submit-0001",
                requestId = "req-brokerage-idempotency-conflict",
                decisionId = decisionId,
                order = orderIntent(quantity = 3, estimatedAmount = 210_000),
            )
        assertEquals(409, idempotencyConflict.response.status)
        assertEquals("IDEMPOTENCY_CONFLICT", json(idempotencyConflict).at("/error/code").stringValue())
        assertEquals(1, count("select count(*) from orders"))

        val detail =
            mockMvc
                .get("/api/v1/brokerage/orders/$orderId") {
                    bearer(token)
                    header("X-Request-Id", "req-brokerage-detail")
                }.andReturn()
        assertEquals(200, detail.response.status)
        assertEquals(orderId, json(detail).at("/data/orderId").stringValue())
        assertEquals(accountId, json(detail).at("/data/accountId").stringValue())
        assertEquals(decisionId, json(detail).at("/data/decisionId").stringValue())
        assertFalse(detail.response.contentAsString.contains("idempotency", ignoreCase = true))

        val cancel =
            cancelOrder(
                token = token,
                idempotencyKey = brokerageIdempotency("cancel", 1),
                requestId = "req-brokerage-cancel",
                orderId = orderId,
            )
        assertEquals(200, cancel.response.status, cancel.response.contentAsString)
        assertEquals("CANCEL_REQUESTED", json(cancel).at("/data/status").stringValue())
        assertEquals(2, count("select count(*) from order_events where order_id = ?", orderId))
        assertEquals(
            listOf(1, 2),
            jdbcTemplate.queryForList(
                "select event_seq from order_events where order_id = ? order by event_seq",
                Int::class.java,
                orderId,
            ),
        )
        assertEquals(2, count("select count(*) from audit_logs where target_type = 'ORDER' and target_id = ?", orderId))
        assertEquals(
            1,
            count("select count(*) from event_outbox where event_type = 'brokerage.mock-order-cancel-requested.v1'"),
        )
        val cancelReplay =
            cancelOrder(
                token = token,
                idempotencyKey = brokerageIdempotency("cancel", 1),
                requestId = "req-brokerage-cancel-replay",
                orderId = orderId,
            )
        assertEquals(200, cancelReplay.response.status)
        assertEquals(json(cancel).at("/data"), json(cancelReplay).at("/data"))
        assertEquals(2, count("select count(*) from order_events where order_id = ?", orderId))

        val secondCancel =
            cancelOrder(
                token = token,
                idempotencyKey = brokerageIdempotency("cancel", 2),
                requestId = "req-brokerage-cancel-conflict",
                orderId = orderId,
            )
        assertEquals(409, secondCancel.response.status)
        assertEquals("CONFLICT", json(secondCancel).at("/error/code").stringValue())

        val postCancelDetail =
            mockMvc
                .get("/api/v1/brokerage/orders/$orderId") {
                    bearer(token)
                    header("X-Request-Id", "req-brokerage-detail-after-cancel")
                }.andReturn()
        assertEquals("CANCEL_REQUESTED", json(postCancelDetail).at("/data/status").stringValue())

        val balance =
            mockMvc
                .get("/api/v1/brokerage/mock/accounts/$accountId/balances") {
                    bearer(token)
                    header("X-Request-Id", "req-brokerage-balance")
                }.andReturn()
        assertEquals(200, balance.response.status, balance.response.contentAsString)
        assertEquals(accountId, json(balance).at("/data/accountId").stringValue())
        assertEquals(10_000_000, json(balance).at("/data/cashKrw").intValue())
        assertEquals(0, json(balance).at("/data/positions").size())

        val buyable =
            mockMvc
                .get("/api/v1/brokerage/mock/accounts/$accountId/buyable?symbol=005930&price=70000") {
                    bearer(token)
                    header("X-Request-Id", "req-brokerage-buyable")
                }.andReturn()
        assertEquals(200, buyable.response.status, buyable.response.contentAsString)
        assertEquals(142, json(buyable).at("/data/buyableQuantity").intValue())
        assertEquals(9_940_000, json(buyable).at("/data/buyableAmountKrw").intValue())

        val adminToken = login("demo-admin", adminPassword())
        val crossOwner =
            mockMvc
                .get("/api/v1/brokerage/orders/$orderId") {
                    bearer(adminToken)
                    header("X-Request-Id", "req-brokerage-cross-owner")
                }.andReturn()
        assertEquals(404, crossOwner.response.status)
        val crossOwnerBalance =
            mockMvc
                .get("/api/v1/brokerage/mock/accounts/$accountId/balances") {
                    bearer(adminToken)
                    header("X-Request-Id", "req-brokerage-balance-cross-owner")
                }.andReturn()
        assertEquals(404, crossOwnerBalance.response.status)
    }

    @Test
    fun `same decision cannot be consumed by a different idempotency key`() {
        val token = login("demo-user", userPassword())
        val decisionId = createDecision(token, suffix = "02", order = orderIntent())
        assertEquals(
            200,
            submitMockOrder(token, "brokerage-once-0001", "req-brokerage-once-a", decisionId, orderIntent())
                .response
                .status,
        )

        val conflict =
            submitMockOrder(token, "brokerage-once-0002", "req-brokerage-once-b", decisionId, orderIntent())

        assertEquals(409, conflict.response.status)
        assertEquals("CONFLICT", json(conflict).at("/error/code").stringValue())
        assertEquals(1, count("select count(*) from orders"))
    }

    @Test
    fun `expired invalidated and mismatched decisions fail closed before order writes`() {
        val token = login("demo-user", userPassword())
        val expiredDecisionId = createDecision(token, suffix = "03", order = orderIntent())
        jdbcTemplate.update(
            """
            update decisions
            set evaluation_as_of = ?::timestamptz,
                created_at = ?::timestamptz,
                valid_until = ?::timestamptz
            where decision_id = ?
            """.trimIndent(),
            EVALUATION_AT.minusSeconds(2),
            EVALUATION_AT.minusSeconds(2),
            EVALUATION_AT.minusSeconds(1),
            expiredDecisionId,
        )
        val expired =
            submitMockOrder(
                token,
                "brokerage-expired-0001",
                "req-brokerage-expired",
                expiredDecisionId,
                orderIntent(),
            )
        assertEquals(409, expired.response.status)
        assertEquals("DECISION_EXPIRED", json(expired).at("/error/code").stringValue())

        val invalidatedDecisionId = createDecision(token, suffix = "04", order = orderIntent())
        killSwitchMutationPort.mutate(
            KillSwitchMutationCommand(
                actor =
                    KillSwitchActor(
                        userId = "usr_demo_user",
                        role = KillSwitchActorRole.USER,
                        securityVersion = 1,
                        requestId = "req-brokerage-kill-switch",
                    ),
                requestedActive = true,
                reasonClass = KillSwitchReasonClass.USER_MANUAL_STOP,
            ),
        )
        val blocked =
            submitMockOrder(
                token,
                "brokerage-blocked-0001",
                "req-brokerage-blocked",
                invalidatedDecisionId,
                orderIntent(),
            )
        assertEquals(422, blocked.response.status)
        assertEquals("RISK_BLOCKED", json(blocked).at("/error/code").stringValue())

        assertEquals(0, count("select count(*) from orders"))
        assertEquals(0, count("select count(*) from order_events"))
        assertEquals(0, count("select count(*) from event_outbox where event_type = 'brokerage.mock-order-submitted.v1'"))
    }

    @Test
    fun `database order sink rejects a Decision expired after a stale service timestamp`() {
        val token = login("demo-user", userPassword())
        val decisionId = createDecision(token, suffix = "09", order = orderIntent())
        val ownerScopeHash =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    """
                    select snapshot_artifact_canonical_json::jsonb #>> '{portfolio,ownerScopeHash}'
                    from decision_artifacts
                    where decision_id = ?
                    """.trimIndent(),
                    String::class.java,
                    decisionId,
                ),
            )
        val staleWindow =
            jdbcTemplate.queryForMap(
                """
                select
                  clock_timestamp() - interval '10 minutes' as requested_created_at,
                  clock_timestamp() - interval '1 second' as expired_at
                """.trimIndent(),
            )
        val requestedCreatedAtTimestamp = staleWindow["requested_created_at"] as Timestamp
        val expiredAtTimestamp = staleWindow["expired_at"] as Timestamp
        val requestedCreatedAt = requestedCreatedAtTimestamp.toInstant()
        jdbcTemplate.update(
            """
            update decisions
            set evaluation_as_of = ?::timestamptz,
                created_at = ?::timestamptz,
                valid_until = ?::timestamptz
            where decision_id = ?
            """.trimIndent(),
            requestedCreatedAtTimestamp,
            requestedCreatedAtTimestamp,
            expiredAtTimestamp,
            decisionId,
        )

        val orderId = "ord_mock_00000000000000000000000000000009"
        val pinnedOrderIntent =
            mapOf(
                "symbol" to "005930",
                "side" to "BUY",
                "orderType" to "MARKET",
                "quantity" to "2",
                "estimatedPrice" to "70000",
                "estimatedAmount" to "140000",
                "timeframe" to "1d",
                "strategyId" to "cash-equity-v1",
            )
        val payload =
            objectMapper.writeValueAsString(
                mapOf<String, Any?>(
                    "actorUserId" to "usr_demo_user",
                    "actorRole" to "USER",
                    "securityVersion" to 1,
                    "requestId" to "req-brokerage-stale-expiry",
                    "decisionId" to decisionId,
                    "orderId" to orderId,
                    "observedKillSwitchGeneration" to 1,
                    "idempotencyScopeHash" to "9".repeat(64),
                    "idempotencyOwnerScopeHash" to "8".repeat(64),
                    "requestHash" to "7".repeat(64),
                    "accountId" to "acct_${ownerScopeHash.take(32)}",
                    "accountScopeHash" to ownerScopeHash,
                    "symbol" to "005930",
                    "side" to "BUY",
                    "orderType" to "MARKET",
                    "quantity" to 2,
                    "submittedPriceKrw" to null,
                    "orderIntent" to pinnedOrderIntent,
                    "resultCanonicalJson" to
                        objectMapper.writeValueAsString(
                            mapOf(
                                "orderId" to orderId,
                                "brokerageMode" to "KIS_MOCK",
                                "status" to "SUBMITTED",
                                "submittedAt" to requestedCreatedAt.toString(),
                            ),
                        ),
                    "warningsAccepted" to true,
                    "submittedAt" to requestedCreatedAt.toString(),
                    "createdAt" to requestedCreatedAt.toString(),
                    "orderEventId" to "oev_00000000000000000000000000000009",
                    "auditLogId" to "aud-brokerage-stale-expiry",
                    "outboxEventId" to "evt-brokerage-stale-expiry",
                ),
            )

        appDataSource.connection.use { connection ->
            connection
                .prepareStatement(
                    """
                    select operation_outcome
                    from create_mock_order(cast(? as jsonb), ?)
                    """.trimIndent(),
                ).use { statement ->
                    statement.setString(1, payload)
                    statement.setString(2, TEST_BROKERAGE_DB_CAPABILITY_TOKEN)
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        assertEquals("DECISION_EXPIRED", result.getString(1))
                    }
                }
        }
        assertEquals(0, count("select count(*) from orders where decision_id = ?", decisionId))
        assertEquals(0, count("select count(*) from event_outbox where event_id = 'evt-brokerage-stale-expiry'"))
    }

    @Test
    fun `LIMIT order is unavailable until current KRX tick table verification is attached`() {
        val token = login("demo-user", userPassword())
        val limitOrder = orderIntent(orderType = "LIMIT", estimatedPrice = 70_000)
        val decisionId = createDecision(token, suffix = "05", order = limitOrder)

        val response =
            submitMockOrder(
                token = token,
                idempotencyKey = "brokerage-limit-0001",
                requestId = "req-brokerage-limit",
                decisionId = decisionId,
                order = limitOrder,
            )

        assertEquals(503, response.response.status)
        assertEquals("BROKERAGE_UNAVAILABLE", json(response).at("/error/code").stringValue())
        assertEquals(0, count("select count(*) from orders"))
    }

    @Test
    fun `body supplied account actor or changed order intent is rejected without order writes`() {
        val token = login("demo-user", userPassword())
        val decisionId = createDecision(token, suffix = "06", order = orderIntent())
        val forged =
            submitRaw(
                token,
                "brokerage-forged-0001",
                "req-brokerage-forged",
                mapOf(
                    "decisionId" to decisionId,
                    "accountId" to "acct_raw",
                    "orderIntent" to orderIntent(),
                    "userAcknowledgement" to mapOf("warningsAccepted" to true),
                ),
            )
        assertEquals(400, forged.response.status)
        assertEquals("VALIDATION_ERROR", json(forged).at("/error/code").stringValue())

        val changed = orderIntent(quantity = 3, estimatedAmount = 210_000)
        val mismatch =
            submitMockOrder(
                token,
                "brokerage-mismatch-01",
                "req-brokerage-mismatch",
                decisionId,
                changed,
            )
        assertEquals(400, mismatch.response.status, mismatch.response.contentAsString)
        assertEquals("VALIDATION_ERROR", json(mismatch).at("/error/code").stringValue())
        assertEquals(0, count("select count(*) from orders"))
    }

    @Test
    fun `caller writable GUCs cannot unlock brokerage rows or explicit owner projections`() {
        val token = login("demo-user", userPassword())
        val decisionId = createDecision(token, suffix = "07", order = orderIntent())
        val submitted =
            submitMockOrder(
                token,
                "brokerage-guc-0001",
                "req-brokerage-guc",
                decisionId,
                orderIntent(),
            )
        assertEquals(200, submitted.response.status, submitted.response.contentAsString)
        val orderId = json(submitted).at("/data/orderId").stringValue()

        appDataSource.connection.use { connection ->
            connection.autoCommit = false
            connection.prepareStatement("SELECT set_config('app.actor_user_id', ?, true)").use { statement ->
                statement.setString(1, "usr_demo_user")
                statement.executeQuery().use { result -> check(result.next()) }
            }
            connection.prepareStatement("SELECT set_config('app.requested_order_id', ?, true)").use { statement ->
                statement.setString(1, orderId)
                statement.executeQuery().use { result -> check(result.next()) }
            }
            val denied =
                org.junit.jupiter.api.assertThrows<SQLException> {
                    connection.createStatement().use { statement ->
                        statement.executeQuery("SELECT order_id FROM orders")
                    }
                }
            assertEquals("42501", denied.sqlState)
            connection.rollback()
        }

        appDataSource.connection.use { connection ->
            val denied =
                org.junit.jupiter.api.assertThrows<SQLException> {
                    connection
                        .prepareStatement(
                            "SELECT * FROM read_mock_order_owner_projection(?, ?, ?)",
                        ).use { statement ->
                            statement.setString(1, "usr_demo_user")
                            statement.setString(2, orderId)
                            statement.setString(3, "0".repeat(64))
                            statement.executeQuery()
                        }
                }
            assertEquals("42501", denied.sqlState)
        }

        appDataSource.connection.use { connection ->
            connection
                .prepareStatement(
                    "SELECT count(*) FROM read_mock_order_owner_projection(?, ?, ?)",
                ).use { statement ->
                    statement.setString(1, "usr_demo_admin")
                    statement.setString(2, orderId)
                    statement.setString(3, TEST_BROKERAGE_DB_CAPABILITY_TOKEN)
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        assertEquals(0, result.getInt(1))
                    }
                }
        }
    }

    @Test
    fun `order sink serializes with a concurrent Kill Switch activation`() {
        val token = login("demo-user", userPassword())
        val decisionId = createDecision(token, suffix = "08", order = orderIntent())
        val executor = Executors.newSingleThreadExecutor()

        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { activation ->
            activation.autoCommit = false
            var committed = false
            try {
                activation
                    .prepareStatement(
                        "SELECT generation FROM risk_kill_switch WHERE kill_switch_id = 'GLOBAL' FOR UPDATE",
                    ).use { statement ->
                        statement.executeQuery().use { result ->
                            assertTrue(result.next())
                            assertEquals(1L, result.getLong(1))
                        }
                    }
                activation
                    .prepareStatement(
                        """
                        UPDATE risk_kill_switch
                        SET active = true,
                            reason_class = 'USER_MANUAL_STOP',
                            generation = generation + 1,
                            changed_by = 'usr_demo_user',
                            changed_by_role = 'USER',
                            changed_at = ?::timestamptz,
                            request_id = 'req-race-activation'
                        WHERE kill_switch_id = 'GLOBAL'
                        """.trimIndent(),
                    ).use { statement ->
                        statement.setObject(1, EVALUATION_AT)
                        assertEquals(1, statement.executeUpdate())
                    }

                val responseFuture =
                    executor.submit<MvcResult> {
                        submitMockOrder(
                            token,
                            "brokerage-race-0001",
                            "req-brokerage-race",
                            decisionId,
                            orderIntent(),
                        )
                    }
                val completedBeforeActivationCommit =
                    try {
                        responseFuture.get(750, TimeUnit.MILLISECONDS)
                        true
                    } catch (_: TimeoutException) {
                        false
                    }

                activation.commit()
                committed = true
                val response = responseFuture.get(10, TimeUnit.SECONDS)

                assertFalse(
                    completedBeforeActivationCommit,
                    "order persistence must wait for the locked Kill Switch generation",
                )
                assertEquals(422, response.response.status, response.response.contentAsString)
                assertEquals("RISK_BLOCKED", json(response).at("/error/code").stringValue())
            } finally {
                if (!committed) {
                    runCatching { activation.rollback() }
                }
                executor.shutdownNow()
            }
        }

        assertEquals(0, count("select count(*) from orders where decision_id = ?", decisionId))
        assertEquals(0, count("select count(*) from order_events"))
        assertEquals(0, count("select count(*) from audit_logs where target_type = 'ORDER'"))
        assertEquals(0, count("select count(*) from event_outbox where aggregate_type = 'ORDER'"))
    }

    private fun createDecision(
        token: String,
        suffix: String,
        order: Map<String, Any>,
    ): String {
        val principleId = insertPrinciple("usr_demo_user", "GUIDE", suffix)
        insertCompleteStoredSources(suffix = suffix, orderCount = 0)
        val response =
            evaluate(
                token,
                "decision-s31-$suffix-0001",
                "req-decision-s31-$suffix",
                mapOf(
                    "principleId" to principleId,
                    "portfolioSource" to "KIS_MOCK",
                    "orderIntent" to order,
                ),
            )
        assertEquals(200, response.response.status)
        assertEquals("ALLOW", json(response).at("/data/riskDecision/decision").stringValue())
        val decisionId = json(response).at("/data/decisionId").stringValue()
        assertEquals(1, visibleOrderableDecisionCount(decisionId), decisionVisibilityDebug(decisionId))
        return decisionId
    }

    private fun decisionVisibilityDebug(decisionId: String): String =
        "debug decision=$decisionId adminDecision=${count("select count(*) from decisions where decision_id = ?", decisionId)} " +
            "adminArtifact=${count("select count(*) from decision_artifacts where decision_id = ?", decisionId)} " +
            "ownerProjection=${visibleDecisionOwnerProjectionCount(decisionId)}"

    private fun visibleOrderableDecisionCount(decisionId: String): Int =
        appDataSource.connection.use { connection ->
            connection
                .prepareStatement("SELECT count(*) FROM read_mock_order_decision(?, ?, ?)")
                .use { statement ->
                    statement.setString(1, "usr_demo_user")
                    statement.setString(2, decisionId)
                    statement.setString(3, TEST_BROKERAGE_DB_CAPABILITY_TOKEN)
                    statement.executeQuery().use { result ->
                        check(result.next())
                        result.getInt(1)
                    }
                }
        }

    private fun visibleDecisionOwnerProjectionCount(decisionId: String): Int =
        appDataSource.connection.use { connection ->
            connection.autoCommit = false
            try {
                connection.prepareStatement("SELECT set_config('app.actor_user_id', ?, true)").use { statement ->
                    statement.setString(1, "usr_demo_user")
                    statement.executeQuery().use { result -> check(result.next()) }
                }
                connection.prepareStatement("SELECT set_config('app.requested_decision_id', ?, true)").use { statement ->
                    statement.setString(1, decisionId)
                    statement.executeQuery().use { result -> check(result.next()) }
                }
                val count =
                    connection.prepareStatement("SELECT count(*) FROM read_decision_owner_projection()").use { statement ->
                        statement.executeQuery().use { result ->
                            check(result.next())
                            result.getInt(1)
                        }
                    }
                connection.commit()
                count
            } catch (exception: Exception) {
                runCatching { connection.rollback() }
                throw exception
            } finally {
                runCatching { connection.autoCommit = true }
            }
        }

    private fun evaluate(
        token: String,
        idempotencyKey: String,
        requestId: String,
        body: Any,
    ): MvcResult =
        mockMvc
            .post("/api/v1/decisions/evaluate-order") {
                bearer(token)
                header("X-Idempotency-Key", idempotencyKey)
                header("X-Request-Id", requestId)
                contentType = MediaType.APPLICATION_JSON
                content = objectMapper.writeValueAsString(body)
            }.andReturn()

    private fun submitMockOrder(
        token: String,
        idempotencyKey: String,
        requestId: String,
        decisionId: String,
        order: Map<String, Any>,
    ): MvcResult =
        submitRaw(
            token,
            idempotencyKey,
            requestId,
            mapOf(
                "decisionId" to decisionId,
                "orderIntent" to order,
                "userAcknowledgement" to mapOf("warningsAccepted" to true),
            ),
        )

    private fun submitRaw(
        token: String,
        idempotencyKey: String,
        requestId: String,
        body: Any,
    ): MvcResult =
        mockMvc
            .post("/api/v1/brokerage/mock/orders") {
                bearer(token)
                header("X-Idempotency-Key", idempotencyKey)
                header("X-Request-Id", requestId)
                contentType = MediaType.APPLICATION_JSON
                content = objectMapper.writeValueAsString(body)
            }.andReturn()

    private fun cancelOrder(
        token: String,
        idempotencyKey: String,
        requestId: String,
        orderId: String,
    ): MvcResult =
        mockMvc
            .post("/api/v1/brokerage/orders/$orderId/cancel") {
                bearer(token)
                header("X-Idempotency-Key", idempotencyKey)
                header("X-Request-Id", requestId)
                contentType = MediaType.APPLICATION_JSON
                content = "{}"
            }.andReturn()

    private fun brokerageIdempotency(
        action: String,
        sequence: Int,
    ): String = "brokerage-$action-${sequence.toString().padStart(4, '0')}"

    private fun orderIntent(
        orderType: String = "MARKET",
        quantity: Long = 2,
        estimatedPrice: Long = 70_000,
        estimatedAmount: Long = quantity * estimatedPrice,
    ): Map<String, Any> =
        mapOf(
            "symbol" to "005930",
            "side" to "BUY",
            "orderType" to orderType,
            "quantity" to quantity,
            "estimatedPrice" to estimatedPrice,
            "estimatedAmount" to estimatedAmount,
            "timeframe" to "1d",
            "strategyId" to "cash-equity-v1",
        )

    private fun insertPrinciple(
        ownerUserId: String,
        mode: String,
        suffix: String,
    ): String {
        val principleId = "prc_31" + suffix.padStart(30, '0')
        val versionId = "pvr_31" + suffix.padStart(30, '0')
        jdbcTemplate.update(
            """
            insert into principles (
              principle_id, user_id, preset_id, title, mode, status, current_version
            )
            values (?, ?, 'balanced', 'S3.1 fixture', ?, 'ACTIVE', 1)
            """.trimIndent(),
            principleId,
            ownerUserId,
            mode,
        )
        jdbcTemplate.update(
            """
            insert into principle_versions (
              principle_version_id, principle_id, version, preset_id, title,
              mode, status, rules_json, changed_fields, created_by
            )
            select ?, ?, 1, preset_id, 'S3.1 fixture', ?, 'ACTIVE', rules_json,
                   array['presetId','title','mode','status','rules'], ?
            from principle_presets
            where preset_id = 'balanced'
            """.trimIndent(),
            versionId,
            principleId,
            mode,
            ownerUserId,
        )
        return principleId
    }

    private fun insertCompleteStoredSources(
        suffix: String,
        orderCount: Int,
    ) {
        jdbcTemplate.update(
            """
            insert into market_quote_observations (
              observation_id, symbol, source, price_krw, bid_krw, ask_krw,
              completeness, observed_at, received_at, schema_version,
              source_version, payload_json, source_ref, artifact_hash
            ) values (
              ?, '005930', 'KIS_MOCK', 70000, 69900, 70000,
              'COMPLETE', ?::timestamptz, ?::timestamptz,
              'market-quote-observation.v1', 's31-fixture-v1',
              '{"symbol":"005930"}'::jsonb, repeat('1', 64), ?
            )
            """.trimIndent(),
            "quote-s31-$suffix",
            EVALUATION_AT,
            EVALUATION_AT,
            hex(suffix, "2"),
        )
        jdbcTemplate.update(
            """
            insert into instrument_catalog_observations (
              observation_id, symbol, is_etf_etn, is_gold_etf_etn,
              product_risk_score, catalog_version, observed_at, received_at,
              completeness, schema_version, source_version, payload_json,
              source_ref, artifact_hash
            ) values (
              ?, '005930', false, false, null,
              's31-catalog-v1', ?::timestamptz, ?::timestamptz,
              'COMPLETE', 'instrument-catalog-observation.v1', 's31-fixture-v1',
              '{"symbol":"005930"}'::jsonb, repeat('3', 64), ?
            )
            """.trimIndent(),
            "instrument-s31-$suffix",
            EVALUATION_AT,
            EVALUATION_AT,
            hex(suffix, "4"),
        )
        jdbcTemplate.update(
            """
            insert into portfolio_balance_observations (
              observation_id, owner_user_id, account_scope_hash, source,
              context_status, cash_krw, portfolio_equity_krw,
              margin_requirement_krw, completeness, position_count,
              observed_at, received_at, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) values (
              ?, 'usr_demo_user', repeat('c', 64), 'KIS_MOCK',
              'ACTIVE', 10000000, 10000000, 0, 'COMPLETE', 0,
              ?::timestamptz, ?::timestamptz,
              'portfolio-balance-observation.v1', 's31-fixture-v1',
              '{"ownerScopeHash":"sanitized"}'::jsonb,
              repeat('5', 64), ?
            )
            """.trimIndent(),
            "balance-s31-$suffix",
            EVALUATION_AT,
            EVALUATION_AT,
            hex(suffix, "6"),
        )
        jdbcTemplate.update(
            """
            insert into deterministic_risk_observations (
              observation_id, owner_user_id, owner_scope_hash, portfolio_source,
              daily_loss_rate, max_drawdown, annualized_volatility, completeness,
              observed_at, received_at, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) values (
              ?, 'usr_demo_user', repeat('c', 64), 'KIS_MOCK',
              -0.01, -0.05, 0.20, 'COMPLETE',
              ?::timestamptz, ?::timestamptz,
              'deterministic-risk-observation.v1', 's31-fixture-v1',
              '{"ownerScopeHash":"sanitized"}'::jsonb,
              repeat('7', 64), ?
            )
            """.trimIndent(),
            "risk-s31-$suffix",
            EVALUATION_AT,
            EVALUATION_AT,
            hex(suffix, "8"),
        )
        jdbcTemplate.update(
            """
            insert into daily_order_count_observations (
              observation_id, owner_user_id, owner_scope_hash, portfolio_source,
              trading_date, order_count, covered_through, completeness,
              observed_at, received_at, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) values (
              ?, 'usr_demo_user', repeat('c', 64), 'KIS_MOCK',
              '2030-01-02', ?, ?::timestamptz, 'COMPLETE',
              ?::timestamptz, ?::timestamptz,
              'daily-order-count-observation.v1', 's31-fixture-v1',
              '{"ownerScopeHash":"sanitized"}'::jsonb,
              repeat('9', 64), ?
            )
            """.trimIndent(),
            "orders-s31-$suffix",
            orderCount,
            EVALUATION_AT,
            EVALUATION_AT,
            EVALUATION_AT,
            hex(suffix, "a"),
        )
    }

    private fun login(
        username: String,
        password: String,
    ): String {
        val response =
            mockMvc
                .post("/api/v1/auth/login") {
                    contentType = MediaType.APPLICATION_JSON
                    content = objectMapper.writeValueAsString(mapOf("username" to username, "password" to password))
                    header("X-Request-Id", "req-brokerage-login-$username")
                }.andExpect {
                    status { isOk() }
                }.andReturn()
        return json(response).at("/data/accessToken").stringValue()
    }

    private fun count(
        sql: String,
        vararg args: Any,
    ): Int = jdbcTemplate.queryForObject(sql, Int::class.java, *args) ?: 0

    private fun hex(
        suffix: String,
        fill: String,
    ): String = (suffix.filter { it in '0'..'9' || it in 'a'..'f' } + fill.repeat(64)).take(64)

    private fun json(result: MvcResult): JsonNode = objectMapper.readTree(result.response.contentAsString)

    companion object {
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")
        private val redisPasswordValue: String = "r" + "p".repeat(24)
        private val decisionScopeKeyValue: String = "d" + "i".repeat(63)
        private val brokerageScopeKeyValue: String = "b" + "r".repeat(63)
        private const val APP_PASSWORD = "app-test"
        private const val FLYWAY_PASSWORD = "flyway-test"
        private val EVALUATION_AS_OF: Instant = Instant.parse("2030-01-02T03:04:05Z")
        private val EVALUATION_AT: OffsetDateTime = OffsetDateTime.ofInstant(EVALUATION_AS_OF, ZoneOffset.UTC)

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            PostgreSQLContainer(postgresImage)
                .withDatabaseName("decision_s31")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")

        @Container
        @JvmStatic
        val redis: GenericContainer<*> =
            GenericContainer(
                DockerImageName.parse(
                    "redis:7.2-alpine@sha256:dfa18828cbc07b3ae6a95ec7343f6c214fdee2d836197b4be8e9904420762cd8",
                ),
            ).withEnv("REDIS_PASSWORD", redisPasswordValue)
                .withCommand(
                    "redis-server",
                    "--appendonly",
                    "yes",
                    "--maxmemory-policy",
                    "noeviction",
                    "--requirepass",
                    redisPasswordValue,
                ).withExposedPorts(6379)

        @DynamicPropertySource
        @JvmStatic
        fun infrastructureProperties(registry: DynamicPropertyRegistry) {
            registry.add("spring.datasource.url", postgres::getJdbcUrl)
            registry.add("spring.datasource.username") { "decision_app" }
            registry.add("spring.datasource.password") { APP_PASSWORD }
            registry.add("spring.flyway.user") { "flyway" }
            registry.add("spring.flyway.password") { FLYWAY_PASSWORD }
            registry.add("spring.data.redis.host", redis::getHost)
            registry.add("spring.data.redis.port") { redis.getMappedPort(6379) }
            registry.add("spring.data.redis.password") { redisPasswordValue }
            registry.add("app.decision.idempotency-scope-hmac-key") { decisionScopeKeyValue }
            registry.add("app.brokerage.idempotency-scope-hmac-key") { brokerageScopeKeyValue }
        }
    }
}
