package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.http.MediaType
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.MvcResult
import org.springframework.test.web.servlet.get
import org.springframework.test.web.servlet.post
import org.springframework.test.web.servlet.put
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

// S2.1 wire, owner, CAS, history를 실제 JWT와 PostgreSQL transaction 경계에서 검증한다.
@Testcontainers
@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
class PrincipleApiIntegrationTest(
    @Autowired private val webApplicationContext: WebApplicationContext,
    @Autowired private val objectMapper: ObjectMapper,
    @Autowired private val jdbcTemplate: JdbcTemplate,
) : SpringApiIntegrationTestBase() {
    private lateinit var mockMvc: MockMvc

    @BeforeEach
    fun setUp() {
        jdbcTemplate.update("delete from audit_logs where target_type = 'PRINCIPLE'")
        jdbcTemplate.update("delete from principle_versions")
        jdbcTemplate.update("delete from principles")
        mockMvc =
            MockMvcBuilders
                .webAppContextSetup(webApplicationContext)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    @Test
    fun `preset endpoint requires authentication and returns the exact database-backed three by eight catalog`() {
        mockMvc
            .get("/api/v1/principle-presets") {
                header("X-Request-Id", "req-principle-preset-unauthorized")
            }.andExpect {
                status { isUnauthorized() }
                jsonPath("$.error.code") { value("UNAUTHORIZED") }
                jsonPath("$.error.details") { isEmpty() }
            }

        val token = login("demo-user", userPassword())
        val response =
            mockMvc
                .get("/api/v1/principle-presets") {
                    bearer(token)
                    header("X-Request-Id", "req-principle-presets")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.success") { value(true) }
                    jsonPath("$.requestId") { value("req-principle-presets") }
                    jsonPath("$.data.items.length()") { value(3) }
                    jsonPath("$.data.items[0].presetId") { value("conservative") }
                    jsonPath("$.data.items[1].presetId") { value("balanced") }
                    jsonPath("$.data.items[2].presetId") { value("aggressive") }
                    jsonPath("$.data.items[0].defaultRules.length()") { value(8) }
                    jsonPath("$.data.items[1].defaultRules.length()") { value(8) }
                    jsonPath("$.data.items[2].defaultRules.length()") { value(8) }
                    jsonPath("$.data.disclaimer.ko") {
                        value("이 프리셋은 교육·시연용 기본값이며 투자 권유, 개인별 적합성 판단 또는 손실 방지를 보장하지 않습니다.")
                    }
                    jsonPath("$.warnings") { isEmpty() }
                    jsonPath("$.error") { doesNotExist() }
                }.andReturn()

        val responseItems = json(response).at("/data/items")
        val databaseRules =
            jdbcTemplate.queryForList(
                "select rules_json::text from principle_presets order by display_order",
                String::class.java,
            )
        assertEquals(
            databaseRules.map(objectMapper::readTree),
            responseItems.values().map { it.path("defaultRules") },
        )

        assertValidation(
            mockMvc
                .get("/api/v1/principle-presets?unexpected=value") {
                    bearer(token)
                    header("X-Request-Id", "req-principle-preset-query")
                }.andReturn(),
            "/query/unexpected",
            "UNKNOWN_FIELD",
        )
    }

    @Test
    fun `create canonicalizes the title and atomically deep copies preset snapshot and sanitized audit`() {
        val token = login("demo-user", userPassword())
        val response =
            create(
                token = token,
                requestId = "req-principle-create",
                body =
                    mapOf(
                        "presetId" to "balanced",
                        "title" to "  e\u0301 원칙  ",
                    ),
            )
        val body = json(response)
        val principleId = body.at("/data/principleId").stringValue()

        assertEquals(201, response.response.status)
        assertTrue(PRINCIPLE_ID.matches(principleId))
        assertEquals("/api/v1/principles/$principleId", response.response.getHeader("Location"))
        assertEquals("é 원칙", body.at("/data/title").stringValue())
        assertEquals("GUIDE", body.at("/data/mode").stringValue())
        assertEquals("ACTIVE", body.at("/data/status").stringValue())
        assertEquals(1, body.at("/data/version").intValue())
        assertEquals(8, body.at("/data/rules").size())
        assertTrue(body.at("/data/createdAt").stringValue().endsWith("+09:00"))
        assertEquals(body.at("/data/createdAt"), body.at("/data/updatedAt"))

        assertEquals(1, count("select count(*) from principles where principle_id = ?", principleId))
        assertEquals(1, count("select count(*) from principle_versions where principle_id = ?", principleId))
        assertEquals(
            1,
            count(
                """
                select count(*) from audit_logs
                where target_type = 'PRINCIPLE'
                  and target_id = ?
                  and action = 'PRINCIPLE_CREATED'
                  and payload_json ??& array['principleId','newVersion','changedFields']
                  and payload_json - array['principleId','newVersion','changedFields'] = '{}'::jsonb
                """.trimIndent(),
                principleId,
            ),
        )
        assertEquals(
            body.at("/data/rules"),
            objectMapper.readTree(
                jdbcTemplate.queryForObject(
                    "select rules_json::text from principle_versions where principle_id = ? and version = 1",
                    String::class.java,
                    principleId,
                ),
            ),
        )
    }

    @Test
    fun `owner list detail and cursor hide cross owner rows and reject malformed or mismatched input`() {
        val userToken = login("demo-user", userPassword())
        val adminToken = login("demo-admin", adminPassword())
        val first = createdId(create(userToken, "req-owner-first", createBody("balanced", "첫 원칙")))
        val second = createdId(create(userToken, "req-owner-second", createBody("aggressive", "둘째 원칙")))
        val adminOwned = createdId(create(adminToken, "req-owner-admin", createBody("conservative", "관리자 원칙")))

        val firstPage =
            mockMvc
                .get("/api/v1/principles?size=1&sort=UPDATED_AT_DESC") {
                    bearer(userToken)
                    header("X-Request-Id", "req-owner-list-first")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.data.items.length()") { value(1) }
                    jsonPath("$.data.items[0].rules") { doesNotExist() }
                    jsonPath("$.data.nextCursor") { isString() }
                }.andReturn()
        val firstPageBody = json(firstPage)
        val cursor = firstPageBody.at("/data/nextCursor").stringValue()
        assertFalse(firstPageBody.toString().contains(adminOwned))

        val secondPage =
            mockMvc
                .get("/api/v1/principles?cursor=$cursor&size=1&sort=UPDATED_AT_DESC") {
                    bearer(userToken)
                    header("X-Request-Id", "req-owner-list-second")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.data.items.length()") { value(1) }
                    jsonPath("$.data.nextCursor") { doesNotExist() }
                }.andReturn()
        assertEquals(
            setOf(first, second),
            setOf(
                firstPageBody.at("/data/items/0/principleId").stringValue(),
                json(secondPage).at("/data/items/0/principleId").stringValue(),
            ),
        )

        assertValidation(
            mockMvc
                .get("/api/v1/principles?cursor=$cursor&size=2&sort=UPDATED_AT_DESC") {
                    bearer(userToken)
                    header("X-Request-Id", "req-owner-list-mismatch")
                }.andReturn(),
            "/query/cursor",
            "INVALID_CURSOR",
        )
        assertValidation(
            mockMvc
                .get("/api/v1/principles?cursor=${cursor.dropLast(1)}x&size=1&sort=UPDATED_AT_DESC") {
                    bearer(userToken)
                    header("X-Request-Id", "req-owner-list-tamper")
                }.andReturn(),
            "/query/cursor",
            "INVALID_CURSOR",
        )
        assertValidation(
            mockMvc
                .get("/api/v1/principles?cursor=$cursor") {
                    bearer(adminToken)
                    header("X-Request-Id", "req-owner-list-subject-mismatch")
                }.andReturn(),
            "/query/cursor",
            "INVALID_CURSOR",
        )
        assertValidation(
            mockMvc
                .get("/api/v1/principles/$first/versions?cursor=$cursor") {
                    bearer(userToken)
                    header("X-Request-Id", "req-owner-list-route-mismatch")
                }.andReturn(),
            "/query/cursor",
            "INVALID_CURSOR",
        )
        assertValidation(
            mockMvc
                .get("/api/v1/principles/not-a-principle") {
                    bearer(userToken)
                    header("X-Request-Id", "req-owner-malformed-id")
                }.andReturn(),
            "/path/principleId",
            "INVALID_FORMAT",
        )

        val userCrossOwner =
            mockMvc
                .get("/api/v1/principles/$adminOwned") {
                    bearer(userToken)
                    header("X-Request-Id", "req-owner-cross")
                }.andExpect {
                    status { isNotFound() }
                    jsonPath("$.error.code") { value("NOT_FOUND") }
                    jsonPath("$.error.details") { isEmpty() }
                    jsonPath("$.error.details.currentVersion") { doesNotExist() }
                }.andReturn()
                .response
                .contentAsString
        val userMissing =
            mockMvc
                .get("/api/v1/principles/prc_ffffffffffffffffffffffffffffffff") {
                    bearer(userToken)
                    header("X-Request-Id", "req-owner-cross")
                }.andExpect {
                    status { isNotFound() }
                }.andReturn()
                .response
                .contentAsString
        assertEquals(userMissing, userCrossOwner)
    }

    @Test
    fun `update preserves canonical no op and appends only real changes to history and audit`() {
        val token = login("demo-user", userPassword())
        val created = create(token, "req-update-create", createBody("balanced", "초기 원칙"))
        val createdBody = json(created).at("/data")
        val principleId = createdBody.path("principleId").stringValue()
        val unchangedRequest =
            mapOf(
                "expectedVersion" to 1,
                "title" to createdBody.path("title").stringValue(),
                "mode" to createdBody.path("mode").stringValue(),
                "status" to createdBody.path("status").stringValue(),
                "rules" to createdBody.path("rules"),
            )

        val noOp = update(token, principleId, "req-update-no-op", unchangedRequest)
        assertEquals(200, noOp.response.status)
        assertEquals(createdBody.path("version"), json(noOp).at("/data/version"))
        assertEquals(createdBody.path("updatedAt"), json(noOp).at("/data/updatedAt"))
        assertEquals(1, count("select count(*) from principle_versions where principle_id = ?", principleId))
        assertEquals(1, count("select count(*) from audit_logs where target_type = 'PRINCIPLE' and target_id = ?", principleId))

        val changedRules = listOf(rule("max_position_per_asset", "POSITION_LIMIT", "asset_weight", "<=", "0.1250", "BLOCK", true))
        val changedRequest =
            mapOf(
                "expectedVersion" to 1,
                "title" to "보관된 엄격 원칙",
                "mode" to "STRICT",
                "status" to "ARCHIVED",
                "rules" to changedRules,
            )
        val changed = update(token, principleId, "req-update-change", changedRequest)
        assertEquals(200, changed.response.status)
        assertEquals(2, json(changed).at("/data/version").intValue())
        assertEquals("ARCHIVED", json(changed).at("/data/status").stringValue())
        assertEquals(2, count("select count(*) from principle_versions where principle_id = ?", principleId))
        assertEquals(
            "PRINCIPLE_ARCHIVED",
            jdbcTemplate.queryForObject(
                "select action from audit_logs where target_id = ? order by created_at desc limit 1",
                String::class.java,
                principleId,
            ),
        )

        update(token, principleId, "req-update-stale", changedRequest)
            .also { stale ->
                assertEquals(409, stale.response.status)
                assertEquals("CONFLICT", json(stale).at("/error/code").stringValue())
                assertEquals(1, json(stale).at("/error/details/expectedVersion").intValue())
                assertEquals(2, json(stale).at("/error/details/currentVersion").intValue())
            }

        val historyFirst =
            mockMvc
                .get("/api/v1/principles/$principleId/versions?size=1&sort=VERSION_DESC") {
                    bearer(token)
                    header("X-Request-Id", "req-history-first")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.data.items[0].version") { value(2) }
                    jsonPath("$.data.items[0].changedFields[0]") { value("title") }
                    jsonPath("$.data.items[0].changedFields[1]") { value("mode") }
                    jsonPath("$.data.items[0].changedFields[2]") { value("status") }
                    jsonPath("$.data.items[0].changedFields[3]") { value("rules") }
                    jsonPath("$.data.items[0].createdBy") { doesNotExist() }
                    jsonPath("$.data.nextCursor") { isString() }
                }.andReturn()
        val cursor = json(historyFirst).at("/data/nextCursor").stringValue()
        mockMvc
            .get("/api/v1/principles/$principleId/versions?cursor=$cursor&size=1&sort=VERSION_DESC") {
                bearer(token)
                header("X-Request-Id", "req-history-second")
            }.andExpect {
                status { isOk() }
                jsonPath("$.data.items[0].version") { value(1) }
                jsonPath("$.data.items[0].changedFields.length()") { value(5) }
                jsonPath("$.data.nextCursor") { doesNotExist() }
            }
    }

    @Test
    fun `same version concurrent updates produce one success and one conflict without a lost snapshot`() {
        val token = login("demo-user", userPassword())
        val created = create(token, "req-race-create", createBody("balanced", "race base"))
        val body = json(created).at("/data")
        val principleId = body.path("principleId").stringValue()
        val start = CountDownLatch(1)
        val executor = Executors.newFixedThreadPool(2)

        val futures =
            listOf("race winner A", "race winner B").mapIndexed { index, title ->
                executor.submit<Int> {
                    start.await()
                    update(
                        token = token,
                        principleId = principleId,
                        requestId = "req-race-$index",
                        body =
                            mapOf(
                                "expectedVersion" to 1,
                                "title" to title,
                                "mode" to "GUIDE",
                                "status" to "ACTIVE",
                                "rules" to body.path("rules"),
                            ),
                    ).response.status
                }
            }
        start.countDown()
        val statuses = futures.map { it.get(15, TimeUnit.SECONDS) }.sorted()
        executor.shutdownNow()

        assertEquals(listOf(200, 409), statuses)
        assertEquals(2, count("select current_version from principles where principle_id = ?", principleId))
        assertEquals(2, count("select count(*) from principle_versions where principle_id = ?", principleId))
        assertEquals(2, count("select count(*) from audit_logs where target_type = 'PRINCIPLE' and target_id = ?", principleId))
    }

    @Test
    fun `strict validation rejects unknown duplicate malformed scale tuple and reflected injection values`() {
        val token = login("demo-user", userPassword())
        val injected = "' OR 1=1 --"
        val invalidBody =
            """
            {
              "presetId":"balanced",
              "title":"bad\n",
              "ownerUserId":"$injected",
              "rules":[
                {
                  "ruleId":"max_position_per_asset",
                  "ruleType":"POSITION_LIMIT",
                  "metric":"order_amount_krw",
                  "operator":"<=",
                  "threshold":0.12345,
                  "severity":"ALLOW",
                  "enabled":true
                },
                {
                  "ruleId":"max_position_per_asset",
                  "ruleType":"POSITION_LIMIT",
                  "metric":"asset_weight",
                  "operator":"<=",
                  "threshold":0.2,
                  "severity":"BLOCK",
                  "enabled":true
                }
              ]
            }
            """.trimIndent()
        val response =
            mockMvc
                .post("/api/v1/principles") {
                    bearer(token)
                    contentType = MediaType.APPLICATION_JSON
                    content = invalidBody
                    header("X-Request-Id", "req-validation-matrix")
                }.andExpect {
                    status { isBadRequest() }
                    jsonPath("$.error.code") { value("VALIDATION_ERROR") }
                    jsonPath("$.error.details.violations") { isArray() }
                }.andReturn()
        val responseText = response.response.contentAsString
        val violations = json(response).at("/error/details/violations")
        val fields = violations.values().map { it.path("field").stringValue() }

        assertEquals(fields.sorted(), fields)
        assertTrue(fields.contains("/ownerUserId"))
        assertTrue(fields.contains("/rules/0/metric"))
        assertTrue(fields.contains("/rules/0/severity"))
        assertTrue(fields.contains("/rules/0/threshold"))
        assertTrue(fields.contains("/rules/1/ruleId"))
        assertTrue(fields.contains("/title"))
        assertFalse(responseText.contains(injected))
        assertEquals(0, count("select count(*) from principles"))

        assertValidation(
            mockMvc
                .post("/api/v1/principles") {
                    bearer(token)
                    contentType = MediaType.APPLICATION_JSON
                    content = """{"presetId":"balanced","presetId":"aggressive","title":"duplicate"}"""
                    header("X-Request-Id", "req-validation-duplicate-key")
                }.andReturn(),
            "/",
            "INVALID_FORMAT",
        )

        assertValidation(
            mockMvc
                .post("/api/v1/principles") {
                    bearer(token)
                    contentType = MediaType.APPLICATION_JSON
                    content = ""
                    header("X-Request-Id", "req-validation-empty-body")
                }.andReturn(),
            "/",
            "INVALID_FORMAT",
        )
    }

    @Test
    fun `custom integer threshold stays a canonical JSON integer`() {
        val token = login("demo-user", userPassword())
        val response =
            create(
                token = token,
                requestId = "req-principle-integer-threshold",
                body =
                    mapOf(
                        "presetId" to "balanced",
                        "title" to "정수 임계값",
                        "rules" to
                            listOf(
                                rule(
                                    "max_single_order_amount",
                                    "ORDER_SIZE",
                                    "order_amount_krw",
                                    "<=",
                                    "300000",
                                    "BLOCK",
                                    true,
                                ),
                            ),
                    ),
            )

        assertEquals(201, response.response.status)
        assertTrue(json(response).at("/data/rules/0/threshold").isIntegralNumber)
        assertFalse(response.response.contentAsString.contains("E+"))
    }

    @Test
    fun `scientific notation threshold is accepted and returned as the same canonical decimal`() {
        val token = login("demo-user", userPassword())
        val response =
            mockMvc
                .post("/api/v1/principles") {
                    bearer(token)
                    contentType = MediaType.APPLICATION_JSON
                    header("X-Request-Id", "req-principle-exponent-threshold")
                    content =
                        """
                        {
                          "presetId":"balanced",
                          "title":"지수 표기 임계값",
                          "rules":[{
                            "ruleId":"max_position_per_asset",
                            "ruleType":"POSITION_LIMIT",
                            "metric":"asset_weight",
                            "operator":"<=",
                            "threshold":1.5e-1,
                            "severity":"BLOCK",
                            "enabled":true
                          }]
                        }
                        """.trimIndent()
                }.andExpect {
                    status { isCreated() }
                    jsonPath("$.data.rules[0].threshold") { value(0.15) }
                }.andReturn()

        assertFalse(response.response.contentAsString.contains("1.5e-1", ignoreCase = true))
        assertEquals(
            1,
            count(
                """
                select count(*) from principle_versions
                where rules_json -> 0 ->> 'threshold' = '0.15'
                """.trimIndent(),
            ),
        )
    }

    @Test
    fun `threshold validation preserves exact decimal range and normalized scale`() {
        val token = login("demo-user", userPassword())
        val overRange =
            """
            {
              "presetId":"balanced",
              "title":"정밀 범위 초과",
              "rules":[{
                "ruleId":"max_position_per_asset",
                "ruleType":"POSITION_LIMIT",
                "metric":"asset_weight",
                "operator":"<=",
                "threshold":1.0000000000000000000000000000000000000001,
                "severity":"BLOCK",
                "enabled":true
              }]
            }
            """.trimIndent()
        assertValidation(
            mockMvc
                .post("/api/v1/principles") {
                    bearer(token)
                    contentType = MediaType.APPLICATION_JSON
                    header("X-Request-Id", "req-principle-exact-range")
                    content = overRange
                }.andReturn(),
            "/rules/0/threshold",
            "OUT_OF_RANGE",
        )

        val overScale =
            """
            {
              "presetId":"balanced",
              "title":"정밀 scale 초과",
              "rules":[{
                "ruleId":"max_position_per_asset",
                "ruleType":"POSITION_LIMIT",
                "metric":"asset_weight",
                "operator":"<=",
                "threshold":0.1234000000000000000000000000000000000001,
                "severity":"BLOCK",
                "enabled":true
              }]
            }
            """.trimIndent()
        assertValidation(
            mockMvc
                .post("/api/v1/principles") {
                    bearer(token)
                    contentType = MediaType.APPLICATION_JSON
                    header("X-Request-Id", "req-principle-exact-scale")
                    content = overScale
                }.andReturn(),
            "/rules/0/threshold",
            "INVALID_SCALE",
        )
        assertEquals(0, count("select count(*) from principles"))
    }

    @Test
    fun `terminal version and oversized request return exact bounded errors`() {
        val token = login("demo-user", userPassword())
        val created = create(token, "req-terminal-create", createBody("balanced", "terminal base"))
        val body = json(created).at("/data")
        val principleId = body.path("principleId").stringValue()
        jdbcTemplate.update(
            """
            insert into principle_versions (
              principle_version_id, principle_id, version, preset_id, title, mode, status,
              rules_json, changed_fields, created_by, created_at
            )
            select ?, principle_id, ?, preset_id, title, mode, status, ?::jsonb,
                   array['title'], user_id, updated_at
            from principles where principle_id = ?
            """.trimIndent(),
            "pvr_terminal_test",
            Int.MAX_VALUE,
            objectMapper.writeValueAsString(body.path("rules")),
            principleId,
        )
        jdbcTemplate.update(
            "update principles set current_version = ? where principle_id = ?",
            Int.MAX_VALUE,
            principleId,
        )

        val exhausted =
            update(
                token = token,
                principleId = principleId,
                requestId = "req-terminal-update",
                body =
                    mapOf(
                        "expectedVersion" to Int.MAX_VALUE,
                        "title" to "must not update",
                        "mode" to "STRICT",
                        "status" to "ACTIVE",
                        "rules" to body.path("rules"),
                    ),
            )
        assertEquals(409, exhausted.response.status)
        assertEquals("VERSION_EXHAUSTED", json(exhausted).at("/error/code").stringValue())
        assertEquals(Int.MAX_VALUE, json(exhausted).at("/error/details/currentVersion").intValue())

        val oversizedTitle = "x".repeat(1_048_577)
        mockMvc
            .post("/api/v1/principles") {
                bearer(token)
                contentType = MediaType.APPLICATION_JSON
                content = """{"presetId":"balanced","title":"$oversizedTitle"}"""
                header("X-Request-Id", "req-principle-oversized")
            }.andExpect {
                status { isContentTooLarge() }
                jsonPath("$.error.code") { value("PAYLOAD_TOO_LARGE") }
                jsonPath("$.error.details.maxBytes") { value(1_048_576) }
            }
    }

    private fun create(
        token: String,
        requestId: String,
        body: Any,
    ): MvcResult =
        mockMvc
            .post("/api/v1/principles") {
                bearer(token)
                contentType = MediaType.APPLICATION_JSON
                content = objectMapper.writeValueAsString(body)
                header("X-Request-Id", requestId)
            }.andReturn()

    private fun update(
        token: String,
        principleId: String,
        requestId: String,
        body: Any,
    ): MvcResult =
        mockMvc
            .put("/api/v1/principles/$principleId") {
                bearer(token)
                contentType = MediaType.APPLICATION_JSON
                content = objectMapper.writeValueAsString(body)
                header("X-Request-Id", requestId)
            }.andReturn()

    private fun createBody(
        presetId: String,
        title: String,
    ): Map<String, Any> = mapOf("presetId" to presetId, "title" to title)

    private fun createdId(result: MvcResult): String {
        assertEquals(201, result.response.status)
        return json(result).at("/data/principleId").stringValue()
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
                    header("X-Request-Id", "req-principle-login-$username")
                }.andExpect {
                    status { isOk() }
                }.andReturn()
        return json(response).at("/data/accessToken").stringValue()
    }

    private fun assertValidation(
        result: MvcResult,
        field: String,
        reason: String,
    ) {
        assertEquals(400, result.response.status)
        val body = json(result)
        assertEquals("VALIDATION_ERROR", body.at("/error/code").stringValue())
        assertEquals(field, body.at("/error/details/violations/0/field").stringValue())
        assertEquals(reason, body.at("/error/details/violations/0/reason").stringValue())
    }

    private fun json(result: MvcResult): JsonNode = objectMapper.readTree(result.response.contentAsByteArray)

    private fun count(
        sql: String,
        vararg arguments: Any,
    ): Int = requireNotNull(jdbcTemplate.queryForObject(sql, Int::class.java, *arguments))

    private fun rule(
        ruleId: String,
        ruleType: String,
        metric: String,
        operator: String,
        threshold: String,
        severity: String,
        enabled: Boolean,
    ): Map<String, Any> =
        mapOf(
            "ruleId" to ruleId,
            "ruleType" to ruleType,
            "metric" to metric,
            "operator" to operator,
            "threshold" to threshold.toBigDecimal(),
            "severity" to severity,
            "enabled" to enabled,
        )

    companion object {
        private val PRINCIPLE_ID = Regex("^prc_[0-9a-f]{32}$")
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            PostgreSQLContainer(postgresImage)
                .withDatabaseName("decision_principle")
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
        }
    }
}
