package com.capstone.decision

import com.capstone.decision.infrastructure.security.LoginAttemptStore
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
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
import tools.jackson.databind.ObjectMapper
import java.time.Instant

// 관리자 콘솔은 ADMIN만 쓰고, 상한은 새 요청 하나만 거부하며 기존 사용자는 멈추지 않는다.
@Testcontainers
@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
class AdminConsoleIntegrationTest(
    @Autowired private val webApplicationContext: WebApplicationContext,
    @Autowired private val objectMapper: ObjectMapper,
    @Autowired private val jdbcTemplate: JdbcTemplate,
    @Autowired private val loginAttemptStore: LoginAttemptStore,
) : SpringApiIntegrationTestBase() {
    private lateinit var mockMvc: MockMvc

    @BeforeEach
    fun setUp() {
        loginAttemptStore.clear()
        TestPeerUser.ensure(jdbcTemplate)
        jdbcTemplate.update("update users set status = 'ACTIVE', role = 'USER' where user_id = ?", TestPeerUser.USER_ID)
        jdbcTemplate.update("update service_limits set signup_cap = null, automation_active_cap = 20 where limits_id = 1")
        mockMvc =
            MockMvcBuilders
                .webAppContextSetup(webApplicationContext)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    @AfterEach
    fun restoreLimits() {
        jdbcTemplate.update("update service_limits set signup_cap = null, automation_active_cap = 20 where limits_id = 1")
    }

    @Test
    fun `only an ADMIN reaches the console and demo-user is that ADMIN`() {
        val admin = login("demo-user", TEST_USER_PASSWORD, "ADMIN")
        val peer = login(TestPeerUser.EMAIL, TestPeerUser.PASSWORD, "USER")

        mockMvc.get("/api/v1/admin/limits") { header("Authorization", "Bearer $peer") }.andExpect { status { isForbidden() } }
        mockMvc.get("/api/v1/admin/users") { header("Authorization", "Bearer $peer") }.andExpect { status { isForbidden() } }
        mockMvc.get("/api/v1/admin/limits").andExpect { status { isUnauthorized() } }

        mockMvc.get("/api/v1/admin/limits") { header("Authorization", "Bearer $admin") }.andExpect {
            status { isOk() }
            jsonPath("$.data.automationActiveCap") { value(20) }
            jsonPath("$.data.signupCap") { doesNotExist() }
        }
        mockMvc.get("/api/v1/admin/users?search=isolation-peer") { header("Authorization", "Bearer $admin") }.andExpect {
            status { isOk() }
            jsonPath("$.data.items[0].userId") { value(TestPeerUser.USER_ID) }
            jsonPath("$.data.items[0].role") { value("USER") }
        }
        assertEquals(0L, jdbcTemplate.queryForObject("select count(*) from users where user_id = 'usr_demo_admin'", Long::class.java))
    }

    @Test
    fun `signup cap rejects only the new account and existing users keep signing in`() {
        val admin = login("demo-user", TEST_USER_PASSWORD, "ADMIN")
        val active = jdbcTemplate.queryForObject("select count(*) from users where status <> 'DISABLED'", Long::class.java)!!
        mockMvc
            .put("/api/v1/admin/limits") {
                header("Authorization", "Bearer $admin")
                contentType = MediaType.APPLICATION_JSON
                content = objectMapper.writeValueAsString(mapOf("signupCap" to active, "automationActiveCap" to 20))
            }.andExpect {
                status { isOk() }
                jsonPath("$.data.signupCap") { value(active.toInt()) }
            }

        mockMvc
            .post("/api/v1/auth/signup") {
                contentType = MediaType.APPLICATION_JSON
                content =
                    objectMapper.writeValueAsString(
                        mapOf("email" to "capped-${Instant.now().toEpochMilli()}@example.test", "password" to "capped-signup-password-01"),
                    )
            }.andExpect { status { isConflict() } }

        login(TestPeerUser.EMAIL, TestPeerUser.PASSWORD, "USER")
        login("demo-user", TEST_USER_PASSWORD, "ADMIN")
    }

    @Test
    fun `an admin cannot demote or disable themselves and a disabled user loses access`() {
        val admin = login("demo-user", TEST_USER_PASSWORD, "ADMIN")
        mockMvc
            .put("/api/v1/admin/users/usr_demo_user/access") {
                header("Authorization", "Bearer $admin")
                contentType = MediaType.APPLICATION_JSON
                content = """{"role":"USER","status":"ACTIVE"}"""
            }.andExpect { status { isConflict() } }

        val peerToken = login(TestPeerUser.EMAIL, TestPeerUser.PASSWORD, "USER")
        mockMvc
            .put("/api/v1/admin/users/${TestPeerUser.USER_ID}/access") {
                header("Authorization", "Bearer $admin")
                contentType = MediaType.APPLICATION_JSON
                content = """{"role":"USER","status":"DISABLED"}"""
            }.andExpect {
                status { isOk() }
                jsonPath("$.data.status") { value("DISABLED") }
            }
        mockMvc.get("/api/v1/principles") { header("Authorization", "Bearer $peerToken") }.andExpect { status { isUnauthorized() } }
        loginAttemptStore.clear()
        mockMvc
            .post("/api/v1/auth/login") {
                contentType = MediaType.APPLICATION_JSON
                content = objectMapper.writeValueAsString(mapOf("username" to TestPeerUser.EMAIL, "password" to TestPeerUser.PASSWORD))
            }.andExpect { status { isUnauthorized() } }

        mockMvc
            .put("/api/v1/admin/users/${TestPeerUser.USER_ID}/access") {
                header("Authorization", "Bearer $admin")
                contentType = MediaType.APPLICATION_JSON
                content = """{"role":"USER","status":"ACTIVE"}"""
            }.andExpect { status { isOk() } }
        loginAttemptStore.clear()
        login(TestPeerUser.EMAIL, TestPeerUser.PASSWORD, "USER")
    }

    @Test
    fun `automation cap rejects only the next arm and keeps the armed owner running`() {
        val admin = login("demo-user", TEST_USER_PASSWORD, "ADMIN")
        jdbcTemplate.update("update service_limits set automation_active_cap = 1 where limits_id = 1")
        try {
            armDirectly("usr_demo_user")
            val rejected = runCatching { armDirectly(TestPeerUser.USER_ID) }.exceptionOrNull()
            assertEquals("53400", generateSequence(rejected) { it.cause }.filterIsInstance<java.sql.SQLException>().firstOrNull()?.sqlState)
            mockMvc.get("/api/v1/admin/automation") { header("Authorization", "Bearer $admin") }.andExpect {
                status { isOk() }
                jsonPath("$.data[0].userId") { value("usr_demo_user") }
                jsonPath("$.data.length()") { value(1) }
            }
        } finally {
            jdbcTemplate.update("delete from automation_control where user_id in ('usr_demo_user', ?)", TestPeerUser.USER_ID)
        }
    }

    // 무장 경로 전체(KIS 인증 등)가 아니라 상한 트리거 자체를 검증한다.
    private fun armDirectly(userId: String) {
        jdbcTemplate.update(
            """
            insert into automation_control(
              user_id, control_state, version, brokerage_mode, account_id, principle_id, strategy_id,
              baseline_account_digest, certification_status, kill_switch_active
            ) values (?, 'ARMED', 1, 'INTERNAL_PAPER', 'acct_capacity_test', 'prc_capacity_test', 'strategy_capacity_test',
              repeat('a', 64), 'NOT_REQUIRED_INTERNAL_PAPER', false)
            """.trimIndent(),
            userId,
        )
    }

    private fun login(
        username: String,
        password: String,
        expectedRole: String,
    ): String {
        val response =
            mockMvc
                .post("/api/v1/auth/login") {
                    contentType = MediaType.APPLICATION_JSON
                    content = objectMapper.writeValueAsString(mapOf("username" to username, "password" to password))
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.data.user.role") { value(expectedRole) }
                }.andReturn()
                .response
                .contentAsString
        return objectMapper.readTree(response).at("/data/accessToken").stringValue()
    }

    companion object {
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            stablePostgresContainer(postgresImage)
                .withDatabaseName("decision_admin")
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
