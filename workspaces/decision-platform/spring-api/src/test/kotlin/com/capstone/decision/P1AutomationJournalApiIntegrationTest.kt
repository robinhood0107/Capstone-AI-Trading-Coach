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
import org.springframework.test.web.servlet.patch
import org.springframework.test.web.servlet.post
import org.springframework.test.web.servlet.setup.DefaultMockMvcBuilder
import org.springframework.test.web.servlet.setup.MockMvcBuilders
import org.springframework.web.context.WebApplicationContext
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import javax.sql.DataSource

@Testcontainers
@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
        "AUTOMATION_RUNTIME_SHARED_SECRET=automation-runtime-bridge-test-secret-0001",
    ],
)
class P1AutomationJournalApiIntegrationTest(
    @Autowired private val context: WebApplicationContext,
    @Autowired private val objectMapper: ObjectMapper,
    @Autowired private val applicationDataSource: DataSource,
) : SpringApiIntegrationTestBase() {
    private lateinit var mockMvc: MockMvc
    private val ownerJdbc by lazy {
        JdbcTemplate(DriverManagerDataSource(postgres.jdbcUrl, postgres.username, postgres.password))
    }
    private val appJdbc by lazy { JdbcTemplate(applicationDataSource) }

    @BeforeEach
    fun setUp() {
        ownerJdbc.update("delete from journal_idempotency")
        ownerJdbc.update("delete from journals")
        ownerJdbc.update("delete from automation_control_idempotency")
        ownerJdbc.update("delete from automation_positions")
        ownerJdbc.update("delete from automation_runs")
        ownerJdbc.update("delete from automation_control")
        ownerJdbc.update("delete from automation_activation_gate")
        ownerJdbc.update("delete from paper_positions where account_id=?", ACCOUNT_ID)
        ownerJdbc.update("delete from paper_accounts where account_id=?", ACCOUNT_ID)
        ownerJdbc.update("delete from principle_versions where principle_id=?", PRINCIPLE_ID)
        ownerJdbc.update("delete from principles where principle_id=?", PRINCIPLE_ID)
        ownerJdbc.update(
            """
            insert into principles(
              principle_id,user_id,preset_id,title,mode,status,current_version,created_at,updated_at
            ) values (?, 'usr_demo_user', 'balanced', 'Automation fixture', 'GUIDE', 'ACTIVE', 1,
              statement_timestamp(),statement_timestamp())
            """.trimIndent(),
            PRINCIPLE_ID,
        )
        ownerJdbc.update(
            """
            insert into paper_accounts(
              account_id,user_id,name,cash_balance,currency,status,created_at,updated_at,
              owner_scope_hash,margin_requirement_krw
            ) values (?, 'usr_demo_user', 'Automation fixture', 1000000, 'KRW', 'ACTIVE',
              statement_timestamp(),statement_timestamp(),repeat('a',64),0)
            """.trimIndent(),
            ACCOUNT_ID,
        )
        ownerJdbc.update("update risk_kill_switch set active=false where kill_switch_id='GLOBAL'")
        mockMvc =
            MockMvcBuilders
                .webAppContextSetup(context)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    @Test
    fun `status is authenticated owner scoped and absent control is safe DISARMED`() {
        mockMvc.get("/api/v1/automation/status").andExpect {
            status { isUnauthorized() }
            jsonPath("$.error.code") { value("UNAUTHORIZED") }
        }

        val token = login("demo-user", userPassword())
        mockMvc
            .get("/api/v1/automation/status") {
                bearer(token)
                header("X-Request-Id", "req-automation-status-default")
            }.andExpect {
                status { isOk() }
                jsonPath("$.data.contractId") { value("automation-control.v1") }
                jsonPath("$.data.controlState") { value("DISARMED") }
                jsonPath("$.data.projectionState") { value("DISARMED") }
                jsonPath("$.data.version") { value(1) }
                jsonPath("$.data.killSwitchActive") { value(false) }
            }

        mockMvc
            .get("/api/v1/automation/status?ownerUserId=usr_demo_admin") { bearer(token) }
            .andExpect {
                status { isBadRequest() }
                jsonPath("$.error.code") { value("VALIDATION_ERROR") }
            }
    }

    @Test
    fun `V89 RLS and ACL deny unscoped app worker and replay access`() {
        assertEquals(0, appJdbc.queryForObject("select count(*) from automation_control", Int::class.java))
        assertThrows<DataAccessException> {
            appJdbc.update(
                """
                insert into automation_control(
                  user_id,control_state,version,brokerage_mode,account_id,principle_id,strategy_id,
                  baseline_account_digest,certification_status,kill_switch_active
                ) values ('usr_demo_user','DISARMED',1,'INTERNAL_PAPER',?,?,?,repeat('a',64),
                  'NOT_REQUIRED_INTERNAL_PAPER',false)
                """.trimIndent(),
                ACCOUNT_ID,
                PRINCIPLE_ID,
                STRATEGY_ID,
            )
        }
        listOf("decision_worker" to "worker-test-secret-0001", "decision_replay" to "replay-test-secret-0001")
            .forEach { (role, password) ->
                val jdbc = JdbcTemplate(DriverManagerDataSource(postgres.jdbcUrl, role, password))
                assertThrows<DataAccessException> { jdbc.queryForObject("select count(*) from journals", Int::class.java) }
                assertThrows<DataAccessException> {
                    jdbc.queryForObject("select count(*) from automation_runs", Int::class.java)
                }
            }
    }

    @Test
    fun `internal automation bridge is loopback secret bound and hides unknown owners`() {
        val body =
            objectMapper.writeValueAsString(
                mapOf(
                    "operation" to "BALANCE",
                    "userId" to "usr_demo_user",
                    "idempotencyKey" to null,
                    "payload" to mapOf("accountId" to KIS_ACCOUNT_ID),
                ),
            )
        mockMvc
            .post("/internal/automation-runtime/command") {
                contentType = MediaType.APPLICATION_JSON
                content = body
            }.andExpect {
                status { isNotFound() }
                jsonPath("$.status") { value("NOT_FOUND") }
            }

        mockMvc
            .post("/internal/automation-runtime/command") {
                contentType = MediaType.APPLICATION_JSON
                header("X-Automation-Runtime-Auth", "automation-runtime-bridge-test-secret-0001")
                content = body.replace("usr_demo_user", "usr_missing_runtime_0001")
            }.andExpect {
                status { isNotFound() }
                jsonPath("$.status") { value("NOT_FOUND") }
            }
    }

    @Test
    fun `INTERNAL PAPER arm replays hash only and rejects same key different request`() {
        val token = login("demo-user", userPassword())
        val key = "automation-arm-key-0001"
        val body = armBody(expectedVersion = 1)
        val first = arm(token, key, body, "req-automation-arm-first")
        val replay = arm(token, key, body, "req-automation-arm-replay")

        assertEquals(2, json(first).at("/data/version").intValue())
        assertEquals(json(first).path("data"), json(replay).path("data"))
        assertEquals(1, count("automation_control_idempotency"))
        assertFalse(
            ownerJdbc
                .queryForObject(
                    "select result_json::text||scope_hash||request_hash from automation_control_idempotency",
                    String::class.java,
                )!!
                .contains(key),
        )

        mockMvc
            .post("/api/v1/automation/arm") {
                bearer(token)
                contentType = MediaType.APPLICATION_JSON
                header("X-Idempotency-Key", key)
                content = objectMapper.writeValueAsString(armBody(expectedVersion = 2))
            }.andExpect {
                status { isConflict() }
                jsonPath("$.error.code") { value("IDEMPOTENCY_CONFLICT") }
            }
    }

    @Test
    fun `concurrent arm requests with the same expected version have one winner`() {
        val token = login("demo-user", userPassword())
        val ready = CountDownLatch(2)
        val start = CountDownLatch(1)
        val executor = Executors.newFixedThreadPool(2)
        try {
            val futures =
                (1..2).map { index ->
                    executor.submit<Int> {
                        ready.countDown()
                        check(start.await(5, TimeUnit.SECONDS))
                        mockMvc
                            .post("/api/v1/automation/arm") {
                                bearer(token)
                                contentType = MediaType.APPLICATION_JSON
                                header("X-Idempotency-Key", "automation-concurrent-key-000$index")
                                content = objectMapper.writeValueAsString(armBody(1))
                            }.andReturn()
                            .response.status
                    }
                }
            assertTrue(ready.await(5, TimeUnit.SECONDS))
            start.countDown()
            assertEquals(listOf(200, 409), futures.map { it.get(15, TimeUnit.SECONDS) }.sorted())
        } finally {
            executor.shutdownNow()
        }
        assertEquals(1, count("automation_control"))
        assertEquals(2, ownerJdbc.queryForObject("select version from automation_control", Int::class.java))
    }

    @Test
    fun `KIS MOCK arm requires server gates and detects baseline drift`() {
        val token = login("demo-user", userPassword())
        val kisBody =
            mapOf(
                "brokerageMode" to "KIS_MOCK",
                "accountId" to KIS_ACCOUNT_ID,
                "principleId" to PRINCIPLE_ID,
                "strategyId" to STRATEGY_ID,
                "expectedVersion" to 1,
            )
        mockMvc
            .post("/api/v1/automation/arm") {
                bearer(token)
                contentType = MediaType.APPLICATION_JSON
                header("X-Idempotency-Key", "automation-kis-closed-0001")
                content = objectMapper.writeValueAsString(kisBody)
            }.andExpect {
                status { isConflict() }
                jsonPath("$.error.code") { value("CONFLICT") }
            }

        insertRealTeamBPointer()
        insertKisBalance("obs_kis_automation_0001", 1000000, "1".repeat(64), "2026-08-27T00:00:00Z")
        ownerJdbc.update(
            """
            insert into automation_activation_gate(
              user_id,certification_status,clean_release_binding,real_team_b_pointer_active,
              release_binding_sha256,updated_at
            ) values ('usr_demo_user','VALID',true,true,repeat('9',64),statement_timestamp())
            """.trimIndent(),
        )
        assertEquals(31, ownerJdbc.queryForObject("select count(*) from current_p1_return_signal_pointer", Int::class.java))
        assertEquals(
            1,
            ownerJdbc.queryForObject(
                "select count(distinct bundle_sha256) from current_p1_return_signal_pointer",
                Int::class.java,
            ),
        )
        assertEquals(
            1,
            ownerJdbc.queryForObject(
                """
                select count(*) from automation_activation_gate
                where user_id='usr_demo_user' and certification_status='VALID'
                  and clean_release_binding and real_team_b_pointer_active
                  and release_binding_sha256 is not null
                """.trimIndent(),
                Int::class.java,
            ),
        )
        mockMvc
            .post("/api/v1/automation/arm") {
                bearer(token)
                contentType = MediaType.APPLICATION_JSON
                header("X-Idempotency-Key", "automation-kis-open-0001")
                content = objectMapper.writeValueAsString(kisBody)
            }.andExpect {
                status { isOk() }
                jsonPath("$.data.brokerageMode") { value("KIS_MOCK") }
                jsonPath("$.data.certificationStatus") { value("VALID") }
                jsonPath("$.data.version") { value(2) }
            }
        mockMvc
            .post("/api/v1/automation/disarm") {
                bearer(token)
                contentType = MediaType.APPLICATION_JSON
                header("X-Idempotency-Key", "automation-kis-disarm-0001")
                content = """{"expectedVersion":2}"""
            }.andExpect { status { isOk() } }

        insertKisBalance("obs_kis_automation_0002", 900000, "2".repeat(64), "2026-08-27T00:01:00Z")
        mockMvc
            .post("/api/v1/automation/arm") {
                bearer(token)
                contentType = MediaType.APPLICATION_JSON
                header("X-Idempotency-Key", "automation-kis-drift-0001")
                content = objectMapper.writeValueAsString(kisBody + ("expectedVersion" to 3))
            }.andExpect {
                status { isConflict() }
                jsonPath("$.error.code") { value("CONFLICT") }
            }
    }

    @Test
    fun `disarm preserves pending run and position while status remains RUNNING`() {
        val token = login("demo-user", userPassword())
        arm(token, "automation-arm-key-0002", armBody(1), "req-automation-arm-preserve")
        insertRun(OWNED_RUN_ID, "usr_demo_user", "PENDING_RECONCILIATION")
        ownerJdbc.update(
            """
            insert into automation_positions(
              position_id,user_id,account_id,symbol,quantity,entry_session,expiry_session,status,
              bot_owned,short_allowed,created_at,closed_at
            ) values ('auto_pos_owned_0001','usr_demo_user',?,'005930',1,'2026-08-18','2026-08-25',
              'OPEN',true,false,statement_timestamp(),null)
            """.trimIndent(),
            ACCOUNT_ID,
        )

        mockMvc
            .post("/api/v1/automation/disarm") {
                bearer(token)
                contentType = MediaType.APPLICATION_JSON
                header("X-Idempotency-Key", "automation-disarm-key-0001")
                content = """{"expectedVersion":2}"""
            }.andExpect {
                status { isOk() }
                jsonPath("$.data.controlState") { value("DISARMED") }
                jsonPath("$.data.version") { value(3) }
            }
        assertEquals(1, count("automation_runs"))
        assertEquals(1, count("automation_positions"))
        mockMvc
            .get("/api/v1/automation/status") { bearer(token) }
            .andExpect {
                status { isOk() }
                jsonPath("$.data.controlState") { value("DISARMED") }
                jsonPath("$.data.projectionState") { value("RUNNING") }
            }
    }

    @Test
    fun `runs are stable bounded and cross owner rows are hidden`() {
        val token = login("demo-user", userPassword())
        insertRun(OWNED_RUN_ID, "usr_demo_user", "COMPLETED")
        insertRun("auto_run_owned_0002", "usr_demo_user", "HALTED")
        insertRun("auto_run_foreign_0001", "usr_demo_admin", "HALTED")

        val first =
            mockMvc
                .get("/api/v1/automation/runs?size=1") { bearer(token) }
                .andExpect {
                    status { isOk() }
                    jsonPath("$.data.items.length()") { value(1) }
                    jsonPath("$.data.items[0].contractId") { value("automation-run.v1") }
                    jsonPath("$.data.nextCursor") { isString() }
                }.andReturn()
        val firstId = json(first).at("/data/items/0/runId").stringValue()
        val cursor = json(first).at("/data/nextCursor").stringValue()
        val second =
            mockMvc
                .get("/api/v1/automation/runs?size=1&cursor=$cursor") { bearer(token) }
                .andExpect {
                    status { isOk() }
                    jsonPath("$.data.items.length()") { value(1) }
                }.andReturn()
        assertNotEquals(firstId, json(second).at("/data/items/0/runId").stringValue())
    }

    @Test
    fun `Journal list cursor is stable and bounded`() {
        val token = login("demo-user", userPassword())
        val firstCreated = createJournal(token, "journal-page-create-0001", journalBody("첫째", "첫째 본문", null))
        val secondCreated = createJournal(token, "journal-page-create-0002", journalBody("둘째", "둘째 본문", null))
        val expectedIds =
            setOf(json(firstCreated).at("/data/journalId").stringValue(), json(secondCreated).at("/data/journalId").stringValue())

        val firstPage =
            mockMvc
                .get("/api/v1/journals?size=1") { bearer(token) }
                .andExpect {
                    status { isOk() }
                    jsonPath("$.data.items.length()") { value(1) }
                    jsonPath("$.data.nextCursor") { isString() }
                }.andReturn()
        val firstId = json(firstPage).at("/data/items/0/journalId").stringValue()
        val cursor = json(firstPage).at("/data/nextCursor").stringValue()
        val secondPage =
            mockMvc
                .get("/api/v1/journals?size=1&cursor=$cursor") { bearer(token) }
                .andExpect {
                    status { isOk() }
                    jsonPath("$.data.items.length()") { value(1) }
                }.andReturn()
        val secondId = json(secondPage).at("/data/items/0/journalId").stringValue()
        assertEquals(expectedIds, setOf(firstId, secondId))
    }

    @Test
    fun `Journal create replace delete are replay safe owner scoped and exclude deleted rows`() {
        val userToken = login("demo-user", userPassword())
        val adminToken = login("demo-admin", adminPassword())
        insertRun(OWNED_RUN_ID, "usr_demo_user", "COMPLETED")
        insertRun("auto_run_foreign_0002", "usr_demo_admin", "COMPLETED")
        val createKey = "journal-create-key-0001"
        val createBody = journalBody("첫 기록", "근거를 확인했다.", OWNED_RUN_ID)

        val created = createJournal(userToken, createKey, createBody)
        val replayed = createJournal(userToken, createKey, createBody)
        val journalId = json(created).at("/data/journalId").stringValue()
        assertTrue(journalId.matches(Regex("^jnl_[0-9a-f]{32}$")))
        assertEquals(json(created).path("data"), json(replayed).path("data"))
        assertEquals(1, count("journals"))
        assertEquals(1, count("journal_idempotency"))

        mockMvc
            .post("/api/v1/journals") {
                bearer(userToken)
                contentType = MediaType.APPLICATION_JSON
                header("X-Idempotency-Key", createKey)
                content = objectMapper.writeValueAsString(journalBody("다른 기록", "다른 본문", OWNED_RUN_ID))
            }.andExpect {
                status { isConflict() }
                jsonPath("$.error.code") { value("IDEMPOTENCY_CONFLICT") }
            }

        mockMvc
            .patch("/api/v1/journals/$journalId") {
                bearer(adminToken)
                contentType = MediaType.APPLICATION_JSON
                header("X-Idempotency-Key", "journal-foreign-key-0001")
                content = objectMapper.writeValueAsString(replaceBody(1, "침범", "차단", null))
            }.andExpect {
                status { isNotFound() }
                jsonPath("$.error.code") { value("NOT_FOUND") }
            }

        val replaced =
            mockMvc
                .patch("/api/v1/journals/$journalId") {
                    bearer(userToken)
                    contentType = MediaType.APPLICATION_JSON
                    header("X-Idempotency-Key", "journal-patch-key-0001")
                    content = objectMapper.writeValueAsString(replaceBody(1, "수정 기록", "교훈을 정리했다.", OWNED_RUN_ID))
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.data.version") { value(2) }
                    jsonPath("$.data.title") { value("수정 기록") }
                }.andReturn()
        assertNotEquals(json(created).at("/data/updatedAt").stringValue(), json(replaced).at("/data/updatedAt").stringValue())

        mockMvc
            .patch("/api/v1/journals/$journalId") {
                bearer(userToken)
                contentType = MediaType.APPLICATION_JSON
                header("X-Idempotency-Key", "test:test:test:0001")
                content = objectMapper.writeValueAsString(replaceBody(1, "stale", "stale", OWNED_RUN_ID))
            }.andExpect {
                status { isConflict() }
                jsonPath("$.error.code") { value("CONFLICT") }
            }

        mockMvc
            .delete("/api/v1/journals/$journalId") {
                bearer(userToken)
                contentType = MediaType.APPLICATION_JSON
                header("X-Idempotency-Key", "journal-delete-key-0001")
                content = """{"expectedVersion":2}"""
            }.andExpect {
                status { isOk() }
                jsonPath("$.data.version") { value(3) }
                jsonPath("$.data.deletedAt") { exists() }
            }
        mockMvc
            .get("/api/v1/journals") { bearer(userToken) }
            .andExpect {
                status { isOk() }
                jsonPath("$.data.items.length()") { value(0) }
            }
        mockMvc
            .delete("/api/v1/journals/$journalId") {
                bearer(userToken)
                contentType = MediaType.APPLICATION_JSON
                header("X-Idempotency-Key", "journal-redelete-key-0001")
                content = """{"expectedVersion":3}"""
            }.andExpect {
                status { isNotFound() }
                jsonPath("$.error.code") { value("NOT_FOUND") }
            }
    }

    @Test
    fun `strict parsers reject missing keys duplicate unknown oversized and foreign links`() {
        val token = login("demo-user", userPassword())
        mockMvc
            .post("/api/v1/automation/arm") {
                bearer(token)
                contentType = MediaType.APPLICATION_JSON
                content = objectMapper.writeValueAsString(armBody(1))
            }.andExpect { status { isBadRequest() } }
        mockMvc
            .post("/api/v1/automation/arm") {
                bearer(token)
                contentType = MediaType.APPLICATION_JSON
                header("X-Idempotency-Key", "automation-duplicate-0001")
                content = """{"brokerageMode":"INTERNAL_PAPER","brokerageMode":"KIS_MOCK"}"""
            }.andExpect { status { isBadRequest() } }
        mockMvc
            .post("/api/v1/journals") {
                bearer(token)
                contentType = MediaType.APPLICATION_JSON
                header("X-Idempotency-Key", "journal-unknown-0001")
                content = """{"title":"t","content":"c","tags":[],"links":{},"ownerUserId":"usr_demo_user"}"""
            }.andExpect { status { isBadRequest() } }
        mockMvc
            .post("/api/v1/journals") {
                bearer(token)
                contentType = MediaType.APPLICATION_JSON
                header("X-Idempotency-Key", "journal-oversize-0001")
                content = objectMapper.writeValueAsString(journalBody("t", "x".repeat(8193), null))
            }.andExpect { status { isBadRequest() } }
        mockMvc
            .post("/api/v1/journals") {
                bearer(token)
                contentType = MediaType.APPLICATION_JSON
                header("X-Idempotency-Key", "journal-link-foreign-0001")
                content = objectMapper.writeValueAsString(journalBody("t", "c", "auto_run_foreign_0002"))
            }.andExpect { status { isNotFound() } }
    }

    private fun arm(
        token: String,
        key: String,
        body: Map<String, Any>,
        requestId: String,
    ): MvcResult =
        mockMvc
            .post("/api/v1/automation/arm") {
                bearer(token)
                contentType = MediaType.APPLICATION_JSON
                header("X-Idempotency-Key", key)
                header("X-Request-Id", requestId)
                content = objectMapper.writeValueAsString(body)
            }.andExpect {
                status { isOk() }
                jsonPath("$.data.controlState") { value("ARMED") }
                jsonPath("$.data.brokerageMode") { value("INTERNAL_PAPER") }
            }.andReturn()

    private fun createJournal(
        token: String,
        key: String,
        body: Map<String, Any>,
    ): MvcResult =
        mockMvc
            .post("/api/v1/journals") {
                bearer(token)
                contentType = MediaType.APPLICATION_JSON
                header("X-Idempotency-Key", key)
                content = objectMapper.writeValueAsString(body)
            }.andExpect {
                status { isOk() }
                jsonPath("$.data.contractId") { value("journal.v1") }
            }.andReturn()

    private fun armBody(expectedVersion: Int): Map<String, Any> =
        mapOf(
            "brokerageMode" to "INTERNAL_PAPER",
            "accountId" to ACCOUNT_ID,
            "principleId" to PRINCIPLE_ID,
            "strategyId" to STRATEGY_ID,
            "expectedVersion" to expectedVersion,
        )

    private fun journalBody(
        title: String,
        content: String,
        automationRunId: String?,
    ): Map<String, Any> =
        mapOf(
            "title" to title,
            "content" to content,
            "tags" to listOf("회고", "risk"),
            "links" to mapOf("automationRunId" to automationRunId),
        )

    private fun replaceBody(
        expectedVersion: Int,
        title: String,
        content: String,
        automationRunId: String?,
    ): Map<String, Any> = journalBody(title, content, automationRunId) + ("expectedVersion" to expectedVersion)

    private fun insertRun(
        runId: String,
        userId: String,
        state: String,
    ) {
        ownerJdbc.update(
            """
            insert into automation_runs(
              run_id,user_id,session_date,state,brokerage_mode,selected_symbol,selected_side,
              physical_submit_count,vertex_call_count,provider_calls,started_at,updated_at
            ) values (?,?,'2026-08-18',?,'INTERNAL_PAPER',null,null,0,0,0,
              statement_timestamp(),statement_timestamp())
            """.trimIndent(),
            runId,
            userId,
            state,
        )
    }

    private fun insertRealTeamBPointer() {
        val bundleSha = "8".repeat(64)
        ownerJdbc.update(
            """
            insert into p1_return_artifact_bundle(
              bundle_sha256,artifact_id,run_id,input_pack_sha256,manifest_sha256,packet_sha256,
              evidence_mode,real_team_b,model_quality,mock_runtime_eligible,session_date,as_of,
              fresh_until,model_projection_sha256,backtest_projection_sha256,imported_at
            ) values (?,?,'run_real_team_b_0001',repeat('3',64),?,repeat('4',64),'REAL_TEAM_B',
              true,'PASS',true,'2026-08-26','2026-08-26T08:10:00+09:00',
              '2026-08-27T08:10:00+09:00',repeat('5',64),repeat('6',64),statement_timestamp())
            """.trimIndent(),
            bundleSha,
            "artifact_p1_${bundleSha.take(24)}",
            bundleSha,
        )
        (1..30).map { it.toString().padStart(6, '0') }.plus("132030").forEach { symbol ->
            ownerJdbc.update(
                """
                insert into p1_return_signal_projection(
                  bundle_sha256,producer,symbol,session_date,as_of,signal,confidence,predicted_return,
                  model_version,model_report_id,payload_sha256,fixture
                ) values (?,'LSTM',?,'2026-08-26','2026-08-26T08:10:00+09:00','HOLD',0.5,0,
                  'real-team-b-v1','mrp_real_team_b_0001',repeat('7',64),false)
                """.trimIndent(),
                bundleSha,
                symbol,
            )
        }
    }

    private fun insertKisBalance(
        observationId: String,
        cashKrw: Long,
        artifactHash: String,
        observedAt: String,
    ) {
        ownerJdbc.update(
            """
            insert into portfolio_balance_observations(
              observation_id,owner_user_id,account_scope_hash,source,context_status,cash_krw,
              portfolio_equity_krw,margin_requirement_krw,completeness,position_count,observed_at,
              received_at,schema_version,source_version,payload_json,source_ref,artifact_hash
            ) values (?,'usr_demo_user',?,'KIS_MOCK','ACTIVE',?,1000000,0,'COMPLETE',0,
              CAST(? AS timestamptz),CAST(? AS timestamptz)+interval '1 second','1','fixture','{}'::jsonb,
              repeat('a',64),?)
            """.trimIndent(),
            observationId,
            KIS_ACCOUNT_SCOPE_HASH,
            cashKrw,
            observedAt,
            observedAt,
            artifactHash,
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
                    header("X-Request-Id", "req-p1-owner-login-$username")
                }.andExpect { status { isOk() } }
                .andReturn()
        return json(response).at("/data/accessToken").stringValue()
    }

    private fun count(table: String): Int = requireNotNull(ownerJdbc.queryForObject("select count(*) from $table", Int::class.java))

    private fun json(result: MvcResult): JsonNode = objectMapper.readTree(result.response.contentAsByteArray)

    companion object {
        private const val ACCOUNT_ID = "acct_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        private const val KIS_ACCOUNT_ID = "acct_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        private const val KIS_ACCOUNT_SCOPE_HASH = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbcccccccccccccccccccccccccccccccc"
        private const val PRINCIPLE_ID = "prc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        private const val STRATEGY_ID = "strategy_aaaaaaaa"
        private const val OWNED_RUN_ID = "auto_run_owned_0001"
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            stablePostgresContainer(postgresImage)
                .withDatabaseName("decision_p1_automation_journal")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")

        @DynamicPropertySource
        @JvmStatic
        fun postgresProperties(registry: DynamicPropertyRegistry) {
            registry.add("spring.datasource.url", postgres::getJdbcUrl)
            registry.add("spring.datasource.username") { "decision_app" }
            registry.add("spring.datasource.password") { "app-test" }
            registry.add("spring.flyway.user") { "flyway" }
            registry.add("spring.flyway.password") { "flyway-test" }
        }
    }
}
