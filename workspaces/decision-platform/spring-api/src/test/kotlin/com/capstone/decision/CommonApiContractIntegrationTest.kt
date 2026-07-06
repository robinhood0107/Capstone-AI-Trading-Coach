package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.context.annotation.Import
import org.springframework.http.MediaType
import org.springframework.security.access.prepost.PreAuthorize
import org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.get
import org.springframework.test.web.servlet.options
import org.springframework.test.web.servlet.post
import org.springframework.test.web.servlet.setup.DefaultMockMvcBuilder
import org.springframework.test.web.servlet.setup.MockMvcBuilders
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController
import org.springframework.web.context.WebApplicationContext
import tools.jackson.databind.ObjectMapper

// DB/Kafka 없이도 S0.3 REST 공통 계약과 security filter 동작을 빠르게 검증한다.
@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.jdbc.autoconfigure.DataSourceAutoConfiguration,org.springframework.boot.hibernate.autoconfigure.HibernateJpaAutoConfiguration,org.springframework.boot.data.jpa.autoconfigure.DataJpaRepositoriesAutoConfiguration,org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
@Import(TestOnlyAdminController::class)
class CommonApiContractIntegrationTest(
    @Autowired private val webApplicationContext: WebApplicationContext,
    @Autowired private val objectMapper: ObjectMapper,
) : SpringApiIntegrationTestBase() {
    private lateinit var mockMvc: MockMvc

    @BeforeEach
    fun setUpMockMvc() {
        // Security filter chain까지 태워야 401/403 envelope와 requestId header를 실제처럼 검증한다.
        mockMvc =
            MockMvcBuilders
                .webAppContextSetup(webApplicationContext)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    // DTO 검증 실패가 Spring 기본 오류가 아니라 VALIDATION_ERROR envelope로 내려가는지 잠근다.
    @Test
    fun `invalid login body returns validation envelope`() {
        mockMvc
            .post("/api/v1/auth/login") {
                contentType = MediaType.APPLICATION_JSON
                content = """{"username":"demo-user"}"""
                header("X-Request-Id", "req-invalid-login")
            }.andExpect {
                status { isBadRequest() }
                header { string("X-Request-Id", "req-invalid-login") }
                jsonPath("$.success") { value(false) }
                jsonPath("$.requestId") { value("req-invalid-login") }
                jsonPath("$.error.code") { value("VALIDATION_ERROR") }
            }
    }

    // system health는 smoke용 API지만 인증 경계를 우회하면 안 된다.
    @Test
    fun `system health requires bearer token`() {
        mockMvc
            .get("/api/v1/system/health") {
                header("X-Request-Id", "req-health-unauthorized")
            }.andExpect {
                status { isUnauthorized() }
                header { string("X-Request-Id", "req-health-unauthorized") }
                jsonPath("$.success") { value(false) }
                jsonPath("$.error.code") { value("UNAUTHORIZED") }
            }
    }

    // 로그인 토큰, requestId 보존, health envelope를 한 번에 확인하는 대표 happy path다.
    @Test
    fun `user token can call system health with preserved request id`() {
        val token = login("demo-user", userPassword())

        mockMvc
            .get("/api/v1/system/health") {
                bearer(token)
                header("X-Request-Id", "req-health-user")
            }.andExpect {
                status { isOk() }
                header { string("X-Request-Id", "req-health-user") }
                jsonPath("$.success") { value(true) }
                jsonPath("$.requestId") { value("req-health-user") }
                jsonPath("$.data.pythonService") { value("UP") }
                jsonPath("$.data.killSwitchActive") { value(false) }
            }
    }

    // ADMIN 전용 endpoint가 USER 토큰으로 막혀야 이후 운영 API 권한 경계가 흔들리지 않는다.
    @Test
    fun `user token is forbidden from admin endpoint`() {
        val token = login("demo-user", userPassword())

        mockMvc
            .get("/api/v1/test/admin") {
                bearer(token)
                header("X-Request-Id", "req-admin-user")
            }.andExpect {
                status { isForbidden() }
                jsonPath("$.success") { value(false) }
                jsonPath("$.requestId") { value("req-admin-user") }
                jsonPath("$.error.code") { value("FORBIDDEN") }
            }
    }

    // 존재하지 않는 API도 프론트가 분기 가능한 NOT_FOUND envelope를 받아야 한다.
    @Test
    fun `unknown api path returns not found envelope`() {
        val token = login("demo-user", userPassword())

        mockMvc
            .get("/api/v1/unknown") {
                bearer(token)
                header("X-Request-Id", "req-api-not-found")
            }.andExpect {
                status { isNotFound() }
                jsonPath("$.success") { value(false) }
                jsonPath("$.requestId") { value("req-api-not-found") }
                jsonPath("$.error.code") { value("NOT_FOUND") }
            }
    }

    // dashboard 개발 서버가 Authorization/idempotency header를 보낼 수 있어야 수동 smoke가 가능하다.
    @Test
    fun `cors preflight allows dashboard origin and request headers`() {
        mockMvc
            .options("/api/v1/system/health") {
                header("Origin", "http://localhost:3000")
                header("Access-Control-Request-Method", "GET")
                header("Access-Control-Request-Headers", "Authorization,Content-Type,X-Request-Id,X-Idempotency-Key")
            }.andExpect {
                status { isOk() }
                header { string("Access-Control-Allow-Origin", "http://localhost:3000") }
                header { string("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Request-Id, X-Idempotency-Key") }
            }

        val token = login("demo-user", userPassword())
        mockMvc
            .get("/api/v1/system/health") {
                bearer(token)
                header("Origin", "http://localhost:3000")
                header("X-Request-Id", "req-cors-health")
            }.andExpect {
                status { isOk() }
                header { string("Access-Control-Allow-Origin", "http://localhost:3000") }
                header { string("Access-Control-Expose-Headers", "X-Request-Id") }
            }
    }

    private fun login(
        username: String,
        password: String,
    ): String {
        // 테스트마다 토큰 발급 흐름을 실제 login API로 통과시켜 JWT 생성 계약도 함께 검증한다.
        val response =
            mockMvc
                .post("/api/v1/auth/login") {
                    contentType = MediaType.APPLICATION_JSON
                    content = """{"username":"$username","password":"$password"}"""
                    header("X-Request-Id", "req-login-$username")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.success") { value(true) }
                }.andReturn()
                .response
                .contentAsString

        val token = objectMapper.readTree(response).at("/data/accessToken").stringValue()
        assertEquals("Bearer", objectMapper.readTree(response).at("/data/tokenType").stringValue())
        return token
    }
}

@RestController
private class TestOnlyAdminController {
    // production API를 오염시키지 않고 403 권한 경계만 검증하기 위한 test-only endpoint다.
    @PreAuthorize("hasRole('ADMIN')")
    @GetMapping("/api/v1/test/admin")
    fun adminOnly(): Map<String, String> = mapOf("status" to "ADMIN")
}
