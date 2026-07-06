package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.context.annotation.Import
import org.springframework.data.redis.core.StringRedisTemplate
import org.springframework.http.HttpStatus
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.post
import org.springframework.test.web.servlet.setup.DefaultMockMvcBuilder
import org.springframework.test.web.servlet.setup.MockMvcBuilders
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RestController
import org.springframework.web.context.WebApplicationContext
import org.testcontainers.containers.GenericContainer
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.utility.DockerImageName
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.util.UUID
import java.util.concurrent.TimeUnit

@Testcontainers
@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.jdbc.autoconfigure.DataSourceAutoConfiguration,org.springframework.boot.hibernate.autoconfigure.HibernateJpaAutoConfiguration,org.springframework.boot.data.jpa.autoconfigure.DataJpaRepositoriesAutoConfiguration,org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
@Import(TestOnlyIdempotencyController::class)
class IdempotencyIntegrationTest(
    @Autowired private val webApplicationContext: WebApplicationContext,
    @Autowired private val objectMapper: ObjectMapper,
    @Autowired private val redisTemplate: StringRedisTemplate,
) : SpringApiIntegrationTestBase() {
    private lateinit var mockMvc: MockMvc

    @BeforeEach
    fun setUpMockMvc() {
        mockMvc =
            MockMvcBuilders
                .webAppContextSetup(webApplicationContext)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    @Test
    fun `same idempotency key and payload replays stored response`() {
        val token = login()
        val first =
            postIdempotent(
                token = token,
                key = "idem-replay",
                body = """{"symbol":"005930","quantity":1}""",
            )
        val second =
            postIdempotent(
                token = token,
                key = "idem-replay",
                body = """{"symbol":"005930","quantity":1}""",
            )

        assertEquals(201, first.status)
        assertEquals(first.body, second.body)
        assertEquals(first.bodyNode.at("/data/echo/symbol").stringValue(), "005930")
    }

    @Test
    fun `same idempotency key and different payload returns conflict envelope`() {
        val token = login()
        postIdempotent(
            token = token,
            key = "idem-conflict",
            body = """{"symbol":"005930","quantity":1}""",
        )

        mockMvc
            .post("/api/v1/orders/test-idempotency") {
                bearer(token)
                header("X-Idempotency-Key", "idem-conflict")
                header("X-Request-Id", "req-idem-conflict")
                contentType = MediaType.APPLICATION_JSON
                content = """{"symbol":"005930","quantity":2}"""
            }.andExpect {
                status { isConflict() }
                jsonPath("$.success") { value(false) }
                jsonPath("$.requestId") { value("req-idem-conflict") }
                jsonPath("$.error.code") { value("IDEMPOTENCY_CONFLICT") }
            }
    }

    @Test
    fun `idempotency key is stored with a twenty four hour ttl`() {
        val token = login()
        postIdempotent(
            token = token,
            key = "idem-ttl",
            body = """{"symbol":"005930","quantity":1}""",
        )

        val keys = redisTemplate.keys("idempotency:demo-user:idem-ttl")
        assertEquals(1, keys.size)
        val ttlHours = redisTemplate.getExpire(keys.single(), TimeUnit.HOURS)
        assertTrue(ttlHours in 23..24, "expected Redis TTL close to 24h but was $ttlHours")
    }

    @Test
    fun `unauthenticated write request returns unauthorized before idempotency storage`() {
        mockMvc
            .post("/api/v1/orders/test-idempotency") {
                header("X-Idempotency-Key", "idem-unauth")
                header("X-Request-Id", "req-idem-unauth")
                contentType = MediaType.APPLICATION_JSON
                content = """{"symbol":"005930","quantity":1}"""
            }.andExpect {
                status { isUnauthorized() }
                jsonPath("$.error.code") { value("UNAUTHORIZED") }
            }

        assertTrue(redisTemplate.keys("*idem-unauth*").isEmpty())
    }

    private fun postIdempotent(
        token: String,
        key: String,
        body: String,
    ): IdempotentHttpResponse {
        val response =
            mockMvc
                .post("/api/v1/orders/test-idempotency") {
                    bearer(token)
                    header("X-Idempotency-Key", key)
                    header("X-Request-Id", "req-$key")
                    contentType = MediaType.APPLICATION_JSON
                    content = body
                }.andReturn()
                .response

        assertFalse(response.contentAsString.isBlank())
        return IdempotentHttpResponse(
            status = response.status,
            body = response.contentAsString,
            bodyNode = objectMapper.readTree(response.contentAsString),
        )
    }

    private fun login(): String {
        val response =
            mockMvc
                .post("/api/v1/auth/login") {
                    contentType = MediaType.APPLICATION_JSON
                    content = """{"username":"demo-user","password":"${userPassword()}"}"""
                    header("X-Request-Id", "req-idem-login")
                }.andExpect {
                    status { isOk() }
                }.andReturn()
                .response
                .contentAsString

        return objectMapper.readTree(response).at("/data/accessToken").stringValue()
    }

    companion object {
        @Container
        @JvmStatic
        val redis: GenericContainer<*> =
            GenericContainer(DockerImageName.parse("redis:7.2-alpine"))
                .withExposedPorts(6379)

        @DynamicPropertySource
        @JvmStatic
        fun redisProperties(registry: DynamicPropertyRegistry) {
            registry.add("spring.data.redis.host", redis::getHost)
            registry.add("spring.data.redis.port") { redis.getMappedPort(6379) }
        }
    }
}

private data class IdempotentHttpResponse(
    val status: Int,
    val body: String,
    val bodyNode: JsonNode,
)

@RestController
private class TestOnlyIdempotencyController {
    @PostMapping("/api/v1/orders/test-idempotency")
    fun create(
        @RequestBody body: Map<String, Any>,
    ): ResponseEntity<Map<String, Any>> =
        ResponseEntity
            .status(HttpStatus.CREATED)
            .body(
                mapOf(
                    "echo" to body,
                    "nonce" to UUID.randomUUID().toString(),
                ),
            )
}
