package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
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

@Testcontainers
@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
class RagV2ApiIntegrationTest(
    @Autowired private val webApplicationContext: WebApplicationContext,
    @Autowired private val objectMapper: ObjectMapper,
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

    @BeforeEach
    fun setUp() {
        mockMvc =
            MockMvcBuilders
                .webAppContextSetup(webApplicationContext)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    @Test
    fun `corpus status is direct sanitized v2 payload and reflects owner private build state`() {
        val token = login("demo-user", userPassword(), "req_rag_v2_login_status")

        val initial =
            mockMvc
                .get("/api/v2/rag/corpus-status") {
                    bearer(token)
                    header("X-Request-Id", "req_rag_v2_status_initial")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.state") { value("CORE_READY") }
                    jsonPath("$.publicCorpusVersion") { value("exact30-v1+oa140-draft-v1") }
                    jsonPath("$.privateOverlayState") { value("ABSENT") }
                    jsonPath("$.progressPercent") { value(0) }
                    jsonPath("$.failureCode") { doesNotExist() }
                    jsonPath("$.success") { doesNotExist() }
                    jsonPath("$.data") { doesNotExist() }
                }.andReturn()
        assertSanitized(json(initial))

        ownerJdbc.update(
            """
            insert into rag_v2_owner_private_generation_pointers (
              owner_user_id,
              private_overlay_state,
              progress_percent
            ) values ('usr_demo_user', 'BUILDING', 42)
            """.trimIndent(),
        )

        val building =
            mockMvc
                .get("/api/v2/rag/corpus-status") {
                    bearer(token)
                    header("X-Request-Id", "req_rag_v2_status_building")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.state") { value("BUILDING") }
                    jsonPath("$.privateOverlayState") { value("BUILDING") }
                    jsonPath("$.progressPercent") { value(42) }
                }.andReturn()
        assertSanitized(json(building))
    }

    @Test
    fun `ask rejects client search selectors and fails closed until full bundle is ready`() {
        val token = login("demo-user", userPassword(), "req_rag_v2_login_ask")

        mockMvc
            .post("/api/v2/rag/ask") {
                bearer(token)
                header("X-Request-Id", "req_rag_v2_ask_selector")
                contentType = MediaType.APPLICATION_JSON
                content =
                    """
                    {
                      "question":"옵션가격 모형을 설명해 주세요.",
                      "answerMode":"DETAILED",
                      "topK":10
                    }
                    """.trimIndent()
            }.andExpect {
                status { isBadRequest() }
                jsonPath("$.code") { value("RAG_VALIDATION_FAILED") }
                jsonPath("$.requestId") { value("req_rag_v2_ask_selector") }
            }

        val notReady =
            mockMvc
                .post("/api/v2/rag/ask") {
                    bearer(token)
                    header("X-Request-Id", "req_rag_v2_ask_not_ready")
                    contentType = MediaType.APPLICATION_JSON
                    content =
                        """
                        {
                          "question":"옵션가격 모형의 가정과 한계를 근거와 함께 설명해 주세요.",
                          "answerMode":"DETAILED",
                          "relatedSymbols":["005930"],
                          "topics":["FINANCIAL_ENGINEERING"]
                        }
                        """.trimIndent()
                }.andExpect {
                    status { isConflict() }
                    jsonPath("$.code") { value("CORPUS_NOT_READY") }
                    jsonPath("$.requestId") { value("req_rag_v2_ask_not_ready") }
                    jsonPath("$.success") { doesNotExist() }
                }.andReturn()
        assertSanitized(json(notReady))
    }

    @Test
    fun `history surface is owner scoped direct v2 payload and protected tables stay function only`() {
        val token = login("demo-user", userPassword(), "req_rag_v2_login_history")
        val answerId = "rag_historyMissing01"

        mockMvc
            .get("/api/v2/rag/history?limit=20") {
                bearer(token)
                header("X-Request-Id", "req_rag_v2_history_list")
            }.andExpect {
                status { isOk() }
                jsonPath("$.items.length()") { value(0) }
                jsonPath("$.nextCursor") { doesNotExist() }
                jsonPath("$.success") { doesNotExist() }
            }
        mockMvc
            .get("/api/v2/rag/history/$answerId") {
                bearer(token)
                header("X-Request-Id", "req_rag_v2_history_not_found")
            }.andExpect {
                status { isNotFound() }
                jsonPath("$.code") { value("RAG_HISTORY_NOT_FOUND") }
            }
        mockMvc
            .delete("/api/v2/rag/history/$answerId") {
                bearer(token)
                header("X-Request-Id", "req_rag_v2_history_delete")
            }.andExpect {
                status { isNoContent() }
            }

        val protectedTables =
            listOf(
                "rag_v2_public_corpus_state",
                "rag_v2_owner_private_generation_pointers",
                "rag_v2_owner_documents",
                "rag_v2_owner_document_chunks",
                "rag_v2_owner_document_embeddings",
                "rag_v2_document_deletion_receipts",
                "rag_v2_answer_history",
                "rag_v2_answer_citations",
            )
        protectedTables.forEach { table ->
            listOf("decision_app", "decision_rag_writer", "decision_rag_query").forEach { role ->
                listOf("SELECT", "INSERT", "UPDATE", "DELETE").forEach { privilege ->
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
    }

    private fun login(
        username: String,
        password: String,
        requestId: String,
    ): String {
        val response =
            mockMvc
                .post("/api/v1/auth/login") {
                    contentType = MediaType.APPLICATION_JSON
                    content = objectMapper.writeValueAsString(mapOf("username" to username, "password" to password))
                    header("X-Request-Id", requestId)
                }.andExpect {
                    status { isOk() }
                }.andReturn()
        return json(response).at("/data/accessToken").stringValue()
    }

    private fun json(result: MvcResult): JsonNode = objectMapper.readTree(result.response.contentAsString)

    private fun assertSanitized(node: JsonNode) {
        val text = node.toString()
        assertFalse(text.contains("/tmp"))
        assertFalse(text.contains("/home"))
        assertFalse(text.contains("sha256", ignoreCase = true))
        assertFalse(text.contains("raw", ignoreCase = true))
        assertFalse(text.contains("path", ignoreCase = true))
        assertFalse(text.contains("credential", ignoreCase = true))
        assertTrue(text.length <= 2048)
    }

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
                .withDatabaseName("decision_rag_v2_runtime")
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
