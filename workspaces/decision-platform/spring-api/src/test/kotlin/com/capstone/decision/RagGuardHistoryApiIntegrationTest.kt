package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.dao.DataAccessException
import org.springframework.http.MediaType
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.jdbc.datasource.DriverManagerDataSource
import org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.MvcResult
import org.springframework.test.web.servlet.delete
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
import java.sql.Connection
import java.sql.DriverManager
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CyclicBarrier
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import javax.sql.DataSource

@Testcontainers
@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
class RagGuardHistoryApiIntegrationTest(
    @Autowired private val webApplicationContext: WebApplicationContext,
    @Autowired private val objectMapper: ObjectMapper,
    @Autowired private val applicationDataSource: DataSource,
) : SpringApiIntegrationTestBase() {
    private lateinit var mockMvc: MockMvc
    private val ownerJdbc: JdbcTemplate by lazy {
        JdbcTemplate(
            DriverManagerDataSource(
                postgres.jdbcUrl,
                postgres.username,
                postgres.password,
            ),
        )
    }
    private val appJdbc: JdbcTemplate by lazy { JdbcTemplate(applicationDataSource) }

    @BeforeEach
    fun setUp() {
        mockMvc =
            MockMvcBuilders
                .webAppContextSetup(webApplicationContext)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    @Test
    fun `fixture ask persists ciphertext replays once and keeps history owner scoped`() {
        val userToken = login("demo-user", userPassword())
        val adminToken = login("demo-admin", adminPassword())
        val idempotencyKey = "idem-rag-history-api-0001"
        val question = "VaR와 ES의 차이를 공개 근거 범위에서 설명해 주세요"
        val requestBody =
            """
            {
              "question":"$question",
              "answerMode":"CONCISE",
              "topics":["RISK"]
            }
            """.trimIndent()

        val first = ask(userToken, idempotencyKey, requestBody, "req-rag-history-first")
        val answerId = json(first).at("/data/answerId").stringValue()
        assertTrue(answerId.matches(Regex("^rag_ans_[0-9a-f]{32}$")))
        assertEquals("RETRIEVAL_ONLY", json(first).at("/data/generationStatus").stringValue())
        assertTrue(json(first).at("/data/answer").isNull)
        assertEquals(0, json(first).at("/data/citations").size())
        assertFalse(first.response.contentAsString.contains("provider", ignoreCase = true))
        assertFalse(first.response.contentAsString.contains("profile", ignoreCase = true))

        val replay = ask(userToken, idempotencyKey, requestBody, "req-rag-history-replay")
        assertEquals(answerId, json(replay).at("/data/answerId").stringValue())
        assertNotEquals(
            json(first).at("/data/requestId").stringValue(),
            json(replay).at("/data/requestId").stringValue(),
        )

        mockMvc
            .post("/api/v1/rag/ask") {
                bearer(userToken)
                header("X-Idempotency-Key", idempotencyKey)
                header("X-Request-Id", "req-rag-history-conflict")
                contentType = MediaType.APPLICATION_JSON
                content = requestBody.replace("CONCISE", "DETAILED")
            }.andExpect {
                status { isConflict() }
                jsonPath("$.error.code") { value("IDEMPOTENCY_CONFLICT") }
            }

        val encrypted =
            ownerJdbc.queryForMap(
                """
                select encode(question_ciphertext, 'hex') as question_ciphertext,
                       encode(answer_ciphertext, 'hex') as answer_ciphertext,
                       scope_hmac, request_fingerprint
                from rag_answer_history
                join rag_answer_claims using (answer_id)
                where answer_id = ?
                """.trimIndent(),
                answerId,
            )
        assertFalse(encrypted.values.joinToString("|").contains(question))
        assertTrue((encrypted["scope_hmac"] as String).matches(Regex("^[0-9a-f]{64}$")))
        assertTrue((encrypted["request_fingerprint"] as String).matches(Regex("^[0-9a-f]{64}$")))

        val list =
            mockMvc
                .get("/api/v1/rag/history?limit=20") {
                    bearer(userToken)
                    header("X-Request-Id", "req-rag-history-list")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.data.items[0].answerId") { value(answerId) }
                }.andReturn()
        assertEquals(
            setOf("answerId", "createdAt", "expiresAt", "answerMode", "generationStatus", "helpful"),
            json(list)
                .at("/data/items/0")
                .propertyNames()
                .asSequence()
                .toSet(),
        )

        mockMvc
            .get("/api/v1/rag/history/$answerId") {
                bearer(adminToken)
                header("X-Request-Id", "req-rag-history-cross-owner")
            }.andExpect {
                status { isNotFound() }
            }
        mockMvc
            .delete("/api/v1/rag/history/$answerId") {
                bearer(adminToken)
                header("X-Request-Id", "req-rag-history-cross-delete")
            }.andExpect {
                status { isNoContent() }
            }

        mockMvc
            .post("/api/v1/rag/answers/$answerId/feedback") {
                bearer(userToken)
                header("X-Request-Id", "req-rag-history-feedback")
                contentType = MediaType.APPLICATION_JSON
                content = """{"helpful":true}"""
            }.andExpect {
                status { isOk() }
                jsonPath("$.data.answerId") { value(answerId) }
                jsonPath("$.data.helpful") { value(true) }
            }
        mockMvc
            .get("/api/v1/rag/history/$answerId") {
                bearer(userToken)
                header("X-Request-Id", "req-rag-history-detail")
            }.andExpect {
                status { isOk() }
                jsonPath("$.data.question") { value(question) }
                jsonPath("$.data.answer") { doesNotExist() }
                jsonPath("$.data.helpful") { value(true) }
            }
        mockMvc
            .delete("/api/v1/rag/history/$answerId") {
                bearer(userToken)
                header("X-Request-Id", "req-rag-history-delete")
            }.andExpect {
                status { isNoContent() }
            }
        mockMvc
            .delete("/api/v1/rag/history/$answerId") {
                bearer(userToken)
                header("X-Request-Id", "req-rag-history-delete-replay")
            }.andExpect {
                status { isNoContent() }
            }
        mockMvc
            .post("/api/v1/rag/ask") {
                bearer(userToken)
                header("X-Idempotency-Key", idempotencyKey)
                header("X-Request-Id", "req-rag-history-expired-replay")
                contentType = MediaType.APPLICATION_JSON
                content = requestBody
            }.andExpect {
                status { isConflict() }
                jsonPath("$.error.code") { value("IDEMPOTENCY_RESULT_UNAVAILABLE") }
            }
    }

    @Test
    fun `append only consent revoke blocks provider attempt and new tables stay behind functions`() {
        val token = login("demo-user", userPassword())
        val grantId = recordConsent(token, "GRANT", "req-rag-consent-grant")
        val revokeId = recordConsent(token, "REVOKE", "req-rag-consent-revoke")
        assertNotEquals(grantId, revokeId)
        assertFalse(readEffectiveConsent())

        assertThrows<DataAccessException> {
            ownerJdbc.update(
                "update rag_consent_events set action = 'GRANT' where consent_event_id = ?",
                revokeId,
            )
        }

        val deniedScope = "a".repeat(64)
        val deniedFingerprint = "b".repeat(64)
        assertEquals("CLAIMED", callClaim(deniedScope, deniedFingerprint))
        assertThrows<Exception> {
            callProviderAttempt(
                deniedScope,
                deniedFingerprint,
                "rpu_${"a".repeat(32)}",
            )
        }

        recordConsent(token, "GRANT", "req-rag-consent-regrant")
        val allowedScope = "c".repeat(64)
        val allowedFingerprint = "d".repeat(64)
        assertEquals("CLAIMED", callClaim(allowedScope, allowedFingerprint))
        callProviderAttempt(
            allowedScope,
            allowedFingerprint,
            "rpu_${"b".repeat(32)}",
        )
        assertEquals(
            1,
            ownerJdbc.queryForObject(
                "select count(*) from rag_provider_usage_ledger where scope_hmac = ?",
                Int::class.java,
                allowedScope,
            ),
        )
        assertThrows<Exception> {
            callProviderAttempt(
                allowedScope,
                allowedFingerprint,
                "rpu_${"c".repeat(32)}",
            )
        }

        val protectedTables =
            listOf(
                "rag_consent_events",
                "rag_answer_claims",
                "rag_answer_claim_transitions",
                "rag_answer_history",
                "rag_answer_citations",
                "rag_answer_feedback",
                "rag_provider_usage_ledger",
            )
        protectedTables.forEach { table ->
            listOf("decision_app", "decision_rag_writer", "decision_rag_query").forEach { role ->
                listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                    assertFalse(
                        ownerJdbc.queryForObject(
                            "select has_table_privilege(?, ?, ?)",
                            Boolean::class.java,
                            role,
                            table,
                            privilege,
                        ) ?: true,
                        "unexpected $privilege on $table for $role",
                    )
                }
            }
        }
        assertThrows<DataAccessException> {
            appJdbc.queryForObject("select count(*) from rag_answer_history", Int::class.java)
        }
    }

    @Test
    fun `twenty concurrent claims allow one winner and stale pending never retries`() {
        val scope = "e".repeat(64)
        val fingerprint = "f".repeat(64)
        val barrier = CyclicBarrier(20)
        val executor = Executors.newFixedThreadPool(20)
        try {
            val futures =
                (1..20).map {
                    CompletableFuture.supplyAsync(
                        {
                            barrier.await(10, TimeUnit.SECONDS)
                            callClaim(scope, fingerprint)
                        },
                        executor,
                    )
                }
            val outcomes = futures.map { it.get(20, TimeUnit.SECONDS) }
            assertEquals(1, outcomes.count { it == "CLAIMED" })
            assertEquals(19, outcomes.count { it == "IN_PROGRESS" })
        } finally {
            executor.shutdownNow()
        }

        assertEquals("CONFLICT", callClaim(scope, "9".repeat(64)))
        val staleScope = "1".repeat(64)
        val staleFingerprint = "2".repeat(64)
        assertEquals("CLAIMED", callClaim(staleScope, staleFingerprint))
        ownerJdbc.update(
            """
            update rag_answer_claims
            set claimed_at = now() - interval '2 minutes',
                pending_expires_at = now() - interval '1 second'
            where scope_hmac = ?
            """.trimIndent(),
            staleScope,
        )
        assertEquals("UNKNOWN_AFTER_PROVIDER", callClaim(staleScope, staleFingerprint))
        assertEquals("UNKNOWN_AFTER_PROVIDER", callClaim(staleScope, staleFingerprint))
    }

    private fun ask(
        token: String,
        idempotencyKey: String,
        body: String,
        requestId: String,
    ): MvcResult =
        mockMvc
            .post("/api/v1/rag/ask") {
                bearer(token)
                header("X-Idempotency-Key", idempotencyKey)
                header("X-Request-Id", requestId)
                contentType = MediaType.APPLICATION_JSON
                content = body
            }.andExpect {
                status { isOk() }
                jsonPath("$.success") { value(true) }
                jsonPath("$.requestId") { value(requestId) }
            }.andReturn()

    private fun recordConsent(
        token: String,
        action: String,
        requestId: String,
    ): String {
        val response =
            mockMvc
                .post("/api/v1/consents") {
                    bearer(token)
                    header("X-Request-Id", requestId)
                    contentType = MediaType.APPLICATION_JSON
                    content =
                        """
                        {
                          "consentType":"EXTERNAL_AI_RAG_V1",
                          "action":"$action",
                          "policyVersion":"EXTERNAL_AI_RAG_V1"
                        }
                        """.trimIndent()
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.data.action") { value(action) }
                }.andReturn()
        return json(response).at("/data/consentEventId").stringValue()
    }

    private fun readEffectiveConsent(): Boolean =
        appTransaction { connection ->
            connection
                .prepareStatement("select granted from read_effective_rag_consent(?)")
                .use { statement ->
                    statement.setString(1, "usr_demo_user")
                    statement.executeQuery().use { result ->
                        result.next()
                        result.getBoolean("granted")
                    }
                }
        }

    private fun callClaim(
        scopeHmac: String,
        fingerprint: String,
    ): String =
        appTransaction { connection ->
            connection
                .prepareStatement(
                    "select outcome from claim_rag_answer(?, ?, ?, 120)",
                ).use { statement ->
                    statement.setString(1, "usr_demo_user")
                    statement.setString(2, scopeHmac)
                    statement.setString(3, fingerprint)
                    statement.executeQuery().use { result ->
                        result.next()
                        result.getString("outcome")
                    }
                }
        }

    private fun callProviderAttempt(
        scopeHmac: String,
        fingerprint: String,
        usageEventId: String,
    ) {
        appTransaction { connection ->
            connection
                .prepareStatement(
                    "select mark_rag_provider_attempt(?, ?, ?, ?, ?, 'GEMINI')",
                ).use { statement ->
                    statement.setString(1, "usr_demo_user")
                    statement.setString(2, scopeHmac)
                    statement.setString(3, fingerprint)
                    statement.setString(4, usageEventId)
                    statement.setString(5, "7".repeat(64))
                    statement.execute()
                }
        }
    }

    private fun <T> appTransaction(block: (Connection) -> T): T {
        DriverManager
            .getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD)
            .use { connection ->
                connection.autoCommit = false
                try {
                    connection
                        .prepareStatement(
                            "select set_config('app.actor_user_id', ?, true)",
                        ).use { statement ->
                            statement.setString(1, "usr_demo_user")
                            statement.execute()
                        }
                    val result = block(connection)
                    connection.commit()
                    return result
                } catch (exception: Exception) {
                    connection.rollback()
                    throw exception
                }
            }
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
                    header("X-Request-Id", "req-rag-history-login-$username")
                }.andExpect {
                    status { isOk() }
                }.andReturn()
        return json(response).at("/data/accessToken").stringValue()
    }

    private fun json(result: MvcResult): JsonNode = objectMapper.readTree(result.response.contentAsString)

    private fun org.springframework.test.web.servlet.MockHttpServletRequestDsl.bearer(token: String) {
        header("Authorization", "Bearer $token")
    }

    companion object {
        private const val APP_PASSWORD = "app-test"
        private const val FLYWAY_PASSWORD = "flyway-test"
        private val redisPasswordValue: String = "r" + "p".repeat(24)
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            PostgreSQLContainer(postgresImage)
                .withDatabaseName("decision_rag_guard_history")
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
            ).withCommand(
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
        fun containerProperties(registry: DynamicPropertyRegistry) {
            registry.add("spring.datasource.url", postgres::getJdbcUrl)
            registry.add("spring.datasource.username") { "decision_app" }
            registry.add("spring.datasource.password") { APP_PASSWORD }
            registry.add("spring.flyway.user") { "flyway" }
            registry.add("spring.flyway.password") { FLYWAY_PASSWORD }
            registry.add("spring.data.redis.host", redis::getHost)
            registry.add("spring.data.redis.port") { redis.getMappedPort(6379) }
            registry.add("spring.data.redis.password") { redisPasswordValue }
        }
    }
}
