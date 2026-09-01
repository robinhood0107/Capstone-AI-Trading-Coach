package com.capstone.decision

import com.capstone.decision.application.dashboard.DashboardViewService
import com.capstone.decision.application.market.ForeignNewsSentimentReadPort
import com.capstone.decision.application.rag.RagHistoryCryptoPort
import com.capstone.decision.application.rag.RagHistoryIdentity
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import com.capstone.decision.infrastructure.security.ActorRlsScope
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.dao.DataAccessException
import org.springframework.http.MediaType
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.jdbc.datasource.DriverManagerDataSource
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken
import org.springframework.security.core.context.SecurityContextHolder
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
import org.springframework.transaction.PlatformTransactionManager
import org.springframework.transaction.support.TransactionTemplate
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
    @Autowired private val dashboardViewService: DashboardViewService,
    @Autowired private val foreignNewsSentimentReadPort: ForeignNewsSentimentReadPort,
    @Autowired private val testActorCapabilityIssuer: TestActorCapabilityIssuer,
    @Autowired private val actorRlsScope: ActorRlsScope,
    @Autowired private val appJdbc: NamedParameterJdbcTemplate,
    @Autowired private val transactionManager: PlatformTransactionManager,
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
    fun `foreign news route is authenticated owner scoped and exposes only sanitized lane states`() {
        val userToken = login("demo-user", userPassword(), "req_foreign_news_user_login")
        val adminToken = login("demo-admin", adminPassword(), "req_foreign_news_admin_login")

        asActor { assertNull(foreignNewsSentimentReadPort.findLatest("usr_demo_user", "005930")) }

        mockMvc
            .get("/api/v2/market-evidence/005930/foreign-news-sentiment") {
                header("X-Request-Id", "req_foreign_news_unauthenticated")
            }.andExpect {
                status { isUnauthorized() }
            }

        val notActivated =
            mockMvc
                .get("/api/v2/market-evidence/005930/foreign-news-sentiment") {
                    bearer(userToken)
                    header("X-Request-Id", "req_foreign_news_not_activated")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.status") { value("ABSTAIN") }
                    jsonPath("$.lanes.length()") { value(4) }
                    jsonPath("$.lanes[0].laneId") { value("FINNHUB_PERSONAL_LOCAL") }
                    jsonPath("$.lanes[3].laneId") { value("GDELT_OFFLINE_REFERENCE") }
                    jsonPath("$.lanes[3].state") { value("NOT_ACTIVATED") }
                    jsonPath("$.contractId") { value("foreign-news-sentiment-v1") }
                    jsonPath("$.decisionAuthority") { value("NONE") }
                    jsonPath("$.allowedUses[0]") { value("EXPLANATION_ONLY") }
                    jsonPath("$.s5FeatureEligible") { value(false) }
                    jsonPath("$.riskDecisionHashIncluded") { value(false) }
                    jsonPath("$.rawProviderDataStored") { value(false) }
                    jsonPath("$.articleMetadataStored") { value(false) }
                    jsonPath("$.success") { doesNotExist() }
                    jsonPath("$.data") { doesNotExist() }
                }.andReturn()
        assertForeignNewsSanitized(json(notActivated))

        val lanes =
            """
            [
              {"laneId":"FINNHUB_PERSONAL_LOCAL","state":"NOT_ACTIVATED"},
              {"laneId":"SEC_OFFICIAL","state":"NOT_ACTIVATED"},
              {"laneId":"FED_OFFICIAL","state":"NOT_ACTIVATED"},
              {"laneId":"GDELT_OFFLINE_REFERENCE","state":"AVAILABLE"}
            ]
            """.trimIndent()
        val payload =
            """
            {
              "allowedUses":["EXPLANATION_ONLY"],
              "articleMetadataStored":false,
              "asOf":"2026-08-09T01:00:00Z",
              "contractId":"foreign-news-sentiment-v1",
              "decisionAuthority":"NONE",
              "lanes":$lanes,
              "rawProviderDataStored":false,
              "riskDecisionHashIncluded":false,
              "s5FeatureEligible":false,
              "schemaVersion":1,
              "status":"AVAILABLE",
              "symbol":"005930"
            }
            """.trimIndent()
        ownerJdbc.update(
            """
            insert into foreign_news_sentiment_aggregates (
              logical_identity_hash, owner_user_id, symbol, as_of, status, lane_states,
              payload_hash, artifact_hash, payload_json
            ) values (?, 'usr_demo_user', '005930', '2026-08-09T01:00:00Z', 'AVAILABLE', ?::jsonb, ?, ?, ?::jsonb)
            """.trimIndent(),
            "a".repeat(64),
            lanes,
            "b".repeat(64),
            "c".repeat(64),
            payload,
        )

        val available =
            mockMvc
                .get("/api/v2/market-evidence/005930/foreign-news-sentiment") {
                    bearer(userToken)
                    header("X-Request-Id", "req_foreign_news_available")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.status") { value("AVAILABLE") }
                    jsonPath("$.lanes[3].state") { value("AVAILABLE") }
                    jsonPath("$.contractId") { value("foreign-news-sentiment-v1") }
                    jsonPath("$.symbol") { value("005930") }
                }.andReturn()
        assertForeignNewsSanitized(json(available))

        val crossOwner =
            mockMvc
                .get("/api/v2/market-evidence/005930/foreign-news-sentiment") {
                    bearer(adminToken)
                    header("X-Request-Id", "req_foreign_news_cross_owner")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.status") { value("ABSTAIN") }
                    jsonPath("$.lanes[3].state") { value("NOT_ACTIVATED") }
                }.andReturn()
        assertForeignNewsSanitized(json(crossOwner))

        mockMvc
            .get("/api/v2/market-evidence/aapl/foreign-news-sentiment") {
                bearer(userToken)
                header("X-Request-Id", "req_foreign_news_symbol_invalid")
            }.andExpect {
                status { isBadRequest() }
                jsonPath("$.code") { value("FOREIGN_NEWS_VALIDATION_FAILED") }
            }
        mockMvc
            .get("/api/v2/market-evidence/005930/foreign-news-sentiment?provider=finnhub") {
                bearer(userToken)
                header("X-Request-Id", "req_foreign_news_query_rejected")
            }.andExpect {
                status { isBadRequest() }
                jsonPath("$.code") { value("FOREIGN_NEWS_VALIDATION_FAILED") }
            }
    }

    @Test
    fun `exact RLS binding rejects operation target payload mismatches and READ cannot write consent`() {
        val binding =
            ActorCapabilityBinding.target(
                "READ_RAG_CONSENT",
                "OWNER",
                "usr_demo_user",
                ActorCapabilityRolePolicy.OWNER,
            )
        listOf(
            listOf("RECORD_RAG_CONSENT", binding.targetKind, binding.targetId, binding.payloadHash),
            listOf(binding.operation, "RAG_CONSENT", binding.targetId, binding.payloadHash),
            listOf(binding.operation, binding.targetKind, "usr_demo_admin", binding.payloadHash),
            listOf(binding.operation, binding.targetKind, binding.targetId, "sha256:${"0".repeat(64)}"),
        ).forEach { asserted ->
            assertThrows<DataAccessException> {
                asActor {
                    TransactionTemplate(transactionManager).executeWithoutResult {
                        actorRlsScope.open(appJdbc, "usr_demo_user", binding)
                        appJdbc.queryForObject(
                            "select assert_actor_rls_scope_exact_v1(:actor,:operation,:kind,:target,:payload)",
                            mapOf(
                                "actor" to "usr_demo_user",
                                "operation" to asserted[0],
                                "kind" to asserted[1],
                                "target" to asserted[2],
                                "payload" to asserted[3],
                            ),
                            Boolean::class.java,
                        )
                    }
                }
            }
        }

        assertThrows<DataAccessException> {
            asActor {
                TransactionTemplate(transactionManager).executeWithoutResult {
                    actorRlsScope.open(appJdbc, "usr_demo_user", binding)
                    appJdbc.queryForObject(
                        """
                        select consent_event_id from record_rag_consent_event(
                          :owner,:eventId,'GRANT','p1-read-must-not-write'
                        )
                        """.trimIndent(),
                        mapOf(
                            "owner" to "usr_demo_user",
                            "eventId" to "consent_read_capability_write_denied",
                        ),
                        String::class.java,
                    )
                }
            }
        }

        val wrapperMismatches =
            listOf(
                ActorCapabilityBinding.target(
                    "READ_RAG_V2_CONSENT",
                    "OWNER",
                    "usr_demo_user",
                    ActorCapabilityRolePolicy.OWNER,
                ) to "usr_demo_user",
                ActorCapabilityBinding.target(
                    "READ_RAG_V2_CORPUS",
                    "OWNER",
                    "usr_demo_user",
                    ActorCapabilityRolePolicy.OWNER,
                ) to "usr_demo_admin",
                ActorCapabilityBinding(
                    operation = "READ_RAG_V2_CORPUS",
                    targetKind = "OWNER",
                    targetId = "usr_demo_user",
                    payloadHash = "sha256:${"0".repeat(64)}",
                    rolePolicy = ActorCapabilityRolePolicy.OWNER,
                ) to "usr_demo_user",
            )
        wrapperMismatches.forEach { (wrapperBinding, requestedOwner) ->
            assertThrows<DataAccessException> {
                asActor {
                    TransactionTemplate(transactionManager).executeWithoutResult {
                        actorRlsScope.open(appJdbc, "usr_demo_user", wrapperBinding)
                        appJdbc.queryForObject(
                            "select state from read_rag_v2_corpus_status(:owner)",
                            mapOf("owner" to requestedOwner),
                            String::class.java,
                        )
                    }
                }
            }
        }

        assertThrows<DataAccessException> {
            asActor {
                TransactionTemplate(transactionManager).executeWithoutResult {
                    actorRlsScope.open(
                        appJdbc,
                        "usr_demo_user",
                        ActorCapabilityBinding.target(
                            "READ_RAG_V2_CORPUS",
                            "OWNER",
                            "usr_demo_user",
                            ActorCapabilityRolePolicy.OWNER,
                        ),
                    )
                    appJdbc.queryForObject(
                        "select state from read_rag_v2_corpus_status_legacy_v87(:owner)",
                        mapOf("owner" to "usr_demo_user"),
                        String::class.java,
                    )
                }
            }
        }
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
        // Keep the row inside its contractual 30-day TTL.  The former fixed
        // timestamp expired at 2026-09-01T02:50Z, so the same tree passed PR
        // CI before that instant and failed main CI immediately afterwards.
        val createdAt = Instant.now().minusSeconds(60)
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

        asActor { assertTrue(dashboardViewService.rag("usr_demo_user", 1, answerId)?.isObject == true) }

        val dashboard =
            mockMvc
                .get("/api/v1/dashboard/rag-sources/$answerId") {
                    bearer(token)
                    header("X-Request-Id", "req_rag_v2_dashboard_sources")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.data.evidenceMode") { value("STORED_RUNTIME") }
                    jsonPath("$.data.performanceClaimAllowed") { value(false) }
                    jsonPath("$.data.view.answerId") { value(answerId) }
                    jsonPath("$.data.view.topSources.length()") { value(1) }
                    jsonPath("$.data.view.topSources[0].sourceId") { value("src_s4_7d_contract_001") }
                    jsonPath("$.data.view.topSources[0].canonicalUrl") { doesNotExist() }
                    jsonPath("$.data.view.topSources[0].locator") { doesNotExist() }
                }.andReturn()
        assertFalse(dashboard.response.contentAsString.contains("example.org"))
        val foreignToken = login("demo-admin", adminPassword(), "req_rag_v2_dashboard_foreign")
        mockMvc.get("/api/v1/dashboard/rag-sources/$answerId") { bearer(foreignToken) }.andExpect {
            status { isNotFound() }
        }
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
    fun `v2 owner import ticket binds the explicit library embedding profile and retains only a database hash`() {
        val userToken = login("demo-user", userPassword(), "req_rag_v2_ticket_user_login")
        val ticketRequest =
            """
            {
              "contractId":"s4-rag-v2-import-ticket-request-v2",
              "schemaVersion":2,
              "sourceScope":"OWNER_PRIVATE",
              "importMode":"LOCAL_EPHEMERAL_PARSE",
              "embeddingProfileId":"voyage_context_4_1024_v1"
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
                    jsonPath("$.contractId") { value("s4-rag-v2-import-ticket-v2") }
                    jsonPath("$.schemaVersion") { value(2) }
                    jsonPath("$.ticketId") { exists() }
                    jsonPath("$.sourceScope") { value("OWNER_PRIVATE") }
                    jsonPath("$.embeddingProfileId") { value("voyage_context_4_1024_v1") }
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
        val storedTicket =
            ownerJdbc.queryForMap(
                """
                select ticket_hash, embedding_profile_id
                from rag_v2_immutable_import_tickets
                where owner_user_id = 'usr_demo_user'
                order by issued_at desc, ticket_hash desc
                limit 1
                """.trimIndent(),
            )
        assertTrue(storedTicket["ticket_hash"].toString().matches(Regex("^[0-9a-f]{64}$")))
        assertEquals("voyage_context_4_1024_v1", storedTicket["embedding_profile_id"])
        assertFalse(ticketId == storedTicket["ticket_hash"])

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

    @Test
    fun `v2 owner import ticket rejects missing arbitrary and legacy profiles`() {
        val userToken = login("demo-user", userPassword(), "req_rag_v2_ticket_profile_login")
        val invalidBodies =
            listOf(
                """
                {
                  "contractId":"s4-rag-v2-import-ticket-request-v2",
                  "schemaVersion":2,
                  "sourceScope":"OWNER_PRIVATE",
                  "importMode":"LOCAL_EPHEMERAL_PARSE"
                }
                """.trimIndent(),
                """
                {
                  "contractId":"s4-rag-v2-import-ticket-request-v2",
                  "schemaVersion":2,
                  "sourceScope":"OWNER_PRIVATE",
                  "importMode":"LOCAL_EPHEMERAL_PARSE",
                  "embeddingProfileId":"arbitrary_profile"
                }
                """.trimIndent(),
                """
                {
                  "contractId":"s4-rag-v2-import-ticket-request-v1",
                  "schemaVersion":1,
                  "sourceScope":"OWNER_PRIVATE",
                  "importMode":"LOCAL_EPHEMERAL_PARSE"
                }
                """.trimIndent(),
            )

        invalidBodies.forEachIndexed { index, invalidBody ->
            mockMvc
                .post("/api/v2/rag/import-tickets") {
                    bearer(userToken)
                    header("X-Request-Id", "req_rag_v2_ticket_profile_reject_$index")
                    contentType = MediaType.APPLICATION_JSON
                    content = invalidBody
                }.andExpect {
                    status { isBadRequest() }
                    jsonPath("$.code") { value("RAG_VALIDATION_FAILED") }
                }
        }
    }

    @Test
    fun `v2 owner delete ticket is document bound and retains only a database hash`() {
        val userToken = login("demo-user", userPassword(), "req_rag_v2_delete_ticket_user_login")
        val ticketRequest =
            """
            {
              "contractId":"s4-rag-v2-delete-ticket-request-v1",
              "schemaVersion":1,
              "sourceScope":"OWNER_PRIVATE",
              "documentId":"doc_01deletefixture"
            }
            """.trimIndent()

        val issued =
            mockMvc
                .post("/api/v2/rag/delete-tickets") {
                    bearer(userToken)
                    header("X-Request-Id", "req_rag_v2_delete_ticket_issue")
                    contentType = MediaType.APPLICATION_JSON
                    content = ticketRequest
                }.andExpect {
                    status { isCreated() }
                    jsonPath("$.contractId") { value("s4-rag-v2-delete-ticket-v1") }
                    jsonPath("$.schemaVersion") { value(1) }
                    jsonPath("$.ticketId") { exists() }
                    jsonPath("$.sourceScope") { value("OWNER_PRIVATE") }
                    jsonPath("$.documentId") { value("doc_01deletefixture") }
                    jsonPath("$.ttlSeconds") { value(300) }
                    jsonPath("$.singleUse") { value(true) }
                    jsonPath("$.ownerBound") { value(true) }
                    jsonPath("$.documentBound") { value(true) }
                    jsonPath("$.ownerRawCopyAllowed") { value(false) }
                    jsonPath("$.ownerUserId") { doesNotExist() }
                }.andReturn()
        val payload = json(issued)
        val ticketId = payload.at("/ticketId").stringValue()
        assertTrue(ticketId.matches(Regex("^rtd_[0-9a-f]{32}$")))
        assertControlPlaneSanitized(payload)

        val issuedAt = Instant.parse(payload.at("/issuedAt").stringValue())
        val expiresAt = Instant.parse(payload.at("/expiresAt").stringValue())
        assertEquals(300L, expiresAt.epochSecond - issuedAt.epochSecond)
        val stored =
            ownerJdbc.queryForMap(
                """
                select ticket_hash, document_id, state
                from rag_v2_immutable_owner_delete_tickets
                where owner_user_id = 'usr_demo_user'
                order by issued_at desc, ticket_hash desc
                limit 1
                """.trimIndent(),
            )
        assertTrue(stored["ticket_hash"].toString().matches(Regex("^[0-9a-f]{64}$")))
        assertEquals("doc_01deletefixture", stored["document_id"])
        assertEquals("ISSUED", stored["state"])
        assertFalse(ticketId == stored["ticket_hash"])

        mockMvc
            .post("/api/v2/rag/delete-tickets") {
                bearer(userToken)
                header("X-Request-Id", "req_rag_v2_delete_ticket_actor_injection")
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

    private fun <T> asActor(block: () -> T): T {
        val previous = SecurityContextHolder.getContext()
        val context = SecurityContextHolder.createEmptyContext()
        val actorRef = testActorCapabilityIssuer.actorRef("usr_demo_user")
        context.authentication =
            UsernamePasswordAuthenticationToken(
                AppPrincipal("usr_demo_user", "demo-user", "USER", 1, actorRef),
                null,
                emptyList(),
            )
        SecurityContextHolder.setContext(context)
        return try {
            block()
        } finally {
            SecurityContextHolder.setContext(previous)
        }
    }

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

    private fun assertForeignNewsSanitized(node: JsonNode) {
        val text = node.toString()
        assertFalse(text.contains("headline", ignoreCase = true))
        assertFalse(text.contains("contentHash"))
        assertFalse(text.contains("officialReleaseLocator"))
        assertFalse(text.contains("credential", ignoreCase = true))
        assertFalse(text.contains("ownerUserId"))
        assertFalse(text.contains("query", ignoreCase = true))
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
            stablePostgresContainer(postgresImage)
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
