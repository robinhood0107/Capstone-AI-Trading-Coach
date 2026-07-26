package com.capstone.decision

import com.capstone.decision.application.risk.KillSwitchActor
import com.capstone.decision.application.risk.KillSwitchMutationCommand
import com.capstone.decision.application.risk.KillSwitchMutationPort
import com.capstone.decision.domain.risk.KillSwitchActorRole
import com.capstone.decision.domain.risk.KillSwitchReasonClass
import io.micrometer.core.instrument.MeterRegistry
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.ValueSource
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.boot.test.context.TestConfiguration
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Import
import org.springframework.context.annotation.Primary
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
import java.math.BigDecimal
import java.sql.DriverManager
import java.time.Clock
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import javax.sql.DataSource

// S2.3 wire, IDOR, durable replay, atomic audit/outbox를 실제 JWT/Redis/PostgreSQL 경계에서 검증한다.
@Testcontainers
@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
@Import(DecisionApiFixedClockConfiguration::class)
class DecisionApiIntegrationTest(
    @Autowired private val webApplicationContext: WebApplicationContext,
    @Autowired private val objectMapper: ObjectMapper,
    @Autowired private val applicationDataSource: DataSource,
    @Autowired private val redisTemplate: StringRedisTemplate,
    @Autowired private val meterRegistry: MeterRegistry,
    @Autowired private val killSwitchMutationPort: KillSwitchMutationPort,
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
        removeFailureTriggers()
        redisTemplate.keys("decision-idempotency:*").takeIf { it.isNotEmpty() }?.let(redisTemplate::delete)
        redisTemplate.keys("idempotency:*").takeIf { it.isNotEmpty() }?.let(redisTemplate::delete)
        jdbcTemplate.update("delete from decision_invalidations")
        jdbcTemplate.update("delete from risk_kill_switch_transitions")
        jdbcTemplate.update("delete from event_outbox where event_type = 'kill-switch.changed'")
        jdbcTemplate.update("delete from audit_logs where target_type = 'KILL_SWITCH'")
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
        jdbcTemplate.update("delete from event_outbox where event_type = 'risk.decision-created.v1'")
        jdbcTemplate.update("delete from audit_logs where target_type = 'DECISION'")
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
        jdbcTemplate.update("delete from principle_versions where principle_id like 'prc_44%'")
        jdbcTemplate.update("delete from principles where principle_id like 'prc_44%'")
        mockMvc =
            MockMvcBuilders
                .webAppContextSetup(webApplicationContext)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    @Test
    fun `Kill Switch public API enforces asymmetric authority exact projection and idempotent replay`() {
        val userToken = login("demo-user", userPassword())
        val activationMetricBefore =
            meterRegistry
                .find("risk.kill_switch.changed")
                .tags(
                    "previous",
                    "false",
                    "next",
                    "true",
                    "reasonClass",
                    "USER_MANUAL_STOP",
                    "actorRole",
                    "USER",
                ).counter()
                ?.count() ?: 0.0
        val activation =
            changeKillSwitch(
                token = userToken,
                idempotencyHeader = "risk-kill-activate-0001",
                requestId = "req-risk-kill-activate",
                active = true,
                reason = "시연 중 안전 정지",
            )

        assertEquals(200, activation.response.status)
        assertEquals(
            setOf("active", "reasonClass", "changedAt"),
            json(activation)
                .at("/data")
                .propertyNames()
                .asSequence()
                .toSet(),
        )
        assertTrue(json(activation).at("/data/active").booleanValue())
        assertEquals("USER_MANUAL_STOP", json(activation).at("/data/reasonClass").stringValue())
        assertEquals(2L, jdbcTemplate.queryForObject("select generation from risk_kill_switch", Long::class.java))
        assertEquals(1, count("select count(*) from risk_kill_switch_transitions"))
        assertEquals(
            activationMetricBefore + 1.0,
            meterRegistry
                .find("risk.kill_switch.changed")
                .tags(
                    "previous",
                    "false",
                    "next",
                    "true",
                    "reasonClass",
                    "USER_MANUAL_STOP",
                    "actorRole",
                    "USER",
                ).counter()
                ?.count(),
        )
        assertEquals(1.0, meterRegistry.find("risk.kill_switch.state").gauge()?.value())
        assertEquals(
            setOf(
                "generation",
                "previousActive",
                "nextActive",
                "reasonClass",
                "changedBy",
                "changedByRole",
                "correlationId",
                "invalidatedDecisionCount",
            ),
            objectMapper
                .readTree(
                    jdbcTemplate.queryForObject(
                        "select payload_json::text from audit_logs where target_type = 'KILL_SWITCH'",
                        String::class.java,
                    ),
                ).propertyNames()
                .asSequence()
                .toSet(),
        )
        val outbox =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    "select payload_json::text from event_outbox where event_type = 'kill-switch.changed'",
                    String::class.java,
                ),
            )
        assertEquals(
            setOf("active", "changedAt"),
            objectMapper
                .readTree(outbox)
                .propertyNames()
                .asSequence()
                .toSet(),
        )
        assertFalse(outbox.contains("usr_demo_user"))
        assertFalse(outbox.contains("시연 중 안전 정지"))

        val replay =
            changeKillSwitch(
                token = userToken,
                idempotencyHeader = "risk-kill-activate-0001",
                requestId = "req-risk-kill-replay",
                active = true,
                reason = "시연 중 안전 정지",
            )
        assertEquals(activation.response.contentAsString, replay.response.contentAsString)
        assertEquals(1, count("select count(*) from risk_kill_switch_transitions"))

        val conflict =
            changeKillSwitch(
                token = userToken,
                idempotencyHeader = "risk-kill-activate-0001",
                requestId = "req-risk-kill-conflict",
                active = false,
                reason = null,
            )
        assertEquals(409, conflict.response.status)
        assertEquals("IDEMPOTENCY_CONFLICT", json(conflict).at("/error/code").stringValue())

        val userResume =
            changeKillSwitch(
                token = userToken,
                idempotencyHeader = "risk-kill-user-resume-01",
                requestId = "req-risk-kill-user-resume",
                active = false,
                reason = null,
            )
        assertEquals(403, userResume.response.status)
        assertEquals("FORBIDDEN", json(userResume).at("/error/code").stringValue())

        val adminToken = login("demo-admin", adminPassword())
        val adminResume =
            changeKillSwitch(
                token = adminToken,
                idempotencyHeader = "risk-kill-admin-resume-1",
                // 추적 ID는 멱등성 키가 아니므로 독립 전이가 같은 값을 재사용해도 상태 변경을 막지 않는다.
                requestId = "req-risk-kill-activate",
                active = false,
                reason = null,
            )
        assertEquals(
            200,
            adminResume.response.status,
            generateSequence<Throwable>(adminResume.resolvedException) { it.cause }
                .joinToString(" <- ") { "${it::class.simpleName}:${it.message}" },
        )
        assertFalse(json(adminResume).at("/data/active").booleanValue())
        assertEquals("ADMIN_RESUME", json(adminResume).at("/data/reasonClass").stringValue())
        assertEquals(3L, jdbcTemplate.queryForObject("select generation from risk_kill_switch", Long::class.java))
        assertEquals(0.0, meterRegistry.find("risk.kill_switch.state").gauge()?.value())

        val noOp =
            changeKillSwitch(
                token = adminToken,
                idempotencyHeader = "risk-kill-admin-noop-001",
                requestId = "req-risk-kill-admin-noop",
                active = false,
                reason = null,
            )
        assertEquals(200, noOp.response.status)
        assertEquals(3L, jdbcTemplate.queryForObject("select generation from risk_kill_switch", Long::class.java))
        assertEquals(2, count("select count(*) from risk_kill_switch_transitions"))
        assertTrue(
            meterRegistry.meters
                .filter { it.id.name.startsWith("risk.") || it.id.name.startsWith("decision.invalidated") }
                .flatMap { it.id.tags }
                .none { tag ->
                    tag.value.contains("usr_demo") ||
                        tag.value.contains("시연 중 안전 정지") ||
                        tag.key in setOf("userId", "decisionId", "requestId", "reason")
                },
        )
    }

    @Test
    fun `Kill Switch resume revalidates current admin status role and security version without writes`() {
        val userToken = login("demo-user", userPassword())
        val activation =
            changeKillSwitch(
                token = userToken,
                idempotencyHeader = "risk-kill-revalidate-on1",
                requestId = "req-risk-kill-revalidate-on",
                active = true,
                reason = null,
            )
        assertEquals(200, activation.response.status)
        val adminToken = login("demo-admin", adminPassword())
        val original =
            jdbcTemplate.queryForMap(
                """
                select role, status, security_version
                from users
                where user_id = 'usr_demo_admin'
                """.trimIndent(),
            )
        val baselineGeneration =
            requireNotNull(jdbcTemplate.queryForObject("select generation from risk_kill_switch", Long::class.java))
        val baselineTransitions = count("select count(*) from risk_kill_switch_transitions")
        val cases =
            listOf(
                Triple("status = 'DISABLED'", 401, "disabled"),
                // 전역 JWT 검증이 stale role claim을 먼저 무효화하고, DB definer는 별도 테스트에서 FORBIDDEN을 고정한다.
                Triple("role = 'USER'", 401, "role"),
                Triple("security_version = security_version + 1", 401, "version"),
            )

        cases.forEachIndexed { index, (mutation, expectedStatus, suffix) ->
            jdbcTemplate.update("update users set $mutation where user_id = 'usr_demo_admin'")
            try {
                val denied =
                    changeKillSwitch(
                        token = adminToken,
                        idempotencyHeader = "risk-kill-revalidate-${index}01",
                        requestId = "req-risk-kill-revalidate-$suffix",
                        active = false,
                        reason = null,
                    )
                assertEquals(expectedStatus, denied.response.status, "$suffix must be rejected")
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
            assertEquals(
                baselineGeneration,
                jdbcTemplate.queryForObject("select generation from risk_kill_switch", Long::class.java),
            )
            assertEquals(baselineTransitions, count("select count(*) from risk_kill_switch_transitions"))
        }
    }

    @Test
    fun `Kill Switch reason injection matrix is rejected without side effects`() {
        val token = login("demo-user", userPassword())
        val invalidReasons =
            listOf(
                "' OR 1=1",
                "-- comment",
                "/* comment */",
                "stop\u0000now",
                "가".repeat(201),
            )

        invalidReasons.forEachIndexed { index, reason ->
            val response =
                mockMvc
                    .post("/api/v1/risk/kill-switch") {
                        bearer(token)
                        header("X-Idempotency-Key", "risk-kill-reason-${index}001")
                        header("X-Request-Id", "req-risk-kill-reason-$index")
                        contentType = MediaType.APPLICATION_JSON
                        content = objectMapper.writeValueAsString(mapOf("active" to true, "reason" to reason))
                    }.andReturn()
            assertEquals(400, response.response.status)
            assertEquals("VALIDATION_ERROR", json(response).at("/error/code").stringValue())
        }

        assertEquals(1L, jdbcTemplate.queryForObject("select generation from risk_kill_switch", Long::class.java))
        assertEquals(0, count("select count(*) from risk_kill_switch_transitions"))
        assertEquals(0, count("select count(*) from audit_logs where target_type = 'KILL_SWITCH'"))
        assertEquals(0, count("select count(*) from event_outbox where event_type = 'kill-switch.changed'"))
    }

    @Test
    fun `Kill Switch accepts schema valid ordinary reason text and discards it before persistence`() {
        val token = login("demo-user", userPassword())
        val response =
            changeKillSwitch(
                token = token,
                idempotencyHeader = "risk-kill-reason-normal",
                requestId = "req-risk-kill-reason-normal",
                active = true,
                reason = "safe and sound",
            )

        assertEquals(200, response.response.status)
        assertEquals("USER_MANUAL_STOP", json(response).at("/data/reasonClass").stringValue())
        assertFalse(response.response.contentAsString.contains("safe and sound"))
        assertFalse(
            requireNotNull(
                jdbcTemplate.queryForObject(
                    "select payload_json::text from audit_logs where target_type = 'KILL_SWITCH'",
                    String::class.java,
                ),
            ).contains("safe and sound"),
        )
    }

    @Test
    fun `Kill Switch validation authentication and idempotency failures make no database writes`() {
        val token = login("demo-user", userPassword())
        val missingKey =
            changeKillSwitch(
                token = token,
                idempotencyHeader = null,
                requestId = "req-risk-kill-no-key",
                active = true,
                reason = null,
            )
        assertEquals(400, missingKey.response.status)
        assertEquals("VALIDATION_ERROR", json(missingKey).at("/error/code").stringValue())

        val injected =
            mockMvc
                .post("/api/v1/risk/kill-switch") {
                    bearer(token)
                    header("X-Idempotency-Key", "risk-kill-injection-001")
                    header("X-Request-Id", "req-risk-kill-injection")
                    contentType = MediaType.APPLICATION_JSON
                    content = """{"active":true,"changedBy":"usr_attacker"}"""
                }.andReturn()
        assertEquals(400, injected.response.status)
        assertEquals("VALIDATION_ERROR", json(injected).at("/error/code").stringValue())
        assertFalse(injected.response.contentAsString.contains("usr_attacker"))

        val unauthenticated =
            mockMvc
                .post("/api/v1/risk/kill-switch") {
                    header("X-Idempotency-Key", "risk-kill-unauth-0001")
                    header("X-Request-Id", "req-risk-kill-unauth")
                    contentType = MediaType.APPLICATION_JSON
                    content = """{"active":true}"""
                }.andReturn()
        assertEquals(401, unauthenticated.response.status)
        assertEquals(1L, jdbcTemplate.queryForObject("select generation from risk_kill_switch", Long::class.java))
        assertEquals(0, count("select count(*) from risk_kill_switch_transitions"))
        assertEquals(0, count("select count(*) from audit_logs where target_type = 'KILL_SWITCH'"))
        assertEquals(0, count("select count(*) from event_outbox where event_type = 'kill-switch.changed'"))
    }

    @Test
    fun `Kill Switch idempotency key uses the canonical alphabet and 16 to 128 bounds`() {
        val token = login("demo-user", userPassword())
        val minimum =
            changeKillSwitch(
                token = token,
                idempotencyHeader = ":".repeat(16),
                requestId = "req-risk-key-minimum",
                active = true,
                reason = null,
            )
        val maximum =
            changeKillSwitch(
                token = token,
                idempotencyHeader = ".".repeat(128),
                requestId = "req-risk-key-maximum",
                active = true,
                reason = null,
            )
        val tooShort =
            changeKillSwitch(
                token = token,
                idempotencyHeader = "-".repeat(15),
                requestId = "req-risk-key-short",
                active = true,
                reason = null,
            )
        val tooLong =
            changeKillSwitch(
                token = token,
                idempotencyHeader = "_".repeat(129),
                requestId = "req-risk-key-long",
                active = true,
                reason = null,
            )

        assertEquals(200, minimum.response.status)
        assertEquals(200, maximum.response.status)
        assertEquals(400, tooShort.response.status)
        assertEquals(400, tooLong.response.status)
        assertEquals(1, count("select count(*) from risk_kill_switch_transitions"))
    }

    @Test
    fun `Kill Switch transaction rollback leaves state history invalidations audit and outbox unchanged`() {
        val token = login("demo-user", userPassword())
        installGraphFailureTrigger("event_outbox")

        val failed =
            changeKillSwitch(
                token = token,
                idempotencyHeader = "risk-kill-rollback-0001",
                requestId = "req-risk-kill-rollback",
                active = true,
                reason = null,
            )

        assertEquals(503, failed.response.status)
        assertEquals("RISK_UNAVAILABLE", json(failed).at("/error/code").stringValue())
        assertEquals(false, jdbcTemplate.queryForObject("select active from risk_kill_switch", Boolean::class.java))
        assertEquals(1L, jdbcTemplate.queryForObject("select generation from risk_kill_switch", Long::class.java))
        assertEquals(0, count("select count(*) from risk_kill_switch_transitions"))
        assertEquals(0, count("select count(*) from decision_invalidations"))
        assertEquals(0, count("select count(*) from audit_logs where target_type = 'KILL_SWITCH'"))
        assertEquals(0, count("select count(*) from event_outbox where event_type = 'kill-switch.changed'"))
    }

    @Test
    fun `concurrent stop and resume serialize to one valid singleton history`() {
        val userToken = login("demo-user", userPassword())
        val adminToken = login("demo-admin", adminPassword())
        val start = CountDownLatch(1)
        val executor = Executors.newFixedThreadPool(2)
        val futures =
            listOf(
                executor.submit<MvcResult> {
                    start.await()
                    changeKillSwitch(
                        userToken,
                        "risk-kill-concurrent-on1",
                        "req-risk-kill-concurrent-on",
                        true,
                        null,
                    )
                },
                executor.submit<MvcResult> {
                    start.await()
                    changeKillSwitch(
                        adminToken,
                        "risk-kill-concurrent-off",
                        "req-risk-kill-concurrent-off",
                        false,
                        null,
                    )
                },
            )
        start.countDown()
        val results = futures.map { it.get(15, TimeUnit.SECONDS) }
        executor.shutdownNow()

        assertEquals(listOf(200, 200), results.map { it.response.status }.sorted())
        val transitionCount = count("select count(*) from risk_kill_switch_transitions")
        val generation = requireNotNull(jdbcTemplate.queryForObject("select generation from risk_kill_switch", Long::class.java))
        assertEquals(transitionCount + 1L, generation)
        assertEquals(
            transitionCount,
            count(
                """
                select count(*) from risk_kill_switch_transitions
                where previous_active <> next_active
                """.trimIndent(),
            ),
        )
        assertEquals(
            transitionCount,
            count("select count(distinct generation) from risk_kill_switch_transitions"),
        )
    }

    @Test
    fun `Kill Switch mutation uses locked database time when caller clock is behind`() {
        val storedFuture = EVALUATION_AT.plusSeconds(1)
        jdbcTemplate.update(
            "update risk_kill_switch set changed_at = ? where kill_switch_id = 'GLOBAL'",
            storedFuture,
        )
        val result =
            killSwitchMutationPort.mutate(
                KillSwitchMutationCommand(
                    actor =
                        KillSwitchActor(
                            userId = "usr_demo_user",
                            role = KillSwitchActorRole.USER,
                            securityVersion = 1,
                            requestId = "req-risk-kill-clock-behind",
                        ),
                    requestedActive = true,
                    reasonClass = KillSwitchReasonClass.USER_MANUAL_STOP,
                ),
            )

        assertTrue(result.changed)
        assertEquals(
            storedFuture,
            jdbcTemplate.queryForObject(
                "select changed_at from risk_kill_switch where kill_switch_id = 'GLOBAL'",
                OffsetDateTime::class.java,
            ),
        )
    }

    @Test
    fun `Kill Switch activation atomically invalidates unused decisions and blocks later evaluations`() {
        val principleId = insertPrinciple("usr_demo_user", "GUIDE", suffix = "91")
        val token = login("demo-user", userPassword())
        val first =
            evaluate(
                token,
                "decision-before-kill-01",
                "req-decision-before-kill",
                request(principleId),
            )
        assertEquals(200, first.response.status)
        val decisionId = json(first).at("/data/decisionId").stringValue()
        val invalidationMetricBefore =
            meterRegistry
                .find("decision.invalidated")
                .tag("reasonClass", "KILL_SWITCH_ACTIVATED")
                .counter()
                ?.count() ?: 0.0

        val activation =
            changeKillSwitch(
                token = token,
                idempotencyHeader = "risk-kill-invalidate-01",
                requestId = "req-risk-kill-invalidate",
                active = true,
                reason = null,
            )
        assertEquals(200, activation.response.status)
        assertEquals(
            1,
            count(
                """
                select count(*) from decision_invalidations
                where decision_id = ? and reason_class = 'KILL_SWITCH_ACTIVATED'
                """.trimIndent(),
                decisionId,
            ),
        )
        assertEquals(
            1,
            objectMapper
                .readTree(
                    jdbcTemplate.queryForObject(
                        "select payload_json::text from audit_logs where target_type = 'KILL_SWITCH'",
                        String::class.java,
                    ),
                ).path("invalidatedDecisionCount")
                .intValue(),
        )
        assertEquals(
            invalidationMetricBefore + 1.0,
            meterRegistry
                .find("decision.invalidated")
                .tag("reasonClass", "KILL_SWITCH_ACTIVATED")
                .counter()
                ?.count(),
        )

        val blocked =
            evaluate(
                token,
                "decision-after-kill-001",
                "req-decision-after-kill",
                request(principleId),
            )
        assertEquals(422, blocked.response.status)
        assertEquals("RISK_BLOCKED", json(blocked).at("/error/code").stringValue())
        assertEquals(1, count("select count(*) from decisions"))
    }

    @Test
    fun `Kill Switch activation cannot miss an in flight decision persistence`() {
        val principleId = insertPrinciple("usr_demo_user", "GUIDE", suffix = "912")
        val token = login("demo-user", userPassword())
        installSlowDecisionTrigger()
        val executor = Executors.newSingleThreadExecutor()
        try {
            val decision =
                executor.submit<MvcResult> {
                    evaluate(
                        token,
                        "decision-kill-race-0001",
                        "req-decision-kill-race",
                        request(principleId),
                    )
                }
            awaitSlowDecisionTriggerEntered()

            val activation =
                changeKillSwitch(
                    token = token,
                    idempotencyHeader = "risk-kill-race-000001",
                    requestId = "req-risk-kill-race",
                    active = true,
                    reason = null,
                )
            val evaluated = decision.get(15, TimeUnit.SECONDS)

            assertEquals(200, evaluated.response.status)
            assertEquals(200, activation.response.status)
            val decisionId = json(evaluated).at("/data/decisionId").stringValue()
            assertEquals(
                1,
                count(
                    """
                    select count(*) from decision_invalidations
                    where decision_id = ? and reason_class = 'KILL_SWITCH_ACTIVATED'
                    """.trimIndent(),
                    decisionId,
                ),
            )
        } finally {
            executor.shutdownNow()
        }
    }

    @Test
    fun `portfolio Risk API keeps absent producers null and reads actual owner observations only`() {
        val token = login("demo-user", userPassword())
        val timerBefore = meterRegistry.find("risk.portfolio.query").timer()?.count() ?: 0L
        val missing =
            mockMvc
                .get("/api/v1/risk/portfolio") {
                    bearer(token)
                    header("X-Request-Id", "req-risk-portfolio-missing")
                }.andReturn()
        assertEquals(200, missing.response.status)
        listOf(
            "portfolioValue",
            "dailyPnlRate",
            "mdd",
            "var95",
            "cvar95",
            "realizedVolatility20d",
            "annualizedVolatility20d",
            "hmmRegime",
            "hmmRegimeProbability",
        ).forEach { field ->
            assertTrue(json(missing).at("/data/$field").isNull, "$field must remain null without a source")
        }
        assertTrue(json(missing).at("/warnings").size() > 0)

        insertCompleteStoredSources(orderCount = 0)
        jdbcTemplate.update(
            """
            update market_quote_observations
            set observed_at = ?::timestamptz,
                received_at = ?::timestamptz
            where observation_id = 'quote-decision-complete'
            """.trimIndent(),
            EVALUATION_AT.minusSeconds(301),
            EVALUATION_AT.minusSeconds(301),
        )
        insertOtherOwnerPortfolioSources()
        val available =
            mockMvc
                .get("/api/v1/risk/portfolio") {
                    bearer(token)
                    header("X-Request-Id", "req-risk-portfolio-available")
                }.andReturn()
        assertEquals(200, available.response.status)
        assertEquals(10_000_000L, json(available).at("/data/portfolioValue").longValue())
        assertEquals(0, json(available).at("/data/dailyPnlRate").decimalValue().compareTo(BigDecimal("-0.01")))
        assertEquals(0, json(available).at("/data/mdd").decimalValue().compareTo(BigDecimal("-0.05")))
        assertEquals(
            0,
            json(available)
                .at("/data/annualizedVolatility20d")
                .decimalValue()
                .compareTo(BigDecimal("0.20")),
        )
        assertTrue(json(available).at("/data/var95").isNull)
        assertFalse(json(available).at("/data/dataFreshness/priceFresh").booleanValue())
        assertFalse(json(available).at("/data/killSwitchActive").booleanValue())
        assertEquals(timerBefore + 2L, meterRegistry.find("risk.portfolio.query").timer()?.count())
    }

    @Test
    fun `complete stored hard sources make the production bean graph ALLOW`() {
        val principleId = insertPrinciple("usr_demo_user", "GUIDE", suffix = "11")
        insertCompleteStoredSources(orderCount = 0)
        val token = login("demo-user", userPassword())

        val response =
            evaluate(
                token,
                "decision-stored-allow",
                "req-decision-stored-allow",
                request(principleId),
            )

        assertEquals(200, response.response.status)
        assertEquals("ALLOW", json(response).at("/data/riskDecision/decision").stringValue())
        assertTrue(json(response).at("/data/riskDecision/canSubmitOrder").booleanValue())
        assertEquals("NONE", json(response).at("/data/enforcementAction").stringValue())
        assertTrue(
            Instant
                .parse(json(response).at("/data/validUntil").stringValue())
                .isAfter(Instant.parse(json(response).at("/data/createdAt").stringValue())),
        )
        assertEquals(1, count("select count(*) from decisions where outcome = 'ALLOW'"))
    }

    @Test
    fun `complete stored warning keeps STRICT reconfirmation semantics`() {
        val principleId = insertPrinciple("usr_demo_user", "STRICT", suffix = "12")
        insertCompleteStoredSources(orderCount = 4)
        val token = login("demo-user", userPassword())

        val response =
            evaluate(
                token,
                "decision-stored-warn",
                "req-decision-stored-warn",
                request(principleId),
            )

        assertEquals(200, response.response.status)
        assertEquals("WARN", json(response).at("/data/riskDecision/decision").stringValue())
        assertTrue(json(response).at("/data/riskDecision/canSubmitOrder").booleanValue())
        assertEquals("RECONFIRM_PRINCIPLE", json(response).at("/data/enforcementAction").stringValue())
    }

    @Test
    fun `complete stored source still BLOCKs an oversized order`() {
        val principleId = insertPrinciple("usr_demo_user", "GUIDE", suffix = "13")
        insertCompleteStoredSources(orderCount = 0)
        val token = login("demo-user", userPassword())
        val oversized =
            request(principleId).toMutableMap().apply {
                this["orderIntent"] =
                    orderIntent().toMutableMap().apply {
                        this["quantity"] = 10
                        this["estimatedAmount"] = 700000
                    }
            }

        val response =
            evaluate(
                token,
                "decision-stored-block",
                "req-decision-stored-block",
                oversized,
            )

        assertEquals(200, response.response.status)
        assertEquals("BLOCK", json(response).at("/data/riskDecision/decision").stringValue())
        assertFalse(json(response).at("/data/riskDecision/canSubmitOrder").booleanValue())
        assertEquals("DO_NOT_SUBMIT", json(response).at("/data/enforcementAction").stringValue())
    }

    @Test
    fun `missing stored portfolio source persists one canonical HOLD and replays it`() {
        val principleId = insertPrinciple("usr_demo_user", "GUIDE", suffix = "01")
        val token = login("demo-user", userPassword())
        val request = request(principleId)
        val key = "decision-replay-0001"
        val timerBefore =
            meterRegistry
                .find("decision.evaluate")
                .tags("outcome", "HOLD", "mode", "GUIDE")
                .timer()
                ?.count() ?: 0
        val counterBefore =
            meterRegistry
                .find("decision.fail_closed")
                .tag("reason", "PORTFOLIO_CONTEXT_UNAVAILABLE")
                .counter()
                ?.count() ?: 0.0

        val first = evaluate(token, key, "req-decision-first", request)
        assertEquals(200, first.response.status)
        val firstData = json(first).at("/data")
        val decisionId = firstData.path("decisionId").stringValue()
        assertTrue(Regex("^dec_[0-9a-f]{32}$").matches(decisionId))
        assertEquals("HOLD", firstData.at("/riskDecision/decision").stringValue())
        assertEquals(false, firstData.at("/riskDecision/canSubmitOrder").booleanValue())
        assertEquals("RE_EVALUATE", firstData.path("enforcementAction").stringValue())
        assertEquals(
            "PORTFOLIO_CONTEXT_UNAVAILABLE",
            firstData.at("/riskDecision/issues/0/code").stringValue(),
        )

        val replay = evaluate(token, key, "req-decision-replay", request)
        assertEquals(200, replay.response.status)
        assertEquals(firstData, json(replay).at("/data"))
        assertEquals(1, count("select count(*) from decisions where decision_id = ?", decisionId))
        assertEquals(7, count("select count(*) from decision_traces where decision_id = ?", decisionId))
        assertEquals(1, count("select count(*) from decision_artifacts where decision_id = ?", decisionId))
        assertEquals(
            1,
            count(
                "select count(*) from audit_logs where target_type = 'DECISION' and target_id = ?",
                decisionId,
            ),
        )
        assertEquals(
            1,
            count(
                "select count(*) from event_outbox where event_type = 'risk.decision-created.v1' and aggregate_id = ?",
                decisionId,
            ),
        )
        assertEquals(1, count("select count(*) from decision_idempotency_results where decision_id = ?", decisionId))
        val scopeHash =
            jdbcTemplate.queryForObject(
                "select scope_hash from decision_idempotency_results where decision_id = ?",
                String::class.java,
                decisionId,
            )
        assertTrue(requireNotNull(scopeHash).matches(Regex("^[0-9a-f]{64}$")))
        assertFalse(scopeHash.contains(key))
        assertFalse(scopeHash.contains("usr_demo_user"))
        assertEquals(
            timerBefore + 2,
            meterRegistry
                .find("decision.evaluate")
                .tags("outcome", "HOLD", "mode", "GUIDE")
                .timer()
                ?.count(),
        )
        assertEquals(
            counterBefore + 1.0,
            meterRegistry
                .find("decision.fail_closed")
                .tag("reason", "PORTFOLIO_CONTEXT_UNAVAILABLE")
                .counter()
                ?.count(),
        )
    }

    @Test
    fun `same idempotency key with a different canonical request conflicts without a second decision`() {
        val principleId = insertPrinciple("usr_demo_user", "GUIDE", suffix = "02")
        val token = login("demo-user", userPassword())
        val key = "decision-conflict-01"

        assertEquals(200, evaluate(token, key, "req-decision-conflict-a", request(principleId)).response.status)
        val changed =
            request(principleId).toMutableMap().apply {
                this["orderIntent"] =
                    orderIntent().toMutableMap().apply {
                        this["quantity"] = 3
                        this["estimatedAmount"] = 210000
                    }
            }
        val conflict = evaluate(token, key, "req-decision-conflict-b", changed)

        assertEquals(409, conflict.response.status)
        assertEquals("IDEMPOTENCY_CONFLICT", json(conflict).at("/error/code").stringValue())
        assertEquals(1, count("select count(*) from decisions"))
        assertEquals(1, count("select count(*) from event_outbox where event_type = 'risk.decision-created.v1'"))
    }

    @Test
    fun `decision detail and audit are owner scoped and expose only the sanitized projection`() {
        assertEquals(
            "decision_app",
            JdbcTemplate(applicationDataSource).queryForObject("select current_user", String::class.java),
        )
        assertEquals(
            "flyway",
            jdbcTemplate.queryForObject(
                "select tableowner from pg_tables where schemaname = 'public' and tablename = 'decisions'",
                String::class.java,
            ),
        )
        val principleId = insertPrinciple("usr_demo_user", "STRICT", suffix = "03")
        val userToken = login("demo-user", userPassword())
        val adminToken = login("demo-admin", adminPassword())
        val created =
            evaluate(
                userToken,
                "decision-owner-0001",
                "req-decision-owner-create",
                request(principleId),
            )
        val createdData = json(created).at("/data")
        val decisionId = createdData.path("decisionId").stringValue()

        val detail =
            mockMvc
                .get("/api/v1/decisions/$decisionId") {
                    bearer(userToken)
                    header("X-Request-Id", "req-decision-owner-detail")
                }.andReturn()
        assertEquals(200, detail.response.status)
        assertEquals(createdData, json(detail).at("/data"))

        val audit =
            mockMvc
                .get("/api/v1/decisions/$decisionId/audit") {
                    bearer(userToken)
                    header("X-Request-Id", "req-decision-owner-audit")
                }.andReturn()
        assertEquals(200, audit.response.status)
        val auditText = audit.response.contentAsString
        assertEquals("DECISION_EVALUATED", json(audit).at("/data/action").stringValue())
        assertEquals(decisionId, json(audit).at("/data/payload/decisionId").stringValue())
        listOf("userId", "accountId", "orderIntent", "sourceRefs", "providerPayload", "token").forEach { forbidden ->
            assertFalse(auditText.contains(forbidden, ignoreCase = true))
        }

        listOf(
            "/api/v1/decisions/$decisionId",
            "/api/v1/decisions/$decisionId/audit",
        ).forEach { path ->
            mockMvc
                .get(path) {
                    bearer(adminToken)
                    header("X-Request-Id", "req-decision-cross-owner")
                }.andExpect {
                    status { isNotFound() }
                    jsonPath("$.error.code") { value("NOT_FOUND") }
                    jsonPath("$.error.details") { isEmpty() }
                }
        }
    }

    @Test
    fun `missing cross-owner inactive Principle share 404 and produce no decision side effects`() {
        val otherOwner = insertPrinciple("usr_demo_admin", "GUIDE", suffix = "04")
        val inactive = insertPrinciple("usr_demo_user", "GUIDE", suffix = "05", status = "ARCHIVED")
        val userToken = login("demo-user", userPassword())
        val missing = "prc_44" + "f".repeat(30)

        listOf(missing, otherOwner, inactive).forEachIndexed { index, principleId ->
            val response =
                evaluate(
                    userToken,
                    "decision-not-found-${index.toString().padStart(2, '0')}",
                    "req-decision-not-found-$index",
                    request(principleId),
                )
            assertEquals(404, response.response.status)
            assertEquals("NOT_FOUND", json(response).at("/error/code").stringValue())
            assertTrue(json(response).at("/error/details").isEmpty)
        }
        assertEquals(0, count("select count(*) from decisions"))
        assertEquals(0, count("select count(*) from audit_logs where target_type = 'DECISION'"))
        assertEquals(0, count("select count(*) from event_outbox where event_type = 'risk.decision-created.v1'"))
        assertEquals(0, count("select count(*) from decision_idempotency_results"))
        assertTrue(redisTemplate.keys("decision-idempotency:*").isEmpty())
    }

    @Test
    fun `forged actor account mode and invalid idempotency headers are rejected before writes`() {
        val principleId = insertPrinciple("usr_demo_user", "GUIDE", suffix = "06")
        val token = login("demo-user", userPassword())
        val forgedNames = listOf("userId", "accountId", "mode", "corpCode", "createdAt")

        forgedNames.forEachIndexed { index, forged ->
            val body = request(principleId).toMutableMap().apply { this[forged] = "forged" }
            val response =
                evaluate(
                    token,
                    "decision-forged-${index.toString().padStart(2, '0')}",
                    "req-decision-forged-$index",
                    body,
                )
            assertEquals(400, response.response.status)
            assertEquals("VALIDATION_ERROR", json(response).at("/error/code").stringValue())
        }
        listOf(null, "short", "contains whitespace 000").forEachIndexed { index, key ->
            val response =
                evaluate(
                    token,
                    key,
                    "req-decision-key-$index",
                    request(principleId),
                )
            assertEquals(400, response.response.status)
            assertEquals("VALIDATION_ERROR", json(response).at("/error/code").stringValue())
        }
        assertEquals(0, count("select count(*) from decisions"))
        assertEquals(0, count("select count(*) from decision_idempotency_results"))
    }

    @Test
    fun `decision request body uses the exact 256 KiB limit`() {
        val token = login("demo-user", userPassword())
        val oversized = """{"padding":"${"x".repeat(262_145)}"}"""

        val result =
            mockMvc
                .post("/api/v1/decisions/evaluate-order") {
                    bearer(token)
                    header("X-Idempotency-Key", "decision-oversize-01")
                    header("X-Request-Id", "req-decision-oversize")
                    contentType = MediaType.APPLICATION_JSON
                    content = oversized
                }.andReturn()

        assertEquals(413, result.response.status)
        assertEquals("PAYLOAD_TOO_LARGE", json(result).at("/error/code").stringValue())
        assertEquals(262_144, json(result).at("/error/details/maxBytes").intValue())
        assertEquals(0, count("select count(*) from decisions"))
    }

    @ParameterizedTest
    @ValueSource(
        strings = [
            "decisions",
            "decision_traces",
            "decision_artifacts",
            "audit_logs",
            "event_outbox",
            "decision_idempotency_results",
        ],
    )
    fun `failure after every reached graph insert rolls back all decision side effects`(targetTable: String) {
        val principleId = insertPrinciple("usr_demo_user", "GUIDE", suffix = "07")
        val token = login("demo-user", userPassword())
        installGraphFailureTrigger(targetTable)

        val response =
            evaluate(
                token,
                "decision-rollback-01",
                "req-decision-rollback",
                request(principleId),
            )

        assertEquals(500, response.response.status)
        assertEquals("INTERNAL_ERROR", json(response).at("/error/code").stringValue())
        assertEquals(0, count("select count(*) from decisions"))
        assertEquals(0, count("select count(*) from decision_traces"))
        assertEquals(0, count("select count(*) from decision_artifacts"))
        assertEquals(0, count("select count(*) from audit_logs where target_type = 'DECISION'"))
        assertEquals(0, count("select count(*) from event_outbox where event_type = 'risk.decision-created.v1'"))
        assertEquals(0, count("select count(*) from decision_idempotency_results"))
    }

    @Test
    fun `violation insert failure rolls back the complete BLOCK graph`() {
        val principleId = insertPrinciple("usr_demo_user", "GUIDE", suffix = "071")
        insertCompleteStoredSources(orderCount = 0)
        val token = login("demo-user", userPassword())
        installGraphFailureTrigger("decision_violations")
        val oversized =
            request(principleId).toMutableMap().apply {
                this["orderIntent"] =
                    orderIntent().toMutableMap().apply {
                        this["quantity"] = 10
                        this["estimatedAmount"] = 700000
                    }
            }

        val response =
            evaluate(
                token,
                "decision-rollback-violation",
                "req-decision-rollback-violation",
                oversized,
            )

        assertEquals(500, response.response.status)
        assertEquals("INTERNAL_ERROR", json(response).at("/error/code").stringValue())
        assertDecisionGraphEmpty()
    }

    @Test
    fun `deferred commit failure cannot return a successful Decision`() {
        val principleId = insertPrinciple("usr_demo_user", "GUIDE", suffix = "072")
        val token = login("demo-user", userPassword())
        installDeferredCommitFailureTrigger()

        val response =
            evaluate(
                token,
                "decision-rollback-commit",
                "req-decision-rollback-commit",
                request(principleId),
            )

        assertEquals(500, response.response.status)
        assertEquals("INTERNAL_ERROR", json(response).at("/error/code").stringValue())
        assertDecisionGraphEmpty()
    }

    @Test
    fun `updater commit first makes pinned decision return 409 with all writes zero`() {
        val principleId = insertPrinciple("usr_demo_user", "GUIDE", suffix = "09")
        insertSecondPrincipleVersion(principleId, "usr_demo_user", "GUIDE", suffix = "09")
        val token = login("demo-user", userPassword())
        val updater = DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password)
        updater.autoCommit = false
        updater
            .prepareStatement(
                "update principles set current_version = 2, updated_at = now() where principle_id = ?",
            ).use { statement ->
                statement.setString(1, principleId)
                assertEquals(1, statement.executeUpdate())
            }

        val executor = Executors.newSingleThreadExecutor()
        try {
            val response =
                executor.submit<MvcResult> {
                    evaluate(
                        token,
                        "decision-updater-first",
                        "req-decision-updater-first",
                        request(principleId),
                    )
                }
            Thread.sleep(250)
            updater.commit()
            val result = response.get(10, TimeUnit.SECONDS)

            assertEquals(409, result.response.status)
            assertEquals("CONFLICT", json(result).at("/error/code").stringValue())
            assertEquals(0, count("select count(*) from decisions"))
            assertEquals(0, count("select count(*) from decision_traces"))
            assertEquals(0, count("select count(*) from decision_artifacts"))
            assertEquals(0, count("select count(*) from audit_logs where target_type = 'DECISION'"))
            assertEquals(0, count("select count(*) from event_outbox where event_type = 'risk.decision-created.v1'"))
            assertEquals(0, count("select count(*) from decision_idempotency_results"))
        } finally {
            updater.close()
            executor.shutdownNow()
        }
    }

    @Test
    fun `decision lock first makes Principle updater wait until decision commit`() {
        val principleId = insertPrinciple("usr_demo_user", "GUIDE", suffix = "10")
        insertSecondPrincipleVersion(principleId, "usr_demo_user", "GUIDE", suffix = "10")
        val token = login("demo-user", userPassword())
        installSlowDecisionTrigger()
        val executor = Executors.newFixedThreadPool(2)
        try {
            val decision =
                executor.submit<MvcResult> {
                    evaluate(
                        token,
                        "decision-lock-first",
                        "req-decision-lock-first",
                        request(principleId),
                    )
                }
            awaitSlowDecisionTriggerEntered()
            val updater =
                executor.submit<Int> {
                    DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
                        connection
                            .prepareStatement(
                                "update principles set current_version = 2, updated_at = now() where principle_id = ?",
                            ).use { statement ->
                                statement.setString(1, principleId)
                                statement.executeUpdate()
                            }
                    }
                }

            Thread.sleep(250)
            assertFalse(updater.isDone, "Principle updater must wait on the Decision FOR SHARE lock")
            assertEquals(200, decision.get(10, TimeUnit.SECONDS).response.status)
            assertEquals(1, updater.get(10, TimeUnit.SECONDS))
            assertEquals(
                1,
                count("select count(*) from decisions where principle_id = ? and principle_version = 1", principleId),
            )
            assertEquals(
                2,
                jdbcTemplate.queryForObject(
                    "select current_version from principles where principle_id = ?",
                    Int::class.java,
                    principleId,
                ),
            )
        } finally {
            executor.shutdownNow()
        }
    }

    @Test
    fun `concurrent duplicate returns in-progress conflict and creates one durable result`() {
        val principleId = insertPrinciple("usr_demo_user", "GUIDE", suffix = "08")
        val token = login("demo-user", userPassword())
        installSlowDecisionTrigger()
        val start = CountDownLatch(1)
        val executor = Executors.newFixedThreadPool(2)
        val futures =
            (0..1).map { index ->
                executor.submit<MvcResult> {
                    start.await()
                    evaluate(
                        token,
                        "decision-concurrent-1",
                        "req-decision-concurrent-$index",
                        request(principleId),
                    )
                }
            }
        start.countDown()
        val results = futures.map { it.get(15, TimeUnit.SECONDS) }
        executor.shutdownNow()

        assertEquals(listOf(200, 409), results.map { it.response.status }.sorted())
        assertTrue(
            results
                .filter { it.response.status == 409 }
                .all { json(it).at("/error/code").stringValue() == "IDEMPOTENCY_IN_PROGRESS" },
        )
        assertEquals(1, count("select count(*) from decisions"))
        assertEquals(1, count("select count(*) from decision_idempotency_results"))
        assertEquals(1, count("select count(*) from event_outbox where event_type = 'risk.decision-created.v1'"))
    }

    private fun evaluate(
        token: String,
        idempotencyKey: String?,
        requestId: String,
        body: Any,
    ): MvcResult =
        mockMvc
            .post("/api/v1/decisions/evaluate-order") {
                bearer(token)
                idempotencyKey?.let { header("X-Idempotency-Key", it) }
                header("X-Request-Id", requestId)
                contentType = MediaType.APPLICATION_JSON
                content = objectMapper.writeValueAsString(body)
            }.andReturn()

    private fun changeKillSwitch(
        token: String,
        idempotencyHeader: String?,
        requestId: String,
        active: Boolean,
        reason: String?,
    ): MvcResult =
        mockMvc
            .post("/api/v1/risk/kill-switch") {
                bearer(token)
                idempotencyHeader?.let { header("X-Idempotency-Key", it) }
                header("X-Request-Id", requestId)
                contentType = MediaType.APPLICATION_JSON
                content =
                    objectMapper.writeValueAsString(
                        buildMap<String, Any> {
                            put("active", active)
                            reason?.let { put("reason", it) }
                        },
                    )
            }.andReturn()

    private fun request(principleId: String): Map<String, Any> =
        mapOf(
            "principleId" to principleId,
            "portfolioSource" to "KIS_MOCK",
            "orderIntent" to orderIntent(),
        )

    private fun orderIntent(): Map<String, Any> =
        mapOf(
            "symbol" to "005930",
            "side" to "BUY",
            "orderType" to "MARKET",
            "quantity" to 2,
            "estimatedPrice" to 70000,
            "estimatedAmount" to 140000,
            "timeframe" to "1d",
            "strategyId" to "cash-equity-v1",
        )

    private fun insertPrinciple(
        ownerUserId: String,
        mode: String,
        suffix: String,
        status: String = "ACTIVE",
    ): String {
        val principleId = "prc_44" + suffix.padStart(30, '0')
        val versionId = "pvr_44" + suffix.padStart(30, '0')
        jdbcTemplate.update(
            """
            insert into principles (
              principle_id, user_id, preset_id, title, mode, status, current_version
            )
            values (?, ?, 'balanced', 'S2.3 fixture', ?, ?, 1)
            """.trimIndent(),
            principleId,
            ownerUserId,
            mode,
            status,
        )
        jdbcTemplate.update(
            """
            insert into principle_versions (
              principle_version_id, principle_id, version, preset_id, title,
              mode, status, rules_json, changed_fields, created_by
            )
            select ?, ?, 1, preset_id, 'S2.3 fixture', ?, ?, rules_json,
                   array['presetId','title','mode','status','rules'], ?
            from principle_presets
            where preset_id = 'balanced'
            """.trimIndent(),
            versionId,
            principleId,
            mode,
            status,
            ownerUserId,
        )
        return principleId
    }

    private fun insertSecondPrincipleVersion(
        principleId: String,
        ownerUserId: String,
        mode: String,
        suffix: String,
    ) {
        val versionId = "pvr_45" + suffix.padStart(30, '0')
        jdbcTemplate.update(
            """
            insert into principle_versions (
              principle_version_id, principle_id, version, preset_id, title,
              mode, status, rules_json, changed_fields, created_by
            )
            select ?, ?, 2, preset_id, 'S2.3 fixture v2', ?, 'ACTIVE', rules_json,
                   array['title'], ?
            from principle_presets
            where preset_id = 'balanced'
            """.trimIndent(),
            versionId,
            principleId,
            mode,
            ownerUserId,
        )
    }

    private fun insertCompleteStoredSources(orderCount: Int) {
        jdbcTemplate.update(
            """
            insert into market_quote_observations (
              observation_id, symbol, source, price_krw, bid_krw, ask_krw,
              completeness, observed_at, received_at, schema_version,
              source_version, payload_json, source_ref, artifact_hash
            ) values (
              'quote-decision-complete', '005930', 'KIS_MOCK', 70000, 69900, 70000,
              'COMPLETE', ?::timestamptz, ?::timestamptz,
              'market-quote-observation.v1', 'decision-fixture-v1',
              '{"symbol":"005930"}'::jsonb, repeat('1', 64), repeat('2', 64)
            )
            """.trimIndent(),
            EVALUATION_AT,
            EVALUATION_AT,
        )
        jdbcTemplate.update(
            """
            insert into instrument_catalog_observations (
              observation_id, symbol, is_etf_etn, is_gold_etf_etn,
              product_risk_score, catalog_version, observed_at, received_at,
              completeness, schema_version, source_version, payload_json,
              source_ref, artifact_hash
            ) values (
              'instrument-decision-complete', '005930', false, false, null,
              'decision-catalog-v1', ?::timestamptz, ?::timestamptz,
              'COMPLETE', 'instrument-catalog-observation.v1', 'decision-fixture-v1',
              '{"symbol":"005930"}'::jsonb, repeat('3', 64), repeat('4', 64)
            )
            """.trimIndent(),
            EVALUATION_AT,
            EVALUATION_AT,
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
              'balance-decision-complete', 'usr_demo_user', repeat('c', 64), 'KIS_MOCK',
              'ACTIVE', 10000000, 10000000, 0, 'COMPLETE', 0,
              ?::timestamptz, ?::timestamptz,
              'portfolio-balance-observation.v1', 'decision-fixture-v1',
              '{"ownerScopeHash":"sanitized"}'::jsonb,
              repeat('5', 64), repeat('6', 64)
            )
            """.trimIndent(),
            EVALUATION_AT,
            EVALUATION_AT,
        )
        jdbcTemplate.update(
            """
            insert into deterministic_risk_observations (
              observation_id, owner_user_id, owner_scope_hash, portfolio_source,
              daily_loss_rate, max_drawdown, annualized_volatility, completeness,
              observed_at, received_at, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) values (
              'risk-decision-complete', 'usr_demo_user', repeat('c', 64), 'KIS_MOCK',
              -0.01, -0.05, 0.20, 'COMPLETE',
              ?::timestamptz, ?::timestamptz,
              'deterministic-risk-observation.v1', 'decision-fixture-v1',
              '{"ownerScopeHash":"sanitized"}'::jsonb,
              repeat('7', 64), repeat('8', 64)
            )
            """.trimIndent(),
            EVALUATION_AT,
            EVALUATION_AT,
        )
        jdbcTemplate.update(
            """
            insert into daily_order_count_observations (
              observation_id, owner_user_id, owner_scope_hash, portfolio_source,
              trading_date, order_count, covered_through, completeness,
              observed_at, received_at, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) values (
              'orders-decision-complete', 'usr_demo_user', repeat('c', 64), 'KIS_MOCK',
              '2030-01-02', ?, ?::timestamptz, 'COMPLETE',
              ?::timestamptz, ?::timestamptz,
              'daily-order-count-observation.v1', 'decision-fixture-v1',
              '{"ownerScopeHash":"sanitized"}'::jsonb,
              repeat('9', 64), repeat('a', 64)
            )
            """.trimIndent(),
            orderCount,
            EVALUATION_AT,
            EVALUATION_AT,
            EVALUATION_AT,
        )
    }

    private fun insertOtherOwnerPortfolioSources() {
        jdbcTemplate.update(
            """
            insert into portfolio_balance_observations (
              observation_id, owner_user_id, account_scope_hash, source,
              context_status, cash_krw, portfolio_equity_krw,
              margin_requirement_krw, completeness, position_count,
              observed_at, received_at, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) values (
              'balance-risk-other-owner', 'usr_demo_admin', repeat('d', 64), 'KIS_MOCK',
              'ACTIVE', 999999999, 999999999, 0, 'COMPLETE', 0,
              ?::timestamptz, ?::timestamptz,
              'portfolio-balance-observation.v1', 'risk-idor-fixture-v1',
              '{"ownerScopeHash":"other"}'::jsonb,
              repeat('b', 64), repeat('c', 64)
            )
            """.trimIndent(),
            EVALUATION_AT,
            EVALUATION_AT,
        )
        jdbcTemplate.update(
            """
            insert into deterministic_risk_observations (
              observation_id, owner_user_id, owner_scope_hash, portfolio_source,
              daily_loss_rate, max_drawdown, annualized_volatility, completeness,
              observed_at, received_at, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) values (
              'risk-other-owner', 'usr_demo_admin', repeat('d', 64), 'KIS_MOCK',
              -0.99, -0.98, 9.9, 'COMPLETE',
              ?::timestamptz, ?::timestamptz,
              'deterministic-risk-observation.v1', 'risk-idor-fixture-v1',
              '{"ownerScopeHash":"other"}'::jsonb,
              repeat('d', 64), repeat('e', 64)
            )
            """.trimIndent(),
            EVALUATION_AT,
            EVALUATION_AT,
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
                    header("X-Request-Id", "req-decision-login-$username")
                }.andExpect {
                    status { isOk() }
                }.andReturn()
        return json(response).at("/data/accessToken").stringValue()
    }

    private fun installGraphFailureTrigger(targetTable: String) {
        require(
            targetTable in
                setOf(
                    "decisions",
                    "decision_violations",
                    "decision_traces",
                    "decision_artifacts",
                    "audit_logs",
                    "event_outbox",
                    "decision_idempotency_results",
                ),
        )
        jdbcTemplate.execute(
            """
            create or replace function s23_test_fail_graph_insert() returns trigger
            language plpgsql as ${'$'}${'$'}
            begin
              raise exception 'synthetic decision graph failure';
            end
            ${'$'}${'$'}
            """.trimIndent(),
        )
        jdbcTemplate.execute(
            """
            create trigger s23_test_fail_graph_insert
            before insert on $targetTable
            for each row execute function s23_test_fail_graph_insert()
            """.trimIndent(),
        )
    }

    private fun installDeferredCommitFailureTrigger() {
        jdbcTemplate.execute(
            """
            create or replace function s23_test_fail_graph_commit() returns trigger
            language plpgsql as ${'$'}${'$'}
            begin
              raise exception 'synthetic deferred decision graph failure';
            end
            ${'$'}${'$'}
            """.trimIndent(),
        )
        jdbcTemplate.execute(
            """
            create constraint trigger s23_test_fail_graph_commit
            after insert on decision_idempotency_results
            deferrable initially deferred
            for each row execute function s23_test_fail_graph_commit()
            """.trimIndent(),
        )
    }

    private fun installSlowDecisionTrigger() {
        jdbcTemplate.execute(
            """
            create or replace function s23_test_slow_decision() returns trigger
            language plpgsql as ${'$'}${'$'}
            begin
              perform pg_advisory_xact_lock($SLOW_DECISION_SIGNAL_LOCK);
              perform pg_sleep(1.0);
              return new;
            end
            ${'$'}${'$'}
            """.trimIndent(),
        )
        jdbcTemplate.execute(
            """
            create trigger s23_test_slow_decision
            before insert on decisions
            for each row execute function s23_test_slow_decision()
            """.trimIndent(),
        )
    }

    private fun awaitSlowDecisionTriggerEntered() {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10)
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.prepareStatement("select pg_try_advisory_lock(?)").use { tryLock ->
                connection.prepareStatement("select pg_advisory_unlock(?)").use { unlock ->
                    tryLock.setLong(1, SLOW_DECISION_SIGNAL_LOCK)
                    unlock.setLong(1, SLOW_DECISION_SIGNAL_LOCK)
                    while (System.nanoTime() < deadline) {
                        val acquired =
                            tryLock.executeQuery().use { result ->
                                check(result.next())
                                result.getBoolean(1)
                            }
                        if (!acquired) {
                            return
                        }
                        unlock.executeQuery().use { result ->
                            check(result.next() && result.getBoolean(1))
                        }
                        Thread.sleep(20)
                    }
                }
            }
        }
        error("Decision insert trigger was not entered within the bounded wait.")
    }

    private fun removeFailureTriggers() {
        listOf(
            "decisions",
            "decision_violations",
            "decision_traces",
            "decision_artifacts",
            "audit_logs",
            "event_outbox",
            "decision_idempotency_results",
        ).forEach { table ->
            jdbcTemplate.execute("drop trigger if exists s23_test_fail_graph_insert on $table")
        }
        jdbcTemplate.execute("drop function if exists s23_test_fail_graph_insert()")
        jdbcTemplate.execute("drop trigger if exists s23_test_slow_decision on decisions")
        jdbcTemplate.execute("drop function if exists s23_test_slow_decision()")
        jdbcTemplate.execute(
            "drop trigger if exists s23_test_fail_graph_commit on decision_idempotency_results",
        )
        jdbcTemplate.execute("drop function if exists s23_test_fail_graph_commit()")
    }

    private fun assertDecisionGraphEmpty() {
        assertEquals(0, count("select count(*) from decisions"))
        assertEquals(0, count("select count(*) from decision_violations"))
        assertEquals(0, count("select count(*) from decision_traces"))
        assertEquals(0, count("select count(*) from decision_artifacts"))
        assertEquals(0, count("select count(*) from audit_logs where target_type = 'DECISION'"))
        assertEquals(0, count("select count(*) from event_outbox where event_type = 'risk.decision-created.v1'"))
        assertEquals(0, count("select count(*) from decision_idempotency_results"))
    }

    private fun count(
        sql: String,
        vararg args: Any,
    ): Int = jdbcTemplate.queryForObject(sql, Int::class.java, *args) ?: 0

    private fun json(result: MvcResult): JsonNode = objectMapper.readTree(result.response.contentAsString)

    companion object {
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")
        private val redisPasswordValue: String = "r" + "p".repeat(24)
        private val decisionScopeKeyValue: String = "d" + "i".repeat(63)
        private const val APP_PASSWORD = "app-test"
        private const val FLYWAY_PASSWORD = "flyway-test"
        private const val SLOW_DECISION_SIGNAL_LOCK = 23_004_401L
        private val EVALUATION_AS_OF: Instant = Instant.parse("2030-01-02T03:04:05Z")
        private val EVALUATION_AT: OffsetDateTime = OffsetDateTime.ofInstant(EVALUATION_AS_OF, ZoneOffset.UTC)

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            PostgreSQLContainer(postgresImage)
                .withDatabaseName("decision_s23")
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
        }
    }
}

@TestConfiguration(proxyBeanMethods = false)
class DecisionApiFixedClockConfiguration {
    @Bean
    @Primary
    fun decisionApiClock(): Clock =
        Clock.fixed(
            Instant.parse("2030-01-02T03:04:05Z"),
            ZoneOffset.UTC,
        )
}
