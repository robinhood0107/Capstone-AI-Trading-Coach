package com.capstone.decision

import com.capstone.decision.infrastructure.security.AuthCutoverException
import com.capstone.decision.infrastructure.security.AuthCutoverSmoke
import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.junit.jupiter.api.io.TempDir
import java.net.InetSocketAddress
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.ZoneOffset
import java.util.Base64
import java.util.concurrent.atomic.AtomicBoolean

// cutover evidence는 raw token 없이 동일 token의 pre-200/post-401 사실만 연결해야 한다.
class AuthCutoverSmokeTest {
    @TempDir
    lateinit var tempDirectory: Path

    private lateinit var server: HttpServer
    private val cutoverComplete = AtomicBoolean(false)
    private val now: Instant = Instant.parse("2026-07-22T10:00:00Z")
    private val clock: Clock = Clock.fixed(now, ZoneOffset.UTC)
    private val oldToken: String = jwtShapedToken(now.plus(Duration.ofHours(4)))

    @BeforeEach
    fun startServer() {
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { exchange -> handle(exchange) }
        server.start()
    }

    @AfterEach
    fun stopServer() {
        server.stop(0)
    }

    @Test
    fun `capture writes atomic digest evidence and post smoke deletes it only after all checks pass`() {
        val evidencePath = tempDirectory.resolve("pre-cutover.json")
        AuthCutoverSmoke.capture(
            environment = captureEnvironment(oldToken),
            evidencePath = evidencePath,
            clock = clock,
        )

        assertTrue(Files.isRegularFile(evidencePath))
        val evidence = Files.readString(evidencePath)
        assertFalse(evidence.contains(oldToken))
        assertTrue(evidence.contains("\"schemaVersion\":1"))
        assertTrue(evidence.contains("\"preflightStatus\":200"))

        cutoverComplete.set(true)
        AuthCutoverSmoke.verifyAfterCutover(
            environment = postEnvironment(oldToken),
            evidencePath = evidencePath,
            clock = Clock.offset(clock, Duration.ofMinutes(5)),
        )

        assertFalse(Files.exists(evidencePath))
    }

    @Test
    fun `same token mismatch and stale evidence fail before post checks and preserve evidence`() {
        val evidencePath = tempDirectory.resolve("pre-cutover.json")
        AuthCutoverSmoke.capture(captureEnvironment(oldToken), evidencePath, clock)

        val differentToken = jwtShapedToken(now.plus(Duration.ofHours(5)))
        assertThrows<AuthCutoverException> {
            AuthCutoverSmoke.verifyAfterCutover(
                postEnvironment(differentToken),
                evidencePath,
                Clock.offset(clock, Duration.ofMinutes(5)),
            )
        }
        assertTrue(Files.exists(evidencePath))

        assertThrows<AuthCutoverException> {
            AuthCutoverSmoke.verifyAfterCutover(
                postEnvironment(oldToken),
                evidencePath,
                Clock.offset(clock, Duration.ofMinutes(31)),
            )
        }
        assertTrue(Files.exists(evidencePath))
    }

    private fun captureEnvironment(token: String): Map<String, String> =
        mapOf(
            "AUTH_SMOKE_BASE_URL" to baseUrl(),
            "AUTH_SMOKE_PRE_CUTOVER_TOKEN" to token,
        )

    private fun postEnvironment(token: String): Map<String, String> =
        mapOf(
            "AUTH_SMOKE_BASE_URL" to baseUrl(),
            "AUTH_SMOKE_PRE_CUTOVER_TOKEN" to token,
            "AUTH_SMOKE_USER_PASSWORD" to "runtime-user-password",
            "AUTH_SMOKE_ADMIN_PASSWORD" to "runtime-admin-password",
        )

    private fun baseUrl(): String = "http://127.0.0.1:${server.address.port}"

    private fun handle(exchange: HttpExchange) {
        val requestBody = exchange.requestBody.use { String(it.readAllBytes(), StandardCharsets.UTF_8) }
        val path = exchange.requestURI.path
        val authorization = exchange.requestHeaders.getFirst("Authorization") ?: ""
        when {
            path == "/api/v1/system/health" && authorization == "Bearer $oldToken" -> {
                respond(exchange, if (cutoverComplete.get()) 401 else 200, "{}")
            }

            path == "/api/v1/system/health" && authorization in setOf("Bearer new-user-token", "Bearer new-admin-token") -> {
                respond(exchange, 200, "{}")
            }

            path == "/api/v1/auth/login" -> {
                val isAdmin = requestBody.contains("demo-admin")
                val userId = if (isAdmin) "usr_demo_admin" else "usr_demo_user"
                val role = if (isAdmin) "ADMIN" else "USER"
                val token = if (isAdmin) "new-admin-token" else "new-user-token"
                respond(
                    exchange,
                    200,
                    """{"success":true,"data":{"accessToken":"$token","user":{"userId":"$userId","role":"$role"}}}""",
                )
            }

            else -> respond(exchange, 404, "{}")
        }
    }

    private fun respond(
        exchange: HttpExchange,
        status: Int,
        body: String,
    ) {
        val bytes = body.toByteArray(StandardCharsets.UTF_8)
        exchange.responseHeaders.add("Content-Type", "application/json")
        exchange.sendResponseHeaders(status, bytes.size.toLong())
        exchange.responseBody.use { it.write(bytes) }
    }

    private fun jwtShapedToken(expiresAt: Instant): String {
        val encoder = Base64.getUrlEncoder().withoutPadding()
        val header = encoder.encodeToString("""{"alg":"HS256"}""".toByteArray(StandardCharsets.UTF_8))
        val payload = encoder.encodeToString("""{"exp":${expiresAt.epochSecond}}""".toByteArray(StandardCharsets.UTF_8))
        return "$header.$payload.test-signature"
    }
}
