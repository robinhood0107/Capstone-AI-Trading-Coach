package com.capstone.decision

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
