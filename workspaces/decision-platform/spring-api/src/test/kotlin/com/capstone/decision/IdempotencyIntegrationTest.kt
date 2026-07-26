package com.capstone.decision

import com.capstone.decision.infrastructure.idempotency.IdempotencyClaimLostException
import com.capstone.decision.infrastructure.idempotency.IdempotencyLookup
import com.capstone.decision.infrastructure.idempotency.IdempotencyProperties
import com.capstone.decision.infrastructure.idempotency.IdempotencyService
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

// Redis를 실제로 띄워 idempotency TTL/replay가 mock이 아니라 저장소 기준으로 동작하는지 본다.
@Testcontainers
@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.jdbc.autoconfigure.DataSourceAutoConfiguration,org.springframework.boot.hibernate.autoconfigure.HibernateJpaAutoConfiguration,org.springframework.boot.data.jpa.autoconfigure.DataJpaRepositoriesAutoConfiguration,org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
        "app.idempotency.max-request-body-bytes=256",
        "app.idempotency.max-response-body-bytes=256",
        "app.idempotency.max-key-length=64",
    ],
)
@Import(TestOnlyIdempotencyController::class, TestAuthRepositoryConfiguration::class)
class IdempotencyIntegrationTest(
    @Autowired private val webApplicationContext: WebApplicationContext,
    @Autowired private val objectMapper: ObjectMapper,
    @Autowired private val redisTemplate: StringRedisTemplate,
    @Autowired private val idempotencyService: IdempotencyService,
    @Autowired private val idempotencyProperties: IdempotencyProperties,
) : SpringApiIntegrationTestBase() {
    private lateinit var mockMvc: MockMvc

    @BeforeEach
    fun setUpMockMvc() {
        // idempotency는 JWT 인증 뒤에만 적용되므로 Security filter chain을 테스트에 포함한다.
        mockMvc =
            MockMvcBuilders
                .webAppContextSetup(webApplicationContext)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    // 같은 요청 재시도는 controller를 다시 실행하지 않고 최초 응답을 그대로 돌려야 한다.
    @Test
    fun `same idempotency key and payload replays stored response`() {
        val token = login()
        val first =
            postIdempotent(
                token = token,
                key = "idem-replay-00001",
                body = """{"symbol":"005930","quantity":1}""",
            )
        val second =
            postIdempotent(
                token = token,
                key = "idem-replay-00001",
                body = """{"symbol":"005930","quantity":1}""",
            )

        assertEquals(201, first.status)
        assertEquals(first.body, second.body)
        assertEquals(first.bodyNode.at("/data/echo/symbol").stringValue(), "005930")
    }

    // 같은 key로 다른 payload가 오면 중복 방지가 아니라 충돌로 처리해야 안전하다.
    @Test
    fun `same idempotency key and different payload returns conflict envelope`() {
        val token = login()
        postIdempotent(
            token = token,
            key = "idem-conflict-0001",
            body = """{"symbol":"005930","quantity":1}""",
        )

        mockMvc
            .post("/api/v1/orders/test-idempotency") {
                bearer(token)
                header("X-Idempotency-Key", "idem-conflict-0001")
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

    // 24시간 보존 계약이 Redis TTL로 실제 설정되는지 확인한다.
    @Test
    fun `idempotency key is stored with a twenty four hour ttl`() {
        val token = login()
        postIdempotent(
            token = token,
            key = "idem-ttl-00000001",
            body = """{"symbol":"005930","quantity":1}""",
        )

        val keys = redisTemplate.keys("idempotency:usr_demo_user:idem-ttl-00000001")
        assertEquals(1, keys.size)
        val ttlHours = redisTemplate.getExpire(keys.single(), TimeUnit.HOURS)
        assertTrue(ttlHours in 23..24, "expected Redis TTL close to 24h but was $ttlHours")
    }

    // 인증 실패 요청은 idempotency 저장소에 흔적을 남기면 안 된다.
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

    @Test
    fun `unknown wildcard write path does not allocate idempotency state`() {
        val token = login()

        mockMvc
            .post("/api/v1/orders/not-a-handler") {
                bearer(token)
                header("X-Idempotency-Key", "idem-missing-handler")
                header("X-Request-Id", "req-idem-missing-handler")
                contentType = MediaType.APPLICATION_JSON
                content = "{}"
            }.andExpect {
                status { isNotFound() }
                jsonPath("$.error.code") { value("NOT_FOUND") }
            }

        assertTrue(redisTemplate.keys("*idem-missing-handler*").isEmpty())
    }

    @Test
    fun `finance idempotency allowlist excludes Principle and includes Kill Switch mutation`() {
        assertEquals(
            listOf(
                "/api/v1/orders/**",
                "/api/v1/backtests/**",
                "/api/v1/risk/kill-switch",
            ),
            idempotencyProperties.paths,
        )
        assertFalse(idempotencyProperties.paths.any { it.contains("principle", ignoreCase = true) })
    }

    @Test
    fun `Principle request never allocates finance idempotency state`() {
        val token = login()

        mockMvc
            .post("/api/v1/principles") {
                bearer(token)
                header("X-Idempotency-Key", "principle-must-not-be-finance-idempotent")
                header("X-Request-Id", "req-principle-idempotency-boundary")
                contentType = MediaType.APPLICATION_JSON
                content = "{}"
            }.andExpect {
                status { isBadRequest() }
                jsonPath("$.error.code") { value("VALIDATION_ERROR") }
            }

        assertTrue(redisTemplate.keys("*principle-must-not-be-finance-idempotent*").isEmpty())
    }

    @Test
    fun `atomic claim allows only the first request to execute`() {
        val first =
            idempotencyService.acquire(
                userId = "demo-user",
                idempotencyKey = "idem-atomic-claim",
                requestHash = "hash-one",
            )
        val second =
            idempotencyService.acquire(
                userId = "demo-user",
                idempotencyKey = "idem-atomic-claim",
                requestHash = "hash-one",
            )

        assertTrue(first is IdempotencyLookup.New)
        assertTrue(second is IdempotencyLookup.InProgress)
    }

    @Test
    fun `per-user admission cap rejects excess new idempotency keys`() {
        val boundedService =
            IdempotencyService(
                redisTemplate,
                IdempotencyProperties(maxNewKeysPerUserPerTtl = 2),
            )
        val userId = "quota-${UUID.randomUUID()}"

        repeat(2) { index ->
            assertTrue(
                boundedService.acquire(userId, "key-$index", "hash-$index") is IdempotencyLookup.New,
            )
        }

        assertTrue(
            boundedService.acquire(userId, "key-overflow", "hash-overflow") is
                IdempotencyLookup.CapacityExceeded,
        )
    }

    @Test
    fun `expired claim owner cannot overwrite a replacement claim`() {
        val first =
            idempotencyService.acquire(
                userId = "demo-user",
                idempotencyKey = "idem-claim-owner",
                requestHash = "hash-owner",
            ) as IdempotencyLookup.New
        redisTemplate.delete("idempotency-claim:demo-user:idem-claim-owner")
        val replacement =
            idempotencyService.acquire(
                userId = "demo-user",
                idempotencyKey = "idem-claim-owner",
                requestHash = "hash-owner",
            ) as IdempotencyLookup.New

        org.junit.jupiter.api.assertThrows<IdempotencyClaimLostException> {
            idempotencyService.store(
                userId = "demo-user",
                idempotencyKey = "idem-claim-owner",
                requestHash = "hash-owner",
                claimToken = first.claimToken,
                status = 200,
                body = """{"owner":"stale"}""",
                contentType = MediaType.APPLICATION_JSON_VALUE,
            )
        }
        idempotencyService.store(
            userId = "demo-user",
            idempotencyKey = "idem-claim-owner",
            requestHash = "hash-owner",
            claimToken = replacement.claimToken,
            status = 200,
            body = """{"owner":"replacement"}""",
            contentType = MediaType.APPLICATION_JSON_VALUE,
        )

        val replay =
            idempotencyService.acquire(
                userId = "demo-user",
                idempotencyKey = "idem-claim-owner",
                requestHash = "hash-owner",
            ) as IdempotencyLookup.Replay
        assertEquals("""{"owner":"replacement"}""", replay.body)
        assertTrue(redisTemplate.getExpire(idempotencyService.redisKey("demo-user", "idem-claim-owner")) > 0)
        assertFalse(redisTemplate.hasKey("idempotency-claim:demo-user:idem-claim-owner"))
    }

    @Test
    fun `idempotency key rejects unsafe or oversized values before controller`() {
        val token = login()

        mockMvc
            .post("/api/v1/orders/test-idempotency") {
                bearer(token)
                header("X-Idempotency-Key", "unsafe key with spaces")
                header("X-Request-Id", "req-idem-key-invalid")
                contentType = MediaType.APPLICATION_JSON
                content = """{"symbol":"005930","quantity":1}"""
            }.andExpect {
                status { isBadRequest() }
                jsonPath("$.error.code") { value("VALIDATION_ERROR") }
            }
    }

    @Test
    fun `idempotency request body is bounded before controller execution`() {
        val token = login()

        mockMvc
            .post("/api/v1/orders/test-idempotency") {
                bearer(token)
                header("X-Idempotency-Key", "idem-body-limit-01")
                header("X-Request-Id", "req-idem-body-limit")
                contentType = MediaType.APPLICATION_JSON
                content = "x".repeat(257)
            }.andExpect {
                status { isPayloadTooLarge() }
                jsonPath("$.error.code") { value("PAYLOAD_TOO_LARGE") }
            }

        assertTrue(redisTemplate.keys("*idem-body-limit-01*").isEmpty())
    }

    @Test
    fun `oversized controller response is replaced by bounded replay-safe error`() {
        val token = login()

        mockMvc
            .post("/api/v1/orders/test-large-response") {
                bearer(token)
                header("X-Idempotency-Key", "idem-response-limit")
                header("X-Request-Id", "req-idem-response-limit")
                contentType = MediaType.APPLICATION_JSON
                content = "{}"
            }.andExpect {
                status { isConflict() }
                jsonPath("$.error.code") { value("CONFLICT") }
            }

        val storedBody =
            redisTemplate.opsForHash<String, String>().get(
                "idempotency:usr_demo_user:idem-response-limit",
                "body",
            )
        assertTrue(!storedBody.isNullOrBlank() && storedBody.toByteArray().size <= 256)
    }

    @Test
    fun `redis requires authentication and disables eviction`() {
        val unauthenticated = redis.execInContainer("redis-cli", "ping")
        val authenticated =
            redis.execInContainer(
                "sh",
                "-ec",
                "REDISCLI_AUTH=\"\$REDIS_PASSWORD\" redis-cli ping && " +
                    "REDISCLI_AUTH=\"\$REDIS_PASSWORD\" redis-cli CONFIG GET maxmemory-policy",
            )

        assertTrue(unauthenticated.stdout.contains("NOAUTH"))
        assertTrue(authenticated.stdout.contains("PONG"))
        assertTrue(authenticated.stdout.contains("noeviction"))
    }

    private fun postIdempotent(
        token: String,
        key: String,
        body: String,
    ): IdempotentHttpResponse {
        // replay 비교를 위해 status/body/json node를 함께 보존한다.
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
        // 테스트 idempotency 요청도 실제 demo JWT를 사용해 userId 기반 Redis key를 검증한다.
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
        private val redisPasswordValue: String = "r" + "p".repeat(24)

        // CI와 로컬에서 동일한 Redis 버전으로 TTL/Hash 동작 차이를 줄인다.
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
        fun redisProperties(registry: DynamicPropertyRegistry) {
            registry.add("spring.data.redis.host", redis::getHost)
            registry.add("spring.data.redis.port") { redis.getMappedPort(6379) }
            registry.add("spring.data.redis.password") { redisPasswordValue }
        }
    }
}

// idempotency replay는 body byte 동등성까지 봐야 해서 응답 원문을 함께 담는다.
private data class IdempotentHttpResponse(
    val status: Int,
    val body: String,
    val bodyNode: JsonNode,
)

@RestController
private class TestOnlyIdempotencyController {
    // nonce가 바뀌는 endpoint여야 replay가 실제 재실행인지 저장 응답인지 구분된다.
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

    @PostMapping("/api/v1/orders/test-large-response")
    fun createLargeResponse(): ResponseEntity<Map<String, String>> =
        ResponseEntity
            .status(HttpStatus.CREATED)
            .contentType(MediaType.APPLICATION_JSON)
            .body(mapOf("payload" to "x".repeat(1_048_576)))
}
