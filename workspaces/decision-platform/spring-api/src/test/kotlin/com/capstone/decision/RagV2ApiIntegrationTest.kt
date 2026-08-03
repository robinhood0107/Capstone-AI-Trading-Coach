package com.capstone.decision

import com.capstone.decision.application.rag.RagHistoryCryptoPort
import com.capstone.decision.application.rag.RagHistoryIdentity
import org.junit.jupiter.api.Assertions.assertEquals
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
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset

@Testcontainers
@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
class RagV2ApiIntegrationTest(
    @Autowired private val webApplicationContext: WebApplicationContext,
    @Autowired private val objectMapper: ObjectMapper,
    @Autowired private val cryptoPort: RagHistoryCryptoPort,
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
                    jsonPath("$.publicCorpusVersion") { value("immutable-v2-0") }
                    jsonPath("$.privateOverlayState") { value("ABSENT") }
                    jsonPath("$.progressPercent") { value(0) }
                    jsonPath("$.failureCode") { doesNotExist() }
                    jsonPath("$.success") { doesNotExist() }
                    jsonPath("$.data") { doesNotExist() }
                }.andReturn()
        assertSanitized(json(initial))

        ownerJdbc.update(
            """
            insert into rag_v2_immutable_owner_bundle_pointers (
              owner_user_id,
              state,
              active_bundle_id,
              bundle_version
            ) values ('usr_demo_user', 'BUILDING', null, 0)
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
                    jsonPath("$.progressPercent") { value(50) }
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
        ownerJdbc.update("delete from rag_v2_answer_history where owner_user_id = 'usr_demo_user'")

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

    @Test
    fun `history detail returns decrypted owner scoped v2 payload`() {
        val token = login("demo-user", userPassword(), "req_rag_v2_login_detail")
        val answerId = "rag_01DETAILDECRYPTID"
        ownerJdbc.update("delete from rag_v2_answer_history where answer_id = ?", answerId)
        val createdAt = Instant.parse("2026-08-02T02:50:00Z")
        val question = "내 문서 기반 RAG는 주문 판단에 영향을 주나요?"
        val answer = "아니요. RAG v2는 설명과 근거 제공에만 사용됩니다."
        val encrypted =
            cryptoPort.encrypt(
                RagHistoryIdentity(
                    answerId = answerId,
                    ownerUserId = "usr_demo_user",
                    createdAt = createdAt,
                ),
                question = question,
                answer = answer,
            )

        ownerJdbc.update(
            """
            insert into rag_v2_answer_history (
              answer_id,
              owner_user_id,
              request_id,
              answer_mode,
              generation_status,
              citation_coverage,
              retrieval_failure,
              guardrail_flags,
              public_corpus_version,
              private_overlay_state,
              kek_version,
              wrap_nonce,
              wrapped_dek,
              wrap_tag,
              question_nonce,
              question_ciphertext,
              question_tag,
              answer_nonce,
              answer_ciphertext,
              answer_tag,
              citation_count,
              created_at,
              expires_at
            ) values (?, ?, ?, ?, ?, ?, ?, ARRAY[]::text[], ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """.trimIndent(),
            answerId,
            "usr_demo_user",
            "req_rag_v2_detail_seed",
            "CONCISE",
            "ANSWERED",
            1.0,
            false,
            "exact30-v1+oa140-draft-v1",
            "ABSENT",
            encrypted.kekVersion,
            encrypted.wrapNonce,
            encrypted.wrappedDek,
            encrypted.wrapTag,
            encrypted.question.nonce,
            encrypted.question.ciphertext,
            encrypted.question.tag,
            encrypted.answer.nonce,
            encrypted.answer.ciphertext,
            encrypted.answer.tag,
            1,
            OffsetDateTime.ofInstant(createdAt, ZoneOffset.UTC),
            OffsetDateTime.ofInstant(createdAt.plusSeconds(30L * 24 * 60 * 60), ZoneOffset.UTC),
        )
        ownerJdbc.update(
            """
            insert into rag_v2_answer_citations (
              answer_id,
              owner_user_id,
              ordinal,
              citation_kind,
              source_id,
              title,
              canonical_url,
              locator
            ) values (?, ?, 1, 'PUBLIC_WEB', ?, ?, ?, ?::jsonb)
            """.trimIndent(),
            answerId,
            "usr_demo_user",
            "src_s4_7d_contract_001",
            "S4.7D RAG v2 Contract",
            "https://example.org/s4-7d-rag-v2",
            """{"section":"RAG v2"}""",
        )

        val detail =
            mockMvc
                .get("/api/v2/rag/history/$answerId") {
                    bearer(token)
                    header("X-Request-Id", "req_rag_v2_history_detail")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.answerId") { value(answerId) }
                    jsonPath("$.question") { value(question) }
                    jsonPath("$.answer") { value(answer) }
                    jsonPath("$.generationStatus") { value("ANSWERED") }
                    jsonPath("$.citations[0].citationKind") { value("PUBLIC_WEB") }
                    jsonPath("$.citations[0].canonicalUrl") { value("https://example.org/s4-7d-rag-v2") }
                    jsonPath("$.success") { doesNotExist() }
                }.andReturn()
        assertSanitized(json(detail))
    }

    @Test
    fun `v2 external consent is append only owner scoped and returns the effective server state`() {
        val userToken = login("demo-user", userPassword(), "req_rag_v2_consent_user_login")
        val adminToken = login("demo-admin", adminPassword(), "req_rag_v2_consent_admin_login")

        mockMvc
            .get("/api/v2/rag/consent") {
                bearer(userToken)
                header("X-Request-Id", "req_rag_v2_consent_missing")
            }.andExpect {
                status { isConflict() }
                jsonPath("$.code") { value("EXTERNAL_AI_CONSENT_REQUIRED") }
                jsonPath("$.requestId") { value("req_rag_v2_consent_missing") }
            }

        val grant =
            """
            {
              "contractId":"s4-rag-v2-external-consent-v1",
              "schemaVersion":1,
              "consentType":"EXTERNAL_AI_RAG_V2",
              "action":"GRANT",
              "disclosureDigest":"${"c".repeat(64)}",
              "policyDigest":"${"d".repeat(64)}",
              "processorSetDigest":"${"e".repeat(64)}"
            }
            """.trimIndent()
        mockMvc
            .post("/api/v2/rag/consents") {
                bearer(userToken)
                header("X-Request-Id", "req_rag_v2_consent_grant")
                contentType = MediaType.APPLICATION_JSON
                content = grant
            }.andExpect {
                status { isNoContent() }
            }

        val granted =
            mockMvc
                .get("/api/v2/rag/consent") {
                    bearer(userToken)
                    header("X-Request-Id", "req_rag_v2_consent_granted")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.contractId") { value("s4-rag-v2-effective-consent-v1") }
                    jsonPath("$.schemaVersion") { value(1) }
                    jsonPath("$.consentEventId") { exists() }
                    jsonPath("$.effective") { value(true) }
                    jsonPath("$.state") { value("GRANTED") }
                    jsonPath("$.policyDigest") { value("d".repeat(64)) }
                    jsonPath("$.processorSetDigest") { value("e".repeat(64)) }
                    jsonPath("$.success") { doesNotExist() }
                    jsonPath("$.ownerUserId") { doesNotExist() }
                }.andReturn()
        assertControlPlaneSanitized(json(granted))

        mockMvc
            .get("/api/v2/rag/consent") {
                bearer(adminToken)
                header("X-Request-Id", "req_rag_v2_consent_cross_owner")
            }.andExpect {
                status { isConflict() }
                jsonPath("$.code") { value("EXTERNAL_AI_CONSENT_REQUIRED") }
            }

        val stored =
            ownerJdbc.queryForMap(
                """
                select consent_event_id, public_consent_event_id, policy_digest, processor_set_digest
                from rag_v2_immutable_consent_events
                where owner_user_id = 'usr_demo_user'
                order by created_at desc, consent_event_id desc
                limit 1
                """.trimIndent(),
            )
        assertTrue(stored["consent_event_id"].toString().startsWith("cns_v2_"))
        assertTrue(stored["public_consent_event_id"].toString().startsWith("rce_"))
        assertEquals("d".repeat(64), stored["policy_digest"])
        assertEquals("e".repeat(64), stored["processor_set_digest"])

        val revoke = grant.replace("\"GRANT\"", "\"REVOKE\"")
        mockMvc
            .post("/api/v2/rag/consents") {
                bearer(userToken)
                header("X-Request-Id", "req_rag_v2_consent_revoke")
                contentType = MediaType.APPLICATION_JSON
                content = revoke
            }.andExpect {
                status { isNoContent() }
            }
        mockMvc
            .get("/api/v2/rag/consent") {
                bearer(userToken)
                header("X-Request-Id", "req_rag_v2_consent_revoked")
            }.andExpect {
                status { isOk() }
                jsonPath("$.effective") { value(false) }
                jsonPath("$.state") { value("REVOKED") }
            }
    }

    @Test
    fun `v2 owner import ticket is a five minute raw capability with only a database hash retained`() {
        val userToken = login("demo-user", userPassword(), "req_rag_v2_ticket_user_login")
        val ticketRequest =
            """
            {
              "contractId":"s4-rag-v2-import-ticket-request-v1",
              "schemaVersion":1,
              "sourceScope":"OWNER_PRIVATE",
              "importMode":"LOCAL_EPHEMERAL_PARSE"
            }
            """.trimIndent()

        val issued =
            mockMvc
                .post("/api/v2/rag/import-tickets") {
                    bearer(userToken)
                    header("X-Request-Id", "req_rag_v2_ticket_issue")
                    contentType = MediaType.APPLICATION_JSON
                    content = ticketRequest
                }.andExpect {
                    status { isCreated() }
                    jsonPath("$.contractId") { value("s4-rag-v2-import-ticket-v1") }
                    jsonPath("$.schemaVersion") { value(1) }
                    jsonPath("$.ticketId") { exists() }
                    jsonPath("$.sourceScope") { value("OWNER_PRIVATE") }
                    jsonPath("$.ttlSeconds") { value(300) }
                    jsonPath("$.singleUse") { value(true) }
                    jsonPath("$.ownerBound") { value(true) }
                    jsonPath("$.ownerRawCopyAllowed") { value(false) }
                    jsonPath("$.success") { doesNotExist() }
                    jsonPath("$.ownerUserId") { doesNotExist() }
                }.andReturn()
        val payload = json(issued)
        val ticketId = payload.at("/ticketId").stringValue()
        assertTrue(ticketId.startsWith("rti_"))
        assertControlPlaneSanitized(payload)

        val issuedAt = Instant.parse(payload.at("/issuedAt").stringValue())
        val expiresAt = Instant.parse(payload.at("/expiresAt").stringValue())
        assertEquals(300L, expiresAt.epochSecond - issuedAt.epochSecond)
        val storedTicketHash =
            ownerJdbc.queryForObject(
                """
                select ticket_hash
                from rag_v2_immutable_import_tickets
                where owner_user_id = 'usr_demo_user'
                order by issued_at desc, ticket_hash desc
                limit 1
                """.trimIndent(),
                String::class.java,
            )
        assertTrue(storedTicketHash?.matches(Regex("^[0-9a-f]{64}$")) == true)
        assertFalse(ticketId == storedTicketHash)

        mockMvc
            .post("/api/v2/rag/import-tickets") {
                bearer(userToken)
                header("X-Request-Id", "req_rag_v2_ticket_actor_injection")
                contentType = MediaType.APPLICATION_JSON
                content = ticketRequest.dropLast(1) + ",\"ownerUserId\":\"usr_demo_admin\"}"
            }.andExpect {
                status { isBadRequest() }
                jsonPath("$.code") { value("RAG_VALIDATION_FAILED") }
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

    private fun assertControlPlaneSanitized(node: JsonNode) {
        val text = node.toString()
        assertFalse(text.contains("/tmp"))
        assertFalse(text.contains("/home"))
        assertFalse(text.contains("owner_user_id", ignoreCase = true))
        assertFalse(text.contains("ownerUserId"))
        assertFalse(text.contains("ticketHash"))
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
