package com.capstone.decision

import com.capstone.decision.application.brokerage.SubmitMockOrderCommand
import com.capstone.decision.application.brokerage.UserAcknowledgement
import com.capstone.decision.application.risk.KillSwitchActor
import com.capstone.decision.application.risk.KillSwitchMutationCommand
import com.capstone.decision.application.risk.KillSwitchMutationPort
import com.capstone.decision.domain.risk.CanonicalJson
import com.capstone.decision.domain.risk.KillSwitchActorRole
import com.capstone.decision.domain.risk.KillSwitchReasonClass
import com.capstone.decision.infrastructure.brokerage.BrokerageIdempotencyHasher
import com.capstone.decision.infrastructure.brokerage.RedisPaperIdempotencyClaimAdapter
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import com.capstone.decision.infrastructure.security.DemoAccount
import com.capstone.decision.infrastructure.security.DemoRole
import com.capstone.decision.infrastructure.security.JwtService
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
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
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
    @Autowired private val brokerageIdempotencyHasher: BrokerageIdempotencyHasher,
    @Autowired private val jwtService: JwtService,
    @Autowired private val actorCapabilityIssuer: TestActorCapabilityIssuer,
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
    private val appJdbcTemplate: JdbcTemplate by lazy { JdbcTemplate(appDataSource) }

    @BeforeEach
    fun setUp() {
        redisTemplate.keys("brokerage*").takeIf { it.isNotEmpty() }?.let(redisTemplate::delete)
        redisTemplate.keys("decision-idempotency:*").takeIf { it.isNotEmpty() }?.let(redisTemplate::delete)
        redisTemplate.keys("idempotency:*").takeIf { it.isNotEmpty() }?.let(redisTemplate::delete)
        redisTemplate.keys("idempotency-claim:*").takeIf { it.isNotEmpty() }?.let(redisTemplate::delete)
        redisTemplate.keys("idempotency-admission:*").takeIf { it.isNotEmpty() }?.let(redisTemplate::delete)
        jdbcTemplate.update("delete from order_fill_application_receipts")
        jdbcTemplate.update("delete from order_fill_observations")
        jdbcTemplate.update("delete from paper_order_events")
        jdbcTemplate.update("delete from paper_positions")
        jdbcTemplate.update("delete from paper_accounts")
        jdbcTemplate.update("delete from order_events")
        jdbcTemplate.update("delete from orders")
        jdbcTemplate.update("delete from decision_invalidations")
        jdbcTemplate.update("delete from risk_kill_switch_transitions")
        jdbcTemplate.update("delete from event_outbox")
        jdbcTemplate.update(
            "delete from audit_logs where target_type in ('DECISION', 'ORDER', 'ORDER_RECONCILIATION', 'KILL_SWITCH')",
        )
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
    fun `stored mock fills reconcile atomically and expose only owner scoped sanitized history`() {
        val userToken = login("demo-user", userPassword())
        val adminToken = login("demo-admin", adminPassword())
        val decisionId =
            createDecision(
                token = userToken,
                suffix = "33",
                order = orderIntent(),
            )
        val submitted =
            submitMockOrder(
                token = userToken,
                idempotencyKey = "brokerage-fill-submit-0001",
                requestId = "req-brokerage-fill-submit",
                decisionId = decisionId,
                order = orderIntent(),
            )
        assertEquals(200, submitted.response.status, submitted.response.contentAsString)
        val orderId = json(submitted).at("/data/orderId").stringValue()
        val accountId = json(submitted).at("/data/accountId").stringValue()
        insertMockFillObservation(
            orderId = orderId,
            suffix = "1",
            execType = "PARTIAL_FILL",
            fillQuantity = 1,
            cumulativeQuantity = 1,
            leavesQuantity = 1,
            observedAt = EVALUATION_AS_OF.minusSeconds(20),
        )
        insertMockFillObservation(
            orderId = orderId,
            suffix = "2",
            execType = "FILL",
            fillQuantity = 1,
            cumulativeQuantity = 2,
            leavesQuantity = 0,
            observedAt = EVALUATION_AS_OF.minusSeconds(10),
        )
        val reconciled =
            reconcileOrder(
                token = adminToken,
                idempotencyKey = "brokerage-fill-apply-0001",
                requestId = "req-brokerage-fill-apply",
                orderId = orderId,
            )
        assertEquals(200, reconciled.response.status, reconciled.response.contentAsString)
        val projection = json(reconciled).at("/data")
        assertEquals(orderId, projection.path("orderId").stringValue())
        assertEquals("KIS_MOCK", projection.path("brokerageMode").stringValue())
        assertEquals("FILLED", projection.path("status").stringValue())
        assertEquals(2, projection.path("filledQuantity").longValue())
        assertEquals(0, projection.path("leavesQuantity").longValue())
        assertEquals(0, projection.path("unfilledTerminatedQuantity").longValue())
        assertEquals(70_000, projection.path("averageFillPriceKrw").longValue())
        assertEquals("MATCHED", projection.at("/reconciliation/status").stringValue())
        assertTrue(projection.at("/reconciliation/checkedAt").isString)
        assertEquals(2, projection.path("appliedEventCount").intValue())
        assertFalse(projection.path("hasMore").booleanValue())
        assertEquals(
            listOf("MOCK_ORDER_SUBMITTED", "MOCK_ORDER_PARTIALLY_FILLED", "MOCK_ORDER_FILLED"),
            jdbcTemplate.queryForList(
                "select event_type from order_events where order_id = ? order by event_seq",
                String::class.java,
                orderId,
            ),
        )

        val fills =
            getFills(
                token = userToken,
                mode = "mock",
                accountId = accountId,
                from = "2030-01-02",
                to = "2030-01-02",
            )
        assertEquals(200, fills.response.status, fills.response.contentAsString)
        val items = json(fills).at("/data/items")
        assertEquals(2, items.size())
        assertEquals(
            listOf(1L, 1L),
            items
                .values()
                .asSequence()
                .map { it.path("fillQuantity").longValue() }
                .toList(),
        )
        assertTrue(
            items
                .values()
                .asSequence()
                .all { it.path("execRefHash").stringValue().matches(Regex("^[0-9a-f]{64}$")) },
        )
        assertFalse(fills.response.contentAsString.contains("provider-exec-raw"))
        assertTrue(json(fills).at("/data/nextCursor").isNull)

        val replay =
            reconcileOrder(
                token = adminToken,
                idempotencyKey = "brokerage-fill-apply-0001",
                requestId = "req-brokerage-fill-replay",
                orderId = orderId,
            )
        assertEquals(reconciled.response.contentAsString, replay.response.contentAsString)
        assertEquals(3, count("select count(*) from order_events where order_id = ?", orderId))
        assertEquals(1, count("select count(*) from audit_logs where target_type = 'ORDER_RECONCILIATION'"))
    }

    @Test
    fun `demoted admin cannot replay a cached reconciliation response`() {
        val userToken = login("demo-user", userPassword())
        val adminToken = login("demo-admin", adminPassword())
        val decisionId = createDecision(userToken, "39", orderIntent())
        val submitted =
            submitMockOrder(
                token = userToken,
                idempotencyKey = "brokerage-fill-auth-replay-submit",
                requestId = "req-brokerage-fill-auth-replay-submit",
                decisionId = decisionId,
                order = orderIntent(),
            )
        assertEquals(200, submitted.response.status, submitted.response.contentAsString)
        val orderId = json(submitted).at("/data/orderId").stringValue()
        val replayKey = "brokerage-fill-auth-replay-apply"
        val initial =
            reconcileOrder(
                token = adminToken,
                idempotencyKey = replayKey,
                requestId = "req-brokerage-fill-auth-replay-initial",
                orderId = orderId,
            )
        assertEquals(200, initial.response.status, initial.response.contentAsString)
        val eventCountBefore = count("select count(*) from order_events where order_id = ?", orderId)
        val auditCountBefore =
            count(
                "select count(*) from audit_logs where target_type = 'ORDER_RECONCILIATION' and target_id = ?",
                orderId,
            )
        val originalRole =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    "select role from users where user_id = ?",
                    String::class.java,
                    "usr_demo_admin",
                ),
            )
        val originalSecurityVersion =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    "select security_version from users where user_id = ?",
                    Long::class.java,
                    "usr_demo_admin",
                ),
            )

        try {
            val demotedSecurityVersion = Math.addExact(originalSecurityVersion, 1L)
            assertEquals(
                1,
                jdbcTemplate.update(
                    "update users set role = 'USER', security_version = ? where user_id = ?",
                    demotedSecurityVersion,
                    "usr_demo_admin",
                ),
            )
            val demotedSessionHandle = "sid1_" + "d".repeat(64)
            val demotedSessionExpiresAt = OffsetDateTime.now().plusHours(12)
            assertEquals(
                1,
                jdbcTemplate.update(
                    """
                    insert into actor_auth_session(
                      session_hash,actor_user_id,actor_role,actor_security_version,issued_at,expires_at
                    ) values ('sha256:'||encode(digest(?,'sha256'),'hex'),?,?,?,?,?)
                    """.trimIndent(),
                    demotedSessionHandle,
                    "usr_demo_admin",
                    "USER",
                    demotedSecurityVersion,
                    OffsetDateTime.now(),
                    demotedSessionExpiresAt,
                ),
            )
            val demotedToken =
                jwtService
                    .issue(
                        DemoAccount(
                            userId = "usr_demo_admin",
                            username = "demo-admin",
                            role = DemoRole.USER,
                            securityVersion = demotedSecurityVersion,
                            sessionHandle = demotedSessionHandle,
                            expiresAt = demotedSessionExpiresAt,
                        ),
                    ).token

            val replay =
                reconcileOrder(
                    token = demotedToken,
                    idempotencyKey = replayKey,
                    requestId = "req-brokerage-fill-auth-replay-demoted",
                    orderId = orderId,
                )
            assertEquals(403, replay.response.status, replay.response.contentAsString)
            assertEquals("FORBIDDEN", json(replay).at("/error/code").stringValue())

            val control =
                reconcileOrder(
                    token = demotedToken,
                    idempotencyKey = "brokerage-fill-auth-replay-control",
                    requestId = "req-brokerage-fill-auth-replay-control",
                    orderId = orderId,
                )
            assertEquals(403, control.response.status, control.response.contentAsString)
            assertEquals(eventCountBefore, count("select count(*) from order_events where order_id = ?", orderId))
            assertEquals(
                auditCountBefore,
                count(
                    "select count(*) from audit_logs where target_type = 'ORDER_RECONCILIATION' and target_id = ?",
                    orderId,
                ),
            )
        } finally {
            jdbcTemplate.update(
                "update users set role = ?, security_version = ? where user_id = ?",
                originalRole,
                originalSecurityVersion,
                "usr_demo_admin",
            )
        }
    }

    @Test
    fun `future dated complete fill is deferred without current work or state mutation`() {
        val userToken = login("demo-user", userPassword())
        val adminToken = login("demo-admin", adminPassword())
        val decisionId = createDecision(userToken, "3a", orderIntent())
        val submitted =
            submitMockOrder(
                token = userToken,
                idempotencyKey = "brokerage-fill-future-submit",
                requestId = "req-brokerage-fill-future-submit",
                decisionId = decisionId,
                order = orderIntent(),
            )
        assertEquals(200, submitted.response.status, submitted.response.contentAsString)
        val orderId = json(submitted).at("/data/orderId").stringValue()
        insertMockFillObservation(
            orderId = orderId,
            suffix = "d",
            execType = "FILL",
            fillQuantity = 2,
            cumulativeQuantity = 2,
            leavesQuantity = 0,
            observedAt = EVALUATION_AS_OF.plusSeconds(3_600),
        )

        val reconciled =
            reconcileOrder(
                token = adminToken,
                idempotencyKey = "brokerage-fill-future-apply",
                requestId = "req-brokerage-fill-future-apply",
                orderId = orderId,
            )

        assertEquals(200, reconciled.response.status, reconciled.response.contentAsString)
        assertEquals("SUBMITTED", json(reconciled).at("/data/status").stringValue())
        assertEquals(0, json(reconciled).at("/data/filledQuantity").longValue())
        assertEquals("NOT_APPLICABLE", json(reconciled).at("/data/reconciliation/status").stringValue())
        assertTrue(json(reconciled).at("/data/reconciliation/checkedAt").isNull)
        assertEquals(0, json(reconciled).at("/data/appliedEventCount").intValue())
        assertFalse(json(reconciled).at("/data/hasMore").booleanValue())
        assertEquals(0, count("select count(*) from order_fill_application_receipts where order_id = ?", orderId))
        assertEquals(1, count("select count(*) from order_events where order_id = ?", orderId))
        assertEquals(
            "SUBMITTED",
            jdbcTemplate.queryForObject(
                "select status from orders where order_id = ?",
                String::class.java,
                orderId,
            ),
        )
    }

    @Test
    fun `paper reconciliation reuses deterministic fill and user or invalid date requests fail closed`() {
        val userToken = login("demo-user", userPassword())
        val adminToken = login("demo-admin", adminPassword())
        val decisionId =
            createDecision(
                token = userToken,
                suffix = "34",
                order = orderIntent(),
                portfolioSource = "INTERNAL_PAPER",
            )
        val submitted =
            submitPaperOrder(
                token = userToken,
                idempotencyKey = "brokerage-paper-fill-0001",
                requestId = "req-brokerage-paper-fill",
                decisionId = decisionId,
                order = orderIntent(),
            )
        assertEquals(200, submitted.response.status, submitted.response.contentAsString)
        val orderId = json(submitted).at("/data/orderId").stringValue()
        val accountId = json(submitted).at("/data/accountId").stringValue()

        reconcileOrder(
            token = userToken,
            idempotencyKey = "brokerage-fill-user-denied",
            requestId = "req-brokerage-fill-user-denied",
            orderId = orderId,
        ).also { denied ->
            assertEquals(403, denied.response.status, denied.response.contentAsString)
        }

        val reconciled =
            reconcileOrder(
                token = adminToken,
                idempotencyKey = "brokerage-paper-reconcile-0001",
                requestId = "req-brokerage-paper-reconcile",
                orderId = orderId,
            )
        assertEquals(200, reconciled.response.status, reconciled.response.contentAsString)
        assertEquals("MATCHED", json(reconciled).at("/data/reconciliation/status").stringValue())
        assertEquals(0, json(reconciled).at("/data/appliedEventCount").intValue())
        assertEquals(1, count("select count(*) from order_events where order_id = ?", orderId))

        val fills =
            getFills(
                token = userToken,
                mode = "paper",
                accountId = accountId,
                from = "2030-01-02",
                to = "2030-01-02",
            )
        assertEquals(200, fills.response.status, fills.response.contentAsString)
        assertEquals(1, json(fills).at("/data/items").size())
        assertEquals(70_035, json(fills).at("/data/items/0/fillPriceKrw").longValue())

        val invalidRange =
            getFills(
                token = userToken,
                mode = "paper",
                accountId = accountId,
                from = "2030-01-01",
                to = "2030-02-01",
            )
        assertEquals(400, invalidRange.response.status, invalidRange.response.contentAsString)
        assertEquals("VALIDATION_ERROR", json(invalidRange).at("/error/code").stringValue())
    }

    @Test
    fun `two hundred fifty stored fills are losslessly drained and cursor stays bounded and tamper evident`() {
        val userToken = login("demo-user", userPassword())
        val adminToken = login("demo-admin", adminPassword())
        val largeOrder = orderIntent()
        val decisionId =
            createDecision(
                token = userToken,
                suffix = "35",
                order = largeOrder,
            )
        val submitted =
            submitMockOrder(
                token = userToken,
                idempotencyKey = "brokerage-fill-bounded-submit",
                requestId = "req-brokerage-fill-bounded-submit",
                decisionId = decisionId,
                order = largeOrder,
            )
        assertEquals(200, submitted.response.status, submitted.response.contentAsString)
        val orderId = json(submitted).at("/data/orderId").stringValue()
        val accountId = json(submitted).at("/data/accountId").stringValue()
        // bounded consumer만 검증하는 격리 fixture이므로 제출 후 수량 projection을 원자적으로 확장한다.
        jdbcTemplate.update(
            """
            update orders
            set quantity = 250,
                leaves_quantity = 250,
                order_intent_json =
                  jsonb_set(
                    jsonb_set(order_intent_json, '{quantity}', '"250"'::jsonb),
                    '{estimatedAmount}',
                    '"17500000"'::jsonb
                  )
            where order_id = ?
            """.trimIndent(),
            orderId,
        )
        insertMockFillSeries(orderId = orderId, count = 250, fillPriceKrw = 70_000)

        val first =
            reconcileOrder(
                token = adminToken,
                idempotencyKey = "brokerage-fill-bounded-apply-1",
                requestId = "req-brokerage-fill-bounded-apply-1",
                orderId = orderId,
            )
        assertEquals(200, first.response.status, first.response.contentAsString)
        assertEquals(200, json(first).at("/data/appliedEventCount").intValue())
        assertTrue(json(first).at("/data/hasMore").booleanValue())
        assertEquals("MISMATCH", json(first).at("/data/reconciliation/status").stringValue())

        val second =
            reconcileOrder(
                token = adminToken,
                idempotencyKey = "brokerage-fill-bounded-apply-2",
                requestId = "req-brokerage-fill-bounded-apply-2",
                orderId = orderId,
            )
        assertEquals(200, second.response.status, second.response.contentAsString)
        assertEquals(50, json(second).at("/data/appliedEventCount").intValue())
        assertFalse(json(second).at("/data/hasMore").booleanValue())
        assertEquals("FILLED", json(second).at("/data/status").stringValue())
        assertEquals("MATCHED", json(second).at("/data/reconciliation/status").stringValue())
        assertEquals(251, count("select count(*) from order_events where order_id = ?", orderId))
        assertEquals(250, count("select count(*) from order_fill_application_receipts where order_id = ?", orderId))

        val page =
            getFills(
                token = userToken,
                mode = "mock",
                accountId = accountId,
                from = "2030-01-02",
                to = "2030-01-02",
            )
        assertEquals(200, page.response.status, page.response.contentAsString)
        assertEquals(50, json(page).at("/data/items").size())
        val cursor = json(page).at("/data/nextCursor").stringValue()
        assertTrue(cursor.isNotBlank())
        val nextPage =
            getFills(
                token = userToken,
                mode = "mock",
                accountId = accountId,
                from = "2030-01-02",
                to = "2030-01-02",
                cursor = cursor,
            )
        assertEquals(200, nextPage.response.status, nextPage.response.contentAsString)
        assertEquals(50, json(nextPage).at("/data/items").size())

        val tamperedCursor = tamperCursorSignature(cursor)
        assertTrue(tamperedCursor != cursor)
        val tampered =
            getFills(
                token = userToken,
                mode = "mock",
                accountId = accountId,
                from = "2030-01-02",
                to = "2030-01-02",
                cursor = tamperedCursor,
            )
        assertEquals(400, tampered.response.status, tampered.response.contentAsString)
        assertEquals("VALIDATION_ERROR", json(tampered).at("/error/code").stringValue())
    }

    @Test
    fun `concurrent reconciliation serializes without duplicate fill events`() {
        val userToken = login("demo-user", userPassword())
        val adminToken = login("demo-admin", adminPassword())
        val decisionId = createDecision(userToken, "36", orderIntent())
        val submitted =
            submitMockOrder(
                token = userToken,
                idempotencyKey = "brokerage-fill-race-submit",
                requestId = "req-brokerage-fill-race-submit",
                decisionId = decisionId,
                order = orderIntent(),
            )
        assertEquals(200, submitted.response.status, submitted.response.contentAsString)
        val orderId = json(submitted).at("/data/orderId").stringValue()
        insertMockFillObservation(
            orderId,
            "a",
            "PARTIAL_FILL",
            1,
            1,
            1,
            EVALUATION_AS_OF.minusSeconds(20),
        )
        insertMockFillObservation(
            orderId,
            "b",
            "FILL",
            1,
            2,
            0,
            EVALUATION_AS_OF.minusSeconds(10),
        )

        val executor = Executors.newFixedThreadPool(2)
        try {
            val responses =
                listOf("a", "b")
                    .map { suffix ->
                        executor.submit<MvcResult> {
                            reconcileOrder(
                                token = adminToken,
                                idempotencyKey = "brokerage-fill-race-apply-$suffix",
                                requestId = "req-brokerage-fill-race-$suffix",
                                orderId = orderId,
                            )
                        }
                    }.map { it.get(15, TimeUnit.SECONDS) }
            assertEquals(listOf(200, 200), responses.map { it.response.status }.sorted())
            assertEquals(
                listOf(0, 2),
                responses.map { json(it).at("/data/appliedEventCount").intValue() }.sorted(),
            )
        } finally {
            executor.shutdownNow()
        }
        assertEquals(3, count("select count(*) from order_events where order_id = ?", orderId))
        assertEquals(2, count("select count(*) from order_fill_application_receipts where order_id = ?", orderId))
    }

    @Test
    fun `duplicate and out of order observations do not mutate twice and average price floors`() {
        val userToken = login("demo-user", userPassword())
        val adminToken = login("demo-admin", adminPassword())
        val decisionId = createDecision(userToken, "37", orderIntent())
        val submitted =
            submitMockOrder(
                token = userToken,
                idempotencyKey = "brokerage-fill-ordering-submit",
                requestId = "req-brokerage-fill-ordering-submit",
                decisionId = decisionId,
                order = orderIntent(),
            )
        assertEquals(200, submitted.response.status, submitted.response.contentAsString)
        val orderId = json(submitted).at("/data/orderId").stringValue()
        jdbcTemplate.update(
            "update orders set quantity = 10, leaves_quantity = 10 where order_id = ?",
            orderId,
        )
        insertMockFillObservation(
            orderId,
            "1",
            "PARTIAL_FILL",
            4,
            4,
            6,
            EVALUATION_AS_OF.minusSeconds(40),
            fillPriceKrw = 100,
            averageFillPriceKrw = 100,
        )
        insertMockFillObservation(
            orderId,
            "2",
            "PARTIAL_FILL",
            1,
            4,
            6,
            EVALUATION_AS_OF.minusSeconds(30),
            fillPriceKrw = 100,
            averageFillPriceKrw = 100,
        )
        insertMockFillObservation(
            orderId,
            "3",
            "PARTIAL_FILL",
            1,
            3,
            7,
            EVALUATION_AS_OF.minusSeconds(20),
            fillPriceKrw = 100,
            averageFillPriceKrw = 100,
        )
        insertMockFillObservation(
            orderId,
            "4",
            "FILL",
            6,
            10,
            0,
            EVALUATION_AS_OF.minusSeconds(10),
            fillPriceKrw = 101,
            averageFillPriceKrw = 100,
        )

        val reconciled =
            reconcileOrder(
                token = adminToken,
                idempotencyKey = "brokerage-fill-ordering-apply",
                requestId = "req-brokerage-fill-ordering-apply",
                orderId = orderId,
            )
        assertEquals(200, reconciled.response.status, reconciled.response.contentAsString)
        assertEquals("FILLED", json(reconciled).at("/data/status").stringValue())
        assertEquals(100, json(reconciled).at("/data/averageFillPriceKrw").longValue())
        assertEquals(3, json(reconciled).at("/data/appliedEventCount").intValue())
        assertEquals("MISMATCH", json(reconciled).at("/data/reconciliation/status").stringValue())
        assertEquals(
            listOf(
                "MOCK_ORDER_SUBMITTED",
                "MOCK_ORDER_PARTIALLY_FILLED",
                "INVALID_TRANSITION",
                "MOCK_ORDER_FILLED",
            ),
            jdbcTemplate.queryForList(
                "select event_type from order_events where order_id = ? order by event_seq",
                String::class.java,
                orderId,
            ),
        )
        assertEquals(4, count("select count(*) from order_fill_application_receipts where order_id = ?", orderId))
    }

    @Test
    fun `cancel requested mock order accepts a later authoritative full fill`() {
        val userToken = login("demo-user", userPassword())
        val adminToken = login("demo-admin", adminPassword())
        val decisionId = createDecision(userToken, "38", orderIntent())
        val submitted =
            submitMockOrder(
                userToken,
                "brokerage-fill-cancel-race-submit",
                "req-brokerage-fill-cancel-race-submit",
                decisionId,
                orderIntent(),
            )
        assertEquals(200, submitted.response.status, submitted.response.contentAsString)
        val orderId = json(submitted).at("/data/orderId").stringValue()
        val cancelled =
            cancelOrder(
                userToken,
                "brokerage-fill-cancel-race-cancel",
                "req-brokerage-fill-cancel-race-cancel",
                orderId,
            )
        assertEquals("CANCEL_REQUESTED", json(cancelled).at("/data/status").stringValue())
        insertMockFillObservation(
            orderId,
            "c",
            "FILL",
            2,
            2,
            0,
            EVALUATION_AS_OF.minusSeconds(10),
        )

        val reconciled =
            reconcileOrder(
                adminToken,
                "brokerage-fill-cancel-race-apply",
                "req-brokerage-fill-cancel-race-apply",
                orderId,
            )
        assertEquals(200, reconciled.response.status, reconciled.response.contentAsString)
        assertEquals("FILLED", json(reconciled).at("/data/status").stringValue())
        assertEquals("MATCHED", json(reconciled).at("/data/reconciliation/status").stringValue())
        assertEquals(
            listOf("MOCK_ORDER_SUBMITTED", "MOCK_ORDER_CANCEL_REQUESTED", "MOCK_ORDER_FILLED"),
            jdbcTemplate.queryForList(
                "select event_type from order_events where order_id = ? order by event_seq",
                String::class.java,
                orderId,
            ),
        )
    }

    @Test
    fun `cancel requested mock order rejects a partial fill without erasing cancellation intent`() {
        val userToken = login("demo-user", userPassword())
        val adminToken = login("demo-admin", adminPassword())
        val decisionId = createDecision(userToken, "3b", orderIntent())
        val submitted =
            submitMockOrder(
                userToken,
                "brokerage-fill-cancel-partial-submit",
                "req-brokerage-fill-cancel-partial-submit",
                decisionId,
                orderIntent(),
            )
        assertEquals(200, submitted.response.status, submitted.response.contentAsString)
        val orderId = json(submitted).at("/data/orderId").stringValue()
        val cancelled =
            cancelOrder(
                userToken,
                "brokerage-fill-cancel-partial-cancel",
                "req-brokerage-fill-cancel-partial-cancel",
                orderId,
            )
        assertEquals("CANCEL_REQUESTED", json(cancelled).at("/data/status").stringValue())
        insertMockFillObservation(
            orderId = orderId,
            suffix = "e",
            execType = "PARTIAL_FILL",
            fillQuantity = 1,
            cumulativeQuantity = 1,
            leavesQuantity = 1,
            observedAt = EVALUATION_AS_OF.minusSeconds(10),
        )

        val reconciled =
            reconcileOrder(
                adminToken,
                "brokerage-fill-cancel-partial-apply",
                "req-brokerage-fill-cancel-partial-apply",
                orderId,
            )

        assertEquals(200, reconciled.response.status, reconciled.response.contentAsString)
        assertEquals("CANCEL_REQUESTED", json(reconciled).at("/data/status").stringValue())
        assertEquals(0, json(reconciled).at("/data/filledQuantity").longValue())
        assertEquals(2, json(reconciled).at("/data/leavesQuantity").longValue())
        assertTrue(json(reconciled).at("/data/averageFillPriceKrw").isNull)
        assertEquals("MISMATCH", json(reconciled).at("/data/reconciliation/status").stringValue())
        assertEquals(1, json(reconciled).at("/data/appliedEventCount").intValue())
        assertEquals(
            "CANCEL_REQUESTED_PARTIAL_FILL",
            jdbcTemplate.queryForObject(
                """
                select invalid_reason
                from order_fill_application_receipts
                where order_id = ?
                """.trimIndent(),
                String::class.java,
                orderId,
            ),
        )
        assertEquals(
            listOf("MOCK_ORDER_SUBMITTED", "MOCK_ORDER_CANCEL_REQUESTED", "INVALID_TRANSITION"),
            jdbcTemplate.queryForList(
                "select event_type from order_events where order_id = ? order by event_seq",
                String::class.java,
                orderId,
            ),
        )
    }

    @Test
    fun `three unit fills preserve exact notional remainder across reconciliation`() {
        val userToken = login("demo-user", userPassword())
        val adminToken = login("demo-admin", adminPassword())
        val decisionId = createDecision(userToken, "3c", orderIntent())
        val submitted =
            submitMockOrder(
                token = userToken,
                idempotencyKey = "brokerage-fill-notional-submit",
                requestId = "req-brokerage-fill-notional-submit",
                decisionId = decisionId,
                order = orderIntent(),
            )
        assertEquals(200, submitted.response.status, submitted.response.contentAsString)
        val orderId = json(submitted).at("/data/orderId").stringValue()
        jdbcTemplate.update(
            "update orders set quantity = 3, leaves_quantity = 3 where order_id = ?",
            orderId,
        )
        insertMockFillObservation(
            orderId,
            "5",
            "PARTIAL_FILL",
            1,
            1,
            2,
            EVALUATION_AS_OF.minusSeconds(30),
            fillPriceKrw = 1,
            averageFillPriceKrw = 1,
        )
        insertMockFillObservation(
            orderId,
            "6",
            "PARTIAL_FILL",
            1,
            2,
            1,
            EVALUATION_AS_OF.minusSeconds(20),
            fillPriceKrw = 2,
            averageFillPriceKrw = 1,
        )
        insertMockFillObservation(
            orderId,
            "7",
            "FILL",
            1,
            3,
            0,
            EVALUATION_AS_OF.minusSeconds(10),
            fillPriceKrw = 3,
            averageFillPriceKrw = 2,
        )

        val reconciled =
            reconcileOrder(
                token = adminToken,
                idempotencyKey = "brokerage-fill-notional-apply",
                requestId = "req-brokerage-fill-notional-apply",
                orderId = orderId,
            )

        assertEquals(200, reconciled.response.status, reconciled.response.contentAsString)
        assertEquals("FILLED", json(reconciled).at("/data/status").stringValue())
        assertEquals(3, json(reconciled).at("/data/filledQuantity").longValue())
        assertEquals(2, json(reconciled).at("/data/averageFillPriceKrw").longValue())
        assertEquals("MATCHED", json(reconciled).at("/data/reconciliation/status").stringValue())
        assertEquals(3, json(reconciled).at("/data/appliedEventCount").intValue())
    }

    @Test
    fun `paper MARKET order fills atomically and rebuilds to the stored ledger state`() {
        val token = login("demo-user", userPassword())
        val decisionId =
            createDecision(
                token = token,
                suffix = "0a",
                order = orderIntent(),
                portfolioSource = "INTERNAL_PAPER",
            )

        val submitted =
            submitPaperOrder(
                token = token,
                idempotencyKey = "brokerage-paper-0001",
                requestId = "req-brokerage-paper-submit",
                decisionId = decisionId,
                order = orderIntent(),
            )

        assertEquals(200, submitted.response.status, submitted.response.contentAsString)
        val data = json(submitted).at("/data")
        val orderId = data.path("orderId").stringValue()
        val accountId = data.path("accountId").stringValue()
        assertTrue(Regex("^ord_paper_[0-9a-f]{32}$").matches(orderId))
        assertEquals("acct_${"c".repeat(32)}", accountId)
        assertEquals("INTERNAL_PAPER", data.path("brokerageMode").stringValue())
        assertEquals("FILLED", data.path("status").stringValue())
        assertEquals(2, data.at("/fill/quantity").longValue())
        assertEquals(70_035, data.at("/fill/priceKrw").longValue())
        assertEquals(140_070, data.at("/fill/amountKrw").longValue())
        assertEquals("LAST_QUOTE", data.at("/fill/priceBasis").stringValue())
        assertEquals(5, data.at("/fill/slippageBps").intValue())
        assertEquals("NONE_V1", data.at("/fill/feeModel").stringValue())
        assertEquals(1, count("select count(*) from paper_order_events where order_id = ?", orderId))
        assertEquals(1, count("select count(*) from order_events where order_id = ?", orderId))
        assertEquals(1, count("select count(*) from audit_logs where action = 'PAPER_ORDER_FILLED'"))
        assertEquals(
            1,
            count("select count(*) from event_outbox where event_type = 'brokerage.paper-order-filled.v1'"),
        )
        assertEquals(
            9_859_930,
            jdbcTemplate.queryForObject(
                "select cash_balance from paper_accounts where account_id = ?",
                Long::class.java,
                accountId,
            ),
        )
        val storedPosition =
            jdbcTemplate.queryForMap(
                """
                select quantity, average_price, market_value
                from paper_positions
                where account_id = ? and symbol = '005930'
                """.trimIndent(),
                accountId,
            )
        assertEquals(2L, storedPosition["quantity"])
        assertEquals(70_035L, storedPosition["average_price"])
        assertEquals(140_070L, storedPosition["market_value"])

        val replay =
            submitPaperOrder(
                token,
                "brokerage-paper-0001",
                "req-brokerage-paper-replay",
                decisionId,
                orderIntent(),
            )
        assertEquals(200, replay.response.status, replay.response.contentAsString)
        assertEquals(data, json(replay).at("/data"))
        assertEquals(1, count("select count(*) from paper_order_events"))

        val detail =
            mockMvc
                .get("/api/v1/brokerage/orders/$orderId") {
                    bearer(token)
                    header("X-Request-Id", "req-brokerage-paper-detail")
                }.andReturn()
        assertEquals(200, detail.response.status, detail.response.contentAsString)
        assertEquals("INTERNAL_PAPER", json(detail).at("/data/brokerageMode").stringValue())
        assertEquals("FILLED", json(detail).at("/data/status").stringValue())

        val balance =
            mockMvc
                .get("/api/v1/brokerage/paper/accounts/$accountId/balances") {
                    bearer(token)
                    header("X-Request-Id", "req-brokerage-paper-balance")
                }.andReturn()
        assertEquals(200, balance.response.status, balance.response.contentAsString)
        assertEquals(9_859_930, json(balance).at("/data/cashKrw").longValue())
        assertEquals(10_000_000, json(balance).at("/data/totalEquityKrw").longValue())
        assertEquals("LAST_FILL_PRICE_V1", json(balance).at("/data/valuationBasis").stringValue())
        assertEquals(2, json(balance).at("/data/positions/0/quantity").longValue())

        val rebuild =
            appJdbcTemplate.queryForMap(
                """
                select operation_outcome, event_count, rebuilt_cash_krw,
                       stored_cash_krw, positions_match
                from rebuild_paper_state_authorized_v2(?, ?, ?)
                """.trimIndent(),
                capability(
                    "usr_demo_user",
                    "REBUILD_PAPER_STATE",
                    "ACCOUNT",
                    accountId,
                ),
                "usr_demo_user",
                accountId,
            )
        assertEquals("MATCHED", rebuild["operation_outcome"])
        assertEquals(1L, rebuild["event_count"])
        assertEquals(true, rebuild["positions_match"])

        val cancel =
            cancelOrder(
                token,
                brokerageIdempotency("paper-cancel", 1),
                "req-brokerage-paper-cancel",
                orderId,
            )
        assertEquals(409, cancel.response.status)
        assertEquals(1, count("select count(*) from paper_order_events"))

        val adminToken = login("demo-admin", adminPassword())
        val crossOwner =
            mockMvc
                .get("/api/v1/brokerage/paper/accounts/$accountId/balances") {
                    bearer(adminToken)
                    header("X-Request-Id", "req-brokerage-paper-cross-owner")
                }.andReturn()
        assertEquals(404, crossOwner.response.status)
        val missing =
            mockMvc
                .get("/api/v1/brokerage/paper/accounts/acct_${"f".repeat(32)}/balances") {
                    bearer(adminToken)
                    header("X-Request-Id", "req-brokerage-paper-missing")
                }.andReturn()
        assertEquals(404, missing.response.status)
        assertEquals(json(crossOwner).at("/error"), json(missing).at("/error"))
    }

    @Test
    fun `paper uses stored previous close when the latest observation has no current price`() {
        val token = login("demo-user", userPassword())
        val decisionId =
            createDecision(
                token = token,
                suffix = "0b",
                order = orderIntent(),
                portfolioSource = "INTERNAL_PAPER",
            )
        jdbcTemplate.update(
            """
            insert into market_quote_observations (
              observation_id, symbol, source, price_krw, previous_close_krw,
              bid_krw, ask_krw, completeness, observed_at, received_at,
              schema_version, source_version, payload_json, source_ref, artifact_hash
            ) values (
              'quote-s32-fallback', '005930', 'KIS_MOCK', null, 69000,
              68900, 69000, 'COMPLETE', ?::timestamptz,
              (?::timestamptz + interval '1 second'),
              'market-quote-observation.v1', 's32-fallback-v1',
              '{"symbol":"005930","priceKrw":null,"previousCloseKrw":69000}'::jsonb,
              repeat('b', 64), repeat('c', 64)
            )
            """.trimIndent(),
            EVALUATION_AT,
            EVALUATION_AT,
        )

        val submitted =
            submitPaperOrder(
                token,
                "brokerage-paper-fallback-0001",
                "req-brokerage-paper-fallback",
                decisionId,
                orderIntent(),
            )

        assertEquals(200, submitted.response.status, submitted.response.contentAsString)
        assertEquals("PREVIOUS_CLOSE", json(submitted).at("/data/fill/priceBasis").stringValue())
        assertEquals(69_035, json(submitted).at("/data/fill/priceKrw").longValue())
        assertEquals(
            "PREVIOUS_CLOSE",
            jdbcTemplate.queryForObject(
                """
                select payload_json ->> 'priceBasis'
                from paper_order_events
                where order_id = ?
                """.trimIndent(),
                String::class.java,
                json(submitted).at("/data/orderId").stringValue(),
            ),
        )
    }

    @Test
    fun `mock and paper routes reject the opposite Decision source without fallback writes`() {
        val token = login("demo-user", userPassword())
        val mockDecision = createDecision(token, "0c", orderIntent())
        val paperAttempt =
            submitPaperOrder(
                token,
                "brokerage-wrong-source-0001",
                "req-brokerage-wrong-source-paper",
                mockDecision,
                orderIntent(),
            )
        assertEquals(400, paperAttempt.response.status, paperAttempt.response.contentAsString)
        assertEquals(0, count("select count(*) from paper_order_events"))
        assertEquals(0, count("select count(*) from paper_positions"))

        val paperDecision =
            createDecision(
                token,
                "0d",
                orderIntent(),
                portfolioSource = "INTERNAL_PAPER",
            )
        val mockAttempt =
            submitMockOrder(
                token,
                "brokerage-wrong-source-0002",
                "req-brokerage-wrong-source-mock",
                paperDecision,
                orderIntent(),
            )
        assertEquals(400, mockAttempt.response.status, mockAttempt.response.contentAsString)
        assertEquals(0, count("select count(*) from orders"))
        assertEquals(0, count("select count(*) from paper_order_events"))
    }

    @Test
    fun `paper LIMIT은 검증된 tick table이 없으면 원장 write 전에 거부한다`() {
        val token = login("demo-user", userPassword())
        val limitOrder = orderIntent(orderType = "LIMIT")
        val decisionId = createDecision(token, "0d1", limitOrder, portfolioSource = "INTERNAL_PAPER")

        val response =
            submitPaperOrder(
                token,
                "brokerage-paper-limit-0001",
                "req-brokerage-paper-limit",
                decisionId,
                limitOrder,
            )

        assertEquals(400, response.response.status, response.response.contentAsString)
        assertEquals("VALIDATION_ERROR", json(response).at("/error/code").stringValue())
        assertEquals(
            "TICK_TABLE_UNVERIFIED",
            json(response).at("/error/details/violations/0/reason").stringValue(),
        )
        assertEquals(0, count("select count(*) from orders"))
        assertEquals(0, count("select count(*) from paper_order_events"))
    }

    @Test
    fun `저장된 paper ACCEPTED 주문은 공통 cancel route에서 즉시 CANCELLED로 닫힌다`() {
        val token = login("demo-user", userPassword())
        val limitOrder = orderIntent(orderType = "LIMIT", estimatedPrice = 69_900)
        val decisionId = createDecision(token, "0d2", limitOrder, portfolioSource = "INTERNAL_PAPER")
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
        val orderId = "ord_paper_${"d".repeat(32)}"
        jdbcTemplate.update(
            """
            insert into orders (
              order_id, user_id, account_id, account_scope_hash, decision_id,
              decision_evaluation_id, brokerage_mode, idempotency_scope_hash,
              idempotency_owner_scope_hash, request_hash, symbol, side, order_type,
              quantity, submitted_price_krw, status, order_intent_json,
              result_canonical_json, acknowledged_by, acknowledged_at,
              submitted_at, created_at, updated_at
            )
            select
              ?, 'usr_demo_user', 'acct_${"c".repeat(32)}', ?, decision_id,
              evaluation_id, 'INTERNAL_PAPER', repeat('1', 64), repeat('2', 64),
              repeat('3', 64), '005930', 'BUY', 'LIMIT', 2, 69900, 'ACCEPTED',
              cast(? as jsonb),
              ?, 'usr_demo_user', ?::timestamptz, ?::timestamptz,
              ?::timestamptz, ?::timestamptz
            from decisions
            where decision_id = ?
            """.trimIndent(),
            orderId,
            ownerScopeHash,
            objectMapper.writeValueAsString(limitOrder),
            """
            {"orderId":"$orderId","accountId":"acct_${"c".repeat(
                32,
            )}","brokerageMode":"INTERNAL_PAPER","status":"ACCEPTED","submittedAt":"$EVALUATION_AS_OF","fill":null}
            """.trimIndent(),
            EVALUATION_AT,
            EVALUATION_AT,
            EVALUATION_AT,
            EVALUATION_AT,
            decisionId,
        )
        jdbcTemplate.update(
            """
            insert into order_events (
              order_event_id, order_id, event_type, event_status, payload_json, created_at, event_seq
            ) values (
              'oev_${"d".repeat(32)}', ?, 'PAPER_ORDER_ACCEPTED', 'ACCEPTED',
              jsonb_build_object(
                'orderId', ?, 'brokerageMode', 'INTERNAL_PAPER', 'status', 'ACCEPTED'
              ),
              ?::timestamptz, 1
            )
            """.trimIndent(),
            orderId,
            orderId,
            EVALUATION_AT,
        )

        val cancelled =
            cancelOrder(
                token,
                "brokerage-paper-cancel-accepted-0001",
                "req-brokerage-paper-cancel-accepted",
                orderId,
            )

        assertEquals(200, cancelled.response.status, cancelled.response.contentAsString)
        assertEquals("INTERNAL_PAPER", json(cancelled).at("/data/brokerageMode").stringValue())
        assertEquals("CANCELLED", json(cancelled).at("/data/status").stringValue())
        val lifecycle =
            jdbcTemplate.query(
                """
                select event_type, event_status, event_seq
                from order_events
                where order_id = ?
                order by event_seq
                """.trimIndent(),
                { row, _ ->
                    Triple(
                        row.getString("event_type"),
                        row.getString("event_status"),
                        row.getInt("event_seq"),
                    )
                },
                orderId,
            )
        assertEquals(
            listOf(
                Triple("PAPER_ORDER_ACCEPTED", "ACCEPTED", 1),
                Triple("PAPER_ORDER_CANCEL_REQUESTED", "CANCEL_REQUESTED", 2),
                Triple("PAPER_ORDER_CANCELLED", "CANCELLED", 3),
            ),
            lifecycle,
        )
        assertEquals(0, count("select count(*) from paper_order_events"))
        assertEquals(
            1,
            count("select count(*) from event_outbox where event_type = 'brokerage.paper-order-cancelled.v1'"),
        )
    }

    @Test
    fun `paper 진행중 claim은 stable HMAC scope만 노출하고 충돌을 구분한다`() {
        val token = login("demo-user", userPassword())
        val decisionId = createDecision(token, "0e", orderIntent(), portfolioSource = "INTERNAL_PAPER")
        val rawKey = "brokerage-paper-claim-0001"
        val identity =
            brokerageIdempotencyHasher.paperIdentity(
                "usr_demo_user",
                rawKey,
                paperCommand(decisionId, orderIntent()),
            )
        val claimKey = RedisPaperIdempotencyClaimAdapter.CLAIM_PREFIX + identity.scopeHash
        redisTemplate.opsForValue().set(claimKey, "occupied:${identity.requestHash}", Duration.ofSeconds(30))

        val inProgress =
            submitPaperOrder(token, rawKey, "req-brokerage-paper-in-progress", decisionId, orderIntent())

        assertEquals(409, inProgress.response.status, inProgress.response.contentAsString)
        assertEquals("IDEMPOTENCY_IN_PROGRESS", json(inProgress).at("/error/code").stringValue())
        assertFalse(claimKey.contains(rawKey))
        assertFalse(claimKey.contains("usr_demo_user"))
        assertEquals(0, count("select count(*) from orders"))
        redisTemplate.delete(claimKey)

        redisTemplate.opsForValue().set(claimKey, "occupied:${"f".repeat(64)}", Duration.ofSeconds(30))
        val conflict =
            submitPaperOrder(token, rawKey, "req-brokerage-paper-claim-conflict", decisionId, orderIntent())
        assertEquals(409, conflict.response.status, conflict.response.contentAsString)
        assertEquals("IDEMPOTENCY_CONFLICT", json(conflict).at("/error/code").stringValue())
        assertEquals(0, count("select count(*) from orders"))
        redisTemplate.delete(claimKey)
    }

    @Test
    fun `mock과 paper는 같은 raw key를 독립 처리하고 paper claim을 완료 후 반납한다`() {
        val token = login("demo-user", userPassword())
        val rawKey = "brokerage-shared-mode-key-0001"
        val mockDecision = createDecision(token, "0f", orderIntent())
        val mock = submitMockOrder(token, rawKey, "req-brokerage-shared-mock", mockDecision, orderIntent())
        val paperDecision = createDecision(token, "10", orderIntent(), portfolioSource = "INTERNAL_PAPER")
        val paper = submitPaperOrder(token, rawKey, "req-brokerage-shared-paper", paperDecision, orderIntent())

        assertEquals(200, mock.response.status, mock.response.contentAsString)
        assertEquals(200, paper.response.status, paper.response.contentAsString)
        assertEquals(1, count("select count(*) from orders where brokerage_mode = 'KIS_MOCK'"))
        assertEquals(1, count("select count(*) from orders where brokerage_mode = 'INTERNAL_PAPER'"))
        assertTrue(redisTemplate.keys("${RedisPaperIdempotencyClaimAdapter.CLAIM_PREFIX}*").isEmpty())
    }

    @Test
    fun `paper kill switch stale quote absent quote와 expired decision은 원장 write 없이 닫힌다`() {
        val token = login("demo-user", userPassword())
        val staleDecision = createDecision(token, "11", orderIntent(), portfolioSource = "INTERNAL_PAPER")
        jdbcTemplate.update(
            """
            update market_quote_observations
            set observed_at = ?::timestamptz,
                received_at = ?::timestamptz
            where observation_id = 'quote-s31-11'
            """.trimIndent(),
            EVALUATION_AT.minusSeconds(301),
            EVALUATION_AT.minusSeconds(301),
        )
        val stale =
            submitPaperOrder(
                token,
                "brokerage-paper-stale-0001",
                "req-brokerage-paper-stale",
                staleDecision,
                orderIntent(),
            )
        assertEquals(409, stale.response.status, stale.response.contentAsString)
        assertEquals("DATA_STALE", json(stale).at("/error/code").stringValue())

        val absentDecision = createDecision(token, "12", orderIntent(), portfolioSource = "INTERNAL_PAPER")
        jdbcTemplate.update("delete from market_quote_observations")
        val absent =
            submitPaperOrder(
                token,
                "brokerage-paper-absent-0001",
                "req-brokerage-paper-absent",
                absentDecision,
                orderIntent(),
            )
        assertEquals(503, absent.response.status, absent.response.contentAsString)
        assertEquals("BROKERAGE_UNAVAILABLE", json(absent).at("/error/code").stringValue())

        val expiredDecision = createDecision(token, "13", orderIntent(), portfolioSource = "INTERNAL_PAPER")
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
            expiredDecision,
        )
        val expired =
            submitPaperOrder(
                token,
                "brokerage-paper-expired-0001",
                "req-brokerage-paper-expired",
                expiredDecision,
                orderIntent(),
            )
        assertEquals(409, expired.response.status, expired.response.contentAsString)
        assertEquals("DECISION_EXPIRED", json(expired).at("/error/code").stringValue())

        val blockedDecision = createDecision(token, "14", orderIntent(), portfolioSource = "INTERNAL_PAPER")
        asTestActor(actorCapabilityIssuer) {
            killSwitchMutationPort.mutate(
                KillSwitchMutationCommand(
                    actor =
                        KillSwitchActor(
                            userId = "usr_demo_user",
                            role = KillSwitchActorRole.USER,
                            securityVersion = 1,
                            requestId = "req-paper-kill-switch",
                        ),
                    requestedActive = true,
                    reasonClass = KillSwitchReasonClass.USER_MANUAL_STOP,
                ),
            )
        }
        val blocked =
            submitPaperOrder(
                token,
                "brokerage-paper-blocked-0001",
                "req-brokerage-paper-blocked",
                blockedDecision,
                orderIntent(),
            )
        assertEquals(422, blocked.response.status, blocked.response.contentAsString)
        assertEquals("RISK_BLOCKED", json(blocked).at("/error/code").stringValue())
        assertEquals(0, count("select count(*) from orders"))
        assertEquals(0, count("select count(*) from paper_order_events"))
        assertEquals(0, count("select count(*) from paper_positions"))
    }

    @Test
    fun `paper event 20건은 append only chain으로 저장 파생 상태를 정확히 재구성한다`() {
        val token = login("demo-user", userPassword())
        repeat(20) { index ->
            val side = if (index % 2 == 0) "BUY" else "SELL"
            val order = orderIntent(side = side, quantity = 1)
            val suffix = (0x20 + index).toString(16)
            val decisionId = createDecision(token, suffix, order, portfolioSource = "INTERNAL_PAPER")
            val submitted =
                submitPaperOrder(
                    token,
                    "brokerage-paper-chain-${index.toString().padStart(4, '0')}",
                    "req-brokerage-paper-chain-$index",
                    decisionId,
                    order,
                )
            assertEquals(200, submitted.response.status, submitted.response.contentAsString)
            assertEquals("FILLED", json(submitted).at("/data/status").stringValue())
        }

        val accountId = "acct_${"c".repeat(32)}"
        val rebuild =
            appJdbcTemplate.queryForMap(
                """
                select operation_outcome, event_count, positions_match
                from rebuild_paper_state_authorized_v2(?, ?, ?)
                """.trimIndent(),
                capability(
                    "usr_demo_user",
                    "REBUILD_PAPER_STATE",
                    "ACCOUNT",
                    accountId,
                ),
                "usr_demo_user",
                accountId,
            )
        assertEquals("MATCHED", rebuild["operation_outcome"])
        assertEquals(20L, rebuild["event_count"])
        assertEquals(true, rebuild["positions_match"])
        assertEquals(20, count("select count(*) from paper_order_events"))
        assertEquals(20, count("select count(*) from orders where brokerage_mode = 'INTERNAL_PAPER'"))
    }

    @Test
    fun `같은 paper decision 동시 제출은 정확히 한 건만 체결한다`() {
        val token = login("demo-user", userPassword())
        val decisionId = createDecision(token, "15", orderIntent(), portfolioSource = "INTERNAL_PAPER")
        val executor = Executors.newFixedThreadPool(2)
        try {
            val responses =
                listOf("a", "b")
                    .map { suffix ->
                        executor.submit<MvcResult> {
                            submitPaperOrder(
                                token,
                                "brokerage-paper-race-$suffix-0001",
                                "req-brokerage-paper-race-$suffix",
                                decisionId,
                                orderIntent(),
                            )
                        }
                    }.map { it.get(15, TimeUnit.SECONDS) }

            assertEquals(listOf(200, 409), responses.map { it.response.status }.sorted())
            assertEquals(
                listOf("CONFLICT"),
                responses
                    .filter { it.response.status == 409 }
                    .map { json(it).at("/error/code").stringValue() },
            )
        } finally {
            executor.shutdownNow()
        }
        assertEquals(1, count("select count(*) from orders where decision_id = ?", decisionId))
        assertEquals(1, count("select count(*) from paper_order_events"))
    }

    @Test
    fun `paper 잔고 경계 동시 제출은 account lock으로 초과 체결을 막는다`() {
        val token = login("demo-user", userPassword())
        val firstDecision = createDecision(token, "16", orderIntent(), portfolioSource = "INTERNAL_PAPER")
        val secondDecision = createDecision(token, "17", orderIntent(), portfolioSource = "INTERNAL_PAPER")
        jdbcTemplate.update(
            "update paper_accounts set cash_balance = 200000 where account_id = 'acct_${"c".repeat(32)}'",
        )
        val executor = Executors.newFixedThreadPool(2)
        try {
            val responses =
                listOf(firstDecision, secondDecision)
                    .mapIndexed { index, decisionId ->
                        executor.submit<MvcResult> {
                            submitPaperOrder(
                                token,
                                "brokerage-paper-cash-race-${index.toString().padStart(4, '0')}",
                                "req-brokerage-paper-cash-race-$index",
                                decisionId,
                                orderIntent(),
                            )
                        }
                    }.map { it.get(15, TimeUnit.SECONDS) }

            assertEquals(listOf(200, 400), responses.map { it.response.status }.sorted())
        } finally {
            executor.shutdownNow()
        }
        assertEquals(1, count("select count(*) from paper_order_events"))
        assertEquals(
            59_930,
            jdbcTemplate.queryForObject(
                "select cash_balance from paper_accounts where account_id = 'acct_${"c".repeat(32)}'",
                Long::class.java,
            ),
        )
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
        asTestActor(actorCapabilityIssuer) {
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
        }
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
                    from create_mock_order_authorized_v2(?, ?)
                    """.trimIndent(),
                ).use { statement ->
                    statement.setString(
                        1,
                        capability(
                            "usr_demo_user",
                            "CREATE_MOCK_ORDER",
                            "ORDER",
                            "ord_mock_00000000000000000000000000000009",
                            payload,
                        ),
                    )
                    statement.setString(2, payload)
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
    fun `mock provider outcome is owner bound and atomically updates order plus event`() {
        val token = login("demo-user", userPassword())
        val decisionId = createDecision(token, suffix = "3d", order = orderIntent())
        val submitted =
            submitMockOrder(
                token = token,
                idempotencyKey = "brokerage-provider-outcome-0001",
                requestId = "req-brokerage-provider-outcome",
                decisionId = decisionId,
                order = orderIntent(),
            )
        assertEquals(200, submitted.response.status, submitted.response.contentAsString)
        val orderId = json(submitted).at("/data/orderId").stringValue()
        jdbcTemplate.update(
            """
            with test_clock as (
              select clock_timestamp() - interval '1 second' as value
            )
            update orders
            set created_at = test_clock.value,
                acknowledged_at = test_clock.value,
                submitted_at = test_clock.value,
                updated_at = test_clock.value
            from test_clock
            where order_id = ?
            """.trimIndent(),
            orderId,
        )
        val receivedAt =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    "select clock_timestamp()",
                    OffsetDateTime::class.java,
                ),
            )
        val malformedPayloads =
            listOf(
                mapOf<String, Any?>(
                    "actorUserId" to "usr_demo_user",
                    "actorRole" to "USER",
                    "securityVersion" to "not-an-integer",
                    "requestId" to "req-provider-outcome-malformed-1",
                    "orderId" to orderId,
                    "status" to "ACCEPTED",
                    "providerOrderRefHash" to "a".repeat(64),
                    "trId" to "VTTC0012U",
                    "receivedAt" to receivedAt.toString(),
                    "orderEventId" to "oev_3d000000000000000000000000000010",
                ),
                mapOf<String, Any?>(
                    "actorUserId" to "usr_demo_user",
                    "actorRole" to "USER",
                    "securityVersion" to 1,
                    "requestId" to "req-provider-outcome-malformed-2",
                    "orderId" to orderId,
                    "status" to "ACCEPTED",
                    "providerOrderRefHash" to "a".repeat(64),
                    "trId" to "VTTC0012U",
                    "receivedAt" to null,
                    "orderEventId" to "oev_3d000000000000000000000000000011",
                ),
            )
        malformedPayloads.forEach { malformedPayload ->
            appDataSource.connection.use { connection ->
                connection
                    .prepareStatement(
                        """
                        select operation_outcome
                        from record_mock_order_provider_outcome(cast(? as jsonb), ?)
                        """.trimIndent(),
                    ).use { statement ->
                        statement.setString(1, objectMapper.writeValueAsString(malformedPayload))
                        statement.setString(2, TEST_BROKERAGE_DB_CAPABILITY_TOKEN)
                        val denied =
                            org.junit.jupiter.api
                                .assertThrows<SQLException> { statement.executeQuery() }
                        assertEquals("42501", denied.sqlState)
                    }
            }
        }
        val acceptedPayload =
            objectMapper.writeValueAsString(
                mapOf<String, Any?>(
                    "actorUserId" to "usr_demo_user",
                    "actorRole" to "USER",
                    "securityVersion" to 1,
                    "requestId" to "req-provider-outcome-accepted",
                    "orderId" to orderId,
                    "status" to "ACCEPTED",
                    "providerOrderRefHash" to "a".repeat(64),
                    "trId" to "VTTC0012U",
                    "receivedAt" to receivedAt.toString(),
                    "orderEventId" to "oev_3d000000000000000000000000000001",
                ),
            )

        appDataSource.connection.use { connection ->
            connection
                .prepareStatement(
                    """
                    select operation_outcome, status
                    from record_mock_order_provider_outcome_authorized_v2(?, ?)
                    """.trimIndent(),
                ).use { statement ->
                    statement.setString(
                        1,
                        capability(
                            "usr_demo_user",
                            "RECORD_MOCK_PROVIDER_OUTCOME",
                            "ORDER",
                            orderId,
                            acceptedPayload,
                        ),
                    )
                    statement.setString(2, acceptedPayload)
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        assertEquals("APPLIED", result.getString("operation_outcome"))
                        assertEquals("ACCEPTED", result.getString("status"))
                    }
                }
        }
        assertEquals(
            mapOf(
                "status" to "ACCEPTED",
                "provider_order_ref_hash" to "a".repeat(64),
                "provider_tr_id" to "VTTC0012U",
            ),
            jdbcTemplate.queryForMap(
                """
                select status, provider_order_ref_hash, provider_tr_id
                from orders
                where order_id = ?
                """.trimIndent(),
                orderId,
            ),
        )
        assertEquals(
            1,
            count(
                """
                select count(*)
                from order_events
                where order_id = ? and event_type = 'MOCK_ORDER_ACCEPTED'
                """.trimIndent(),
                orderId,
            ),
        )

        val crossOwnerPayload =
            objectMapper.writeValueAsString(
                mapOf<String, Any?>(
                    "actorUserId" to "usr_demo_admin",
                    "actorRole" to "ADMIN",
                    "securityVersion" to 1,
                    "requestId" to "req-provider-outcome-cross-owner",
                    "orderId" to orderId,
                    "status" to "PENDING_RECONCILIATION",
                    "providerOrderRefHash" to null,
                    "trId" to null,
                    "receivedAt" to receivedAt.toString(),
                    "orderEventId" to "oev_3d000000000000000000000000000002",
                ),
            )
        appDataSource.connection.use { connection ->
            connection
                .prepareStatement(
                    """
                    select operation_outcome
                    from record_mock_order_provider_outcome_authorized_v2(?, ?)
                    """.trimIndent(),
                ).use { statement ->
                    statement.setString(
                        1,
                        capability(
                            "usr_demo_admin",
                            "RECORD_MOCK_PROVIDER_OUTCOME",
                            "ORDER",
                            orderId,
                            crossOwnerPayload,
                        ),
                    )
                    statement.setString(2, crossOwnerPayload)
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        assertEquals("ORDER_NOT_FOUND", result.getString("operation_outcome"))
                    }
                }
        }
        assertEquals(2, count("select count(*) from order_events where order_id = ?", orderId))
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
            val denied =
                org.junit.jupiter.api.assertThrows<SQLException> {
                    connection
                        .prepareStatement(
                            "SELECT count(*) FROM read_mock_order_owner_projection(?, ?, ?)",
                        ).use { statement ->
                            statement.setString(1, "usr_demo_admin")
                            statement.setString(2, orderId)
                            statement.setString(3, TEST_BROKERAGE_DB_CAPABILITY_TOKEN)
                            statement.executeQuery()
                        }
                }
            assertEquals("42501", denied.sqlState)
        }
    }

    @Test
    fun `order sink lock wait is bounded and fails closed during Kill Switch activation`() {
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
                val response = responseFuture.get(3, TimeUnit.SECONDS)

                activation.commit()
                committed = true

                assertEquals(503, response.response.status, response.response.contentAsString)
                assertEquals("BROKERAGE_UNAVAILABLE", json(response).at("/error/code").stringValue())
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
        portfolioSource: String = "KIS_MOCK",
    ): String {
        val principleId = insertPrinciple("usr_demo_user", "GUIDE", suffix)
        insertCompleteStoredSources(suffix = suffix, orderCount = 0, portfolioSource = portfolioSource)
        val response =
            evaluate(
                token,
                "decision-s31-$suffix-0001",
                "req-decision-s31-$suffix",
                mapOf(
                    "principleId" to principleId,
                    "portfolioSource" to portfolioSource,
                    "orderIntent" to order,
                ),
            )
        assertEquals(200, response.response.status, response.response.contentAsString)
        assertEquals(
            "ALLOW",
            json(response).at("/data/riskDecision/decision").stringValue(),
            response.response.contentAsString,
        )
        val decisionId = json(response).at("/data/decisionId").stringValue()
        assertEquals(1, visibleOrderableDecisionCount(decisionId), decisionVisibilityDebug(decisionId))
        return decisionId
    }

    private fun decisionVisibilityDebug(decisionId: String): String =
        "debug decision=$decisionId adminDecision=${count("select count(*) from decisions where decision_id = ?", decisionId)} " +
            "adminArtifact=${count("select count(*) from decision_artifacts where decision_id = ?", decisionId)} " +
            "ownerProjection=${visibleDecisionOwnerProjectionCount(decisionId)}"

    private fun visibleOrderableDecisionCount(decisionId: String): Int =
        jdbcTemplate.queryForObject(
            """
            SELECT count(*)
            FROM decisions decision
            JOIN decision_artifacts artifact
              ON artifact.decision_id=decision.decision_id
             AND artifact.evaluation_id=decision.evaluation_id
            JOIN users actor ON actor.user_id=decision.user_id AND actor.status='ACTIVE'
            WHERE decision.user_id=? AND decision.decision_id=?
            """.trimIndent(),
            Int::class.java,
            "usr_demo_user",
            decisionId,
        ) ?: 0

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

    private fun submitPaperOrder(
        token: String,
        idempotencyKey: String,
        requestId: String,
        decisionId: String,
        order: Map<String, Any>,
    ): MvcResult =
        mockMvc
            .post("/api/v1/brokerage/paper/orders") {
                bearer(token)
                header("X-Idempotency-Key", idempotencyKey)
                header("X-Request-Id", requestId)
                contentType = MediaType.APPLICATION_JSON
                content =
                    objectMapper.writeValueAsString(
                        mapOf(
                            "decisionId" to decisionId,
                            "orderIntent" to order,
                            "userAcknowledgement" to mapOf("warningsAccepted" to true),
                        ),
                    )
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

    private fun reconcileOrder(
        token: String,
        idempotencyKey: String,
        requestId: String,
        orderId: String,
    ): MvcResult =
        mockMvc
            .post("/api/v1/brokerage/orders/$orderId/reconcile") {
                bearer(token)
                header("X-Idempotency-Key", idempotencyKey)
                header("X-Request-Id", requestId)
                contentType = MediaType.APPLICATION_JSON
                content = "{}"
            }.andReturn()

    private fun getFills(
        token: String,
        mode: String,
        accountId: String,
        from: String,
        to: String,
        cursor: String? = null,
    ): MvcResult =
        mockMvc
            .get("/api/v1/brokerage/$mode/accounts/$accountId/fills") {
                bearer(token)
                param("from", from)
                param("to", to)
                cursor?.let { param("cursor", it) }
                header("X-Request-Id", "req-brokerage-$mode-fills")
            }.andReturn()

    private fun insertMockFillObservation(
        orderId: String,
        suffix: String,
        execType: String,
        fillQuantity: Long,
        cumulativeQuantity: Long,
        leavesQuantity: Long,
        observedAt: Instant,
        fillPriceKrw: Long = 70_000,
        averageFillPriceKrw: Long = fillPriceKrw,
    ) {
        jdbcTemplate.update(
            """
            insert into order_fill_observations (
              observation_id, order_id, provider_exec_ref_hash, exec_type,
              fill_quantity, fill_price_krw, cumulative_quantity, leaves_quantity,
              average_fill_price_krw, observed_at, received_at, schema_version,
              source_version, source_ref, completeness, artifact_hash
            ) values (
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::timestamptz,
              ?::timestamptz, '1', 's3.3-fill-observation-v1',
              'offline-s33-fixture', 'COMPLETE', ?
            )
            """.trimIndent(),
            "ofo_${suffix.repeat(32)}",
            orderId,
            suffix.repeat(64),
            execType,
            fillQuantity,
            fillPriceKrw,
            cumulativeQuantity,
            leavesQuantity,
            averageFillPriceKrw,
            OffsetDateTime.ofInstant(observedAt, ZoneOffset.UTC),
            OffsetDateTime.ofInstant(observedAt.plusSeconds(1), ZoneOffset.UTC),
            hex(suffix, "f"),
        )
    }

    private fun insertMockFillSeries(
        orderId: String,
        count: Int,
        fillPriceKrw: Long,
    ) {
        require(count in 1..250)
        repeat(count) { zeroBased ->
            val sequence = zeroBased + 1
            val suffix = sequence.toString(16).padStart(32, '0')
            jdbcTemplate.update(
                """
                insert into order_fill_observations (
                  observation_id, order_id, provider_exec_ref_hash, exec_type,
                  fill_quantity, fill_price_krw, cumulative_quantity, leaves_quantity,
                  average_fill_price_krw, observed_at, received_at, schema_version,
                  source_version, source_ref, completeness, artifact_hash
                ) values (
                  ?, ?, ?, ?, 1, ?, ?, ?, ?, ?::timestamptz,
                  ?::timestamptz, '1', 's3.3-fill-observation-v1',
                  'offline-s33-bounded', 'COMPLETE', ?
                )
                """.trimIndent(),
                "ofo_$suffix",
                orderId,
                sequence.toString(16).padStart(64, '0'),
                if (sequence == count) "FILL" else "PARTIAL_FILL",
                fillPriceKrw,
                sequence,
                count - sequence,
                fillPriceKrw,
                OffsetDateTime.ofInstant(
                    EVALUATION_AS_OF.minusSeconds((count - sequence + 1).toLong()),
                    ZoneOffset.UTC,
                ),
                OffsetDateTime.ofInstant(
                    EVALUATION_AS_OF.minusSeconds((count - sequence).toLong()),
                    ZoneOffset.UTC,
                ),
                (sequence + count).toString(16).padStart(64, '0'),
            )
        }
    }

    private fun brokerageIdempotency(
        action: String,
        sequence: Int,
    ): String = "brokerage-$action-${sequence.toString().padStart(4, '0')}"

    private fun orderIntent(
        side: String = "BUY",
        orderType: String = "MARKET",
        quantity: Long = 2,
        estimatedPrice: Long = 70_000,
        estimatedAmount: Long = quantity * estimatedPrice,
    ): Map<String, Any> =
        mapOf(
            "symbol" to "005930",
            "side" to side,
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
        portfolioSource: String = "KIS_MOCK",
    ) {
        val ownerScopeHash =
            if (portfolioSource == "INTERNAL_PAPER") {
                CanonicalJson.sha256(
                    CanonicalJson.encode(
                        mapOf(
                            "actorUserId" to "usr_demo_user",
                            "paperAccountId" to "acct_${"c".repeat(32)}",
                            "purpose" to "s2.3-paper-owner-scope-v1",
                        ),
                    ),
                )
            } else {
                "c".repeat(64)
            }
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
        if (portfolioSource == "KIS_MOCK") {
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
        } else {
            jdbcTemplate.update(
                """
                insert into paper_accounts (
                  account_id, user_id, name, cash_balance, currency, status,
                  created_at, updated_at, owner_scope_hash, margin_requirement_krw
                ) values (
                  ?, 'usr_demo_user', 'S3.2 fixture', 10000000, 'KRW', 'ACTIVE',
                  ?::timestamptz, ?::timestamptz, ?, 0
                )
                on conflict (account_id) do nothing
                """.trimIndent(),
                "acct_${"c".repeat(32)}",
                EVALUATION_AT,
                EVALUATION_AT,
                ownerScopeHash,
            )
        }
        jdbcTemplate.update(
            """
            insert into deterministic_risk_observations (
              observation_id, owner_user_id, owner_scope_hash, portfolio_source,
              daily_loss_rate, max_drawdown, annualized_volatility, completeness,
              observed_at, received_at, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) values (
              ?, 'usr_demo_user', ?, ?,
              -0.01, -0.05, 0.20, 'COMPLETE',
              ?::timestamptz, ?::timestamptz,
              'deterministic-risk-observation.v1', 's31-fixture-v1',
              '{"ownerScopeHash":"sanitized"}'::jsonb,
              repeat('7', 64), ?
            )
            """.trimIndent(),
            "risk-s31-$suffix",
            ownerScopeHash,
            portfolioSource,
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
              ?, 'usr_demo_user', ?, ?,
              '2030-01-02', ?, ?::timestamptz, 'COMPLETE',
              ?::timestamptz, ?::timestamptz,
              'daily-order-count-observation.v1', 's31-fixture-v1',
              '{"ownerScopeHash":"sanitized"}'::jsonb,
              repeat('9', 64), ?
            )
            """.trimIndent(),
            "orders-s31-$suffix",
            ownerScopeHash,
            portfolioSource,
            orderCount,
            EVALUATION_AT,
            EVALUATION_AT,
            EVALUATION_AT,
            hex(suffix, "a"),
        )
    }

    private fun paperCommand(
        decisionId: String,
        order: Map<String, Any>,
    ): SubmitMockOrderCommand =
        SubmitMockOrderCommand(
            decisionId = decisionId,
            orderIntent =
                com.capstone.decision.domain.risk.OrderIntentSnapshot(
                    symbol = order.getValue("symbol") as String,
                    side = order.getValue("side") as String,
                    orderType = order.getValue("orderType") as String,
                    quantity = order.getValue("quantity") as Long,
                    estimatedPrice = order.getValue("estimatedPrice") as Long,
                    estimatedAmount = order.getValue("estimatedAmount") as Long,
                    timeframe = order.getValue("timeframe") as String,
                    strategyId = order.getValue("strategyId") as String,
                ),
            userAcknowledgement = UserAcknowledgement(warningsAccepted = true),
        )

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

    private fun capability(
        actorUserId: String,
        operation: String,
        targetKind: String,
        targetId: String,
        payloadJson: String? = null,
        admin: Boolean = false,
    ): String =
        actorCapabilityIssuer.issue(
            actorCapabilityIssuer.actorRef(actorUserId),
            if (payloadJson == null) {
                ActorCapabilityBinding.target(
                    operation,
                    targetKind,
                    targetId,
                    if (admin) ActorCapabilityRolePolicy.ADMIN_ONLY else ActorCapabilityRolePolicy.OWNER,
                )
            } else {
                ActorCapabilityBinding.request(
                    operation,
                    targetKind,
                    targetId,
                    if (admin) ActorCapabilityRolePolicy.ADMIN_ONLY else ActorCapabilityRolePolicy.OWNER,
                    payloadJson,
                )
            },
        )

    private fun hex(
        suffix: String,
        fill: String,
    ): String = (suffix.filter { it in '0'..'9' || it in 'a'..'f' } + fill.repeat(64)).take(64)

    private fun tamperCursorSignature(cursor: String): String = cursor.dropLast(1) + if (cursor.last() == 'A') "Q" else "A"

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
            stablePostgresContainer(postgresImage)
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
