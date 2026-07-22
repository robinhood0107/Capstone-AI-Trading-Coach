package com.capstone.decision.infrastructure.security

import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import tools.jackson.module.kotlin.jacksonObjectMapper
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.charset.StandardCharsets
import java.nio.file.FileAlreadyExistsException
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermissions
import java.security.MessageDigest
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.util.Base64
import java.util.HexFormat
import java.util.Locale
import kotlin.system.exitProcess

// 배포 전 old token 200과 배포 후 같은 token 401을 raw token 저장 없이 연결하는 operator-only smoke다.
object AuthCutoverSmoke {
    private val objectMapper: ObjectMapper = jacksonObjectMapper()

    @JvmStatic
    fun main(args: Array<String>) {
        val mode = args.singleOrNull()
        try {
            when (mode) {
                "capture" -> capture(System.getenv(), defaultEvidencePath(), Clock.systemUTC())
                "verify" -> verifyAfterCutover(System.getenv(), defaultEvidencePath(), Clock.systemUTC())
                else -> throw AuthCutoverException("unexpected_mode")
            }
            println("auth cutover smoke completed: $mode")
        } catch (exception: AuthCutoverException) {
            // HTTP body, token, digest, password는 출력하지 않고 allowlisted check code만 남긴다.
            System.err.println("auth cutover smoke failed: ${exception.checkCode}")
            exitProcess(1)
        } catch (exception: Exception) {
            System.err.println("auth cutover smoke failed: unexpected_failure")
            exitProcess(1)
        }
    }

    fun capture(
        environment: Map<String, String>,
        evidencePath: Path,
        clock: Clock = Clock.systemUTC(),
    ) {
        val baseUrl = normalizeBaseUrl(required(environment, BASE_URL_ENV))
        val oldToken = required(environment, OLD_TOKEN_ENV)
        val now = clock.instant()
        val expiresAt = parseJwtExpiration(oldToken)
        if (Duration.between(now, expiresAt).seconds < PRE_MIN_REMAINING_SECONDS) {
            throw AuthCutoverException("pre_token_lifetime")
        }
        if (healthStatus(baseUrl, oldToken) != 200) {
            throw AuthCutoverException("pre_health_status")
        }

        val evidence =
            objectMapper.createObjectNode().apply {
                put("schemaVersion", EVIDENCE_SCHEMA_VERSION)
                put("baseUrl", baseUrl)
                put("tokenSha256", sha256(oldToken))
                put("tokenExpiresAt", expiresAt.epochSecond)
                put("capturedAt", now.epochSecond)
                put("preflightStatus", 200)
            }
        writeAtomically(evidencePath, objectMapper.writeValueAsBytes(evidence))
    }

    fun verifyAfterCutover(
        environment: Map<String, String>,
        evidencePath: Path,
        clock: Clock = Clock.systemUTC(),
    ) {
        val baseUrl = normalizeBaseUrl(required(environment, BASE_URL_ENV))
        val oldToken = required(environment, OLD_TOKEN_ENV)
        val userPassword = required(environment, USER_PASSWORD_ENV)
        val adminPassword = required(environment, ADMIN_PASSWORD_ENV)
        val evidence = readEvidence(evidencePath)
        validateEvidence(evidence, baseUrl, oldToken, clock.instant())

        if (healthStatus(baseUrl, oldToken) != 401) {
            throw AuthCutoverException("post_old_token_status")
        }
        val userToken = login(baseUrl, "demo-user", userPassword, "usr_demo_user", "USER")
        val adminToken = login(baseUrl, "demo-admin", adminPassword, "usr_demo_admin", "ADMIN")
        if (userToken == adminToken) {
            throw AuthCutoverException("post_token_distinct")
        }
        if (healthStatus(baseUrl, userToken) != 200) {
            throw AuthCutoverException("post_user_health_status")
        }
        if (healthStatus(baseUrl, adminToken) != 200) {
            throw AuthCutoverException("post_admin_health_status")
        }
        if (authorizedGetStatus(baseUrl, ADMIN_BOUNDARY_PATH, userToken) != 403) {
            throw AuthCutoverException("post_user_admin_boundary")
        }
        if (authorizedGetStatus(baseUrl, ADMIN_BOUNDARY_PATH, adminToken) != 200) {
            throw AuthCutoverException("post_admin_boundary")
        }
        try {
            Files.delete(evidencePath)
        } catch (exception: Exception) {
            throw AuthCutoverException("evidence_delete", exception)
        }
    }

    private fun validateEvidence(
        evidence: JsonNode,
        baseUrl: String,
        oldToken: String,
        now: Instant,
    ) {
        if (evidence.path("schemaVersion").intValue() != EVIDENCE_SCHEMA_VERSION) {
            throw AuthCutoverException("evidence_schema")
        }
        if (evidence.path("baseUrl").stringValue() != baseUrl || evidence.path("preflightStatus").intValue() != 200) {
            throw AuthCutoverException("evidence_binding")
        }
        val expectedDigest =
            evidence.path("tokenSha256").stringValue()?.takeIf(SHA256_PATTERN::matches)
                ?: throw AuthCutoverException("evidence_digest")
        val suppliedDigest = sha256(oldToken)
        if (
            !MessageDigest.isEqual(
                expectedDigest.toByteArray(StandardCharsets.US_ASCII),
                suppliedDigest.toByteArray(StandardCharsets.US_ASCII),
            )
        ) {
            throw AuthCutoverException("same_token_digest")
        }
        val tokenExpiration = parseJwtExpiration(oldToken)
        val evidenceExpiration = evidence.path("tokenExpiresAt").longValue()
        if (tokenExpiration.epochSecond != evidenceExpiration) {
            throw AuthCutoverException("evidence_expiration")
        }
        val capturedAt = Instant.ofEpochSecond(evidence.path("capturedAt").longValue())
        val age = Duration.between(capturedAt, now).seconds
        if (age !in 0..POST_MAX_CAPTURE_AGE_SECONDS) {
            throw AuthCutoverException("evidence_age")
        }
        if (Duration.between(now, tokenExpiration).seconds < POST_MIN_REMAINING_SECONDS) {
            throw AuthCutoverException("post_token_lifetime")
        }
    }

    private fun login(
        baseUrl: String,
        username: String,
        password: String,
        expectedUserId: String,
        expectedRole: String,
    ): String {
        val body = objectMapper.writeValueAsString(mapOf("username" to username, "password" to password))
        val response =
            send(
                HttpRequest
                    .newBuilder(URI.create("$baseUrl/api/v1/auth/login"))
                    .timeout(HTTP_TIMEOUT)
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
                    .build(),
                includeBody = true,
            )
        if (response.status != 200) {
            throw AuthCutoverException(if (expectedRole == "ADMIN") "post_admin_login_status" else "post_user_login_status")
        }
        val root = parseBoundedJson(response.body)
        val userId = root.at("/data/user/userId").stringValue()
        val role = root.at("/data/user/role").stringValue()
        val accessToken = root.at("/data/accessToken").stringValue()?.takeIf { it.isNotBlank() }
        if (userId != expectedUserId || role != expectedRole || accessToken == null) {
            throw AuthCutoverException(if (expectedRole == "ADMIN") "post_admin_login_contract" else "post_user_login_contract")
        }
        validateTokenIdentity(accessToken, expectedUserId, expectedRole)
        return accessToken
    }

    private fun healthStatus(
        baseUrl: String,
        token: String,
    ): Int = authorizedGetStatus(baseUrl, "/api/v1/system/health", token)

    private fun authorizedGetStatus(
        baseUrl: String,
        path: String,
        token: String,
    ): Int =
        send(
            HttpRequest
                .newBuilder(URI.create("$baseUrl$path"))
                .timeout(HTTP_TIMEOUT)
                .header("Authorization", "Bearer $token")
                .GET()
                .build(),
            includeBody = false,
        ).status

    private fun send(
        request: HttpRequest,
        includeBody: Boolean,
    ): SmokeHttpResponse =
        try {
            if (includeBody) {
                val response = httpClient().send(request, HttpResponse.BodyHandlers.ofInputStream())
                val bytes = response.body().use { it.readNBytes(MAX_HTTP_BODY_BYTES + 1) }
                if (bytes.size > MAX_HTTP_BODY_BYTES) {
                    throw AuthCutoverException("http_body_limit")
                }
                SmokeHttpResponse(response.statusCode(), bytes)
            } else {
                val response = httpClient().send(request, HttpResponse.BodyHandlers.discarding())
                SmokeHttpResponse(response.statusCode(), ByteArray(0))
            }
        } catch (exception: AuthCutoverException) {
            throw exception
        } catch (exception: Exception) {
            throw AuthCutoverException("http_transport", exception)
        }

    private fun httpClient(): HttpClient =
        HttpClient
            .newBuilder()
            .connectTimeout(HTTP_TIMEOUT)
            .followRedirects(HttpClient.Redirect.NEVER)
            .build()

    private fun parseBoundedJson(bytes: ByteArray): JsonNode =
        try {
            objectMapper.readTree(bytes)
        } catch (exception: Exception) {
            throw AuthCutoverException("http_json", exception)
        }

    private fun parseJwtExpiration(token: String): Instant {
        val root = parseJwtPayload(token)
        val expiration = root.path("exp")
        if (!expiration.isIntegralNumber || !expiration.canConvertToLong()) {
            throw AuthCutoverException("token_expiration")
        }
        return try {
            Instant.ofEpochSecond(expiration.longValue())
        } catch (exception: Exception) {
            throw AuthCutoverException("token_expiration", exception)
        }
    }

    private fun validateTokenIdentity(
        token: String,
        expectedUserId: String,
        expectedRole: String,
    ) {
        val root = parseJwtPayload(token)
        val securityVersion = root.path("securityVersion")
        if (
            root.path("sub").stringValue() != expectedUserId ||
            root.path("role").stringValue() != expectedRole ||
            !securityVersion.isIntegralNumber ||
            !securityVersion.canConvertToLong() ||
            securityVersion.longValue() <= 0
        ) {
            throw AuthCutoverException(if (expectedRole == "ADMIN") "post_admin_login_contract" else "post_user_login_contract")
        }
        parseJwtExpiration(token)
    }

    private fun parseJwtPayload(token: String): JsonNode {
        if (token.length !in 16..MAX_TOKEN_LENGTH) {
            throw AuthCutoverException("token_format")
        }
        val segments = token.split('.')
        if (segments.size != 3 || segments.any { it.isBlank() }) {
            throw AuthCutoverException("token_format")
        }
        val payload =
            try {
                Base64.getUrlDecoder().decode(segments[1])
            } catch (exception: IllegalArgumentException) {
                throw AuthCutoverException("token_format", exception)
            }
        if (payload.size > MAX_TOKEN_PAYLOAD_BYTES) {
            throw AuthCutoverException("token_payload_limit")
        }
        return parseBoundedJson(payload)
    }

    private fun readEvidence(path: Path): JsonNode {
        if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)) {
            throw AuthCutoverException("evidence_missing")
        }
        val bytes = Files.readAllBytes(path)
        if (bytes.isEmpty() || bytes.size > MAX_EVIDENCE_BYTES) {
            throw AuthCutoverException("evidence_size")
        }
        return try {
            objectMapper.readTree(bytes)
        } catch (exception: Exception) {
            throw AuthCutoverException("evidence_json", exception)
        }
    }

    private fun writeAtomically(
        path: Path,
        bytes: ByteArray,
    ) {
        val absolutePath = path.toAbsolutePath().normalize()
        val parent = absolutePath.parent ?: throw AuthCutoverException("evidence_path")
        Files.createDirectories(parent)
        if (Files.exists(absolutePath, LinkOption.NOFOLLOW_LINKS)) {
            throw AuthCutoverException("evidence_exists")
        }
        val temporary = Files.createTempFile(parent, ".pre-cutover-", ".tmp")
        try {
            runCatching {
                Files.setPosixFilePermissions(temporary, PosixFilePermissions.fromString("rw-------"))
            }
            Files.write(temporary, bytes)
            try {
                // 같은 directory의 완성된 inode를 hard-link해 create-if-absent와 완전한 content visibility를 함께 보장한다.
                Files.createLink(absolutePath, temporary)
                runCatching { Files.deleteIfExists(temporary) }
            } catch (exception: FileAlreadyExistsException) {
                throw AuthCutoverException("evidence_exists", exception)
            }
        } catch (exception: Exception) {
            runCatching { Files.deleteIfExists(temporary) }
            if (exception is AuthCutoverException) throw exception
            throw AuthCutoverException("evidence_write", exception)
        }
    }

    private fun normalizeBaseUrl(value: String): String {
        val uri =
            try {
                URI.create(value)
            } catch (exception: IllegalArgumentException) {
                throw AuthCutoverException("base_url", exception)
            }
        val scheme = uri.scheme?.lowercase(Locale.ROOT)
        val host = uri.host ?: throw AuthCutoverException("base_url")
        val normalizedHost = host.lowercase(Locale.ROOT)
        val loopback = normalizedHost == "localhost" || normalizedHost == "127.0.0.1" || normalizedHost == "::1"
        if (
            scheme !in setOf("http", "https") ||
            (scheme == "http" && !loopback) ||
            uri.rawUserInfo != null ||
            uri.rawQuery != null ||
            uri.rawFragment != null ||
            uri.port == 0 ||
            (uri.path.isNotEmpty() && uri.path != "/")
        ) {
            throw AuthCutoverException("base_url")
        }
        return URI(scheme, null, normalizedHost, uri.port, null, null, null).toASCIIString()
    }

    private fun required(
        environment: Map<String, String>,
        name: String,
    ): String =
        environment[name]?.takeIf { it.isNotBlank() }
            ?: throw AuthCutoverException("missing_$name")

    private fun sha256(value: String): String =
        HexFormat.of().formatHex(
            MessageDigest.getInstance("SHA-256").digest(value.toByteArray(StandardCharsets.UTF_8)),
        )

    private fun defaultEvidencePath(): Path = Path.of("build", "auth-cutover", "pre-cutover.json")

    private data class SmokeHttpResponse(
        val status: Int,
        val body: ByteArray,
    )

    private const val EVIDENCE_SCHEMA_VERSION = 1
    private const val PRE_MIN_REMAINING_SECONDS = 7_200L
    private const val POST_MIN_REMAINING_SECONDS = 3_600L
    private const val POST_MAX_CAPTURE_AGE_SECONDS = 1_800L
    private const val MAX_TOKEN_LENGTH = 16_384
    private const val MAX_TOKEN_PAYLOAD_BYTES = 16_384
    private const val MAX_HTTP_BODY_BYTES = 65_536
    private const val MAX_EVIDENCE_BYTES = 4_096
    private const val BASE_URL_ENV = "AUTH_SMOKE_BASE_URL"
    private const val OLD_TOKEN_ENV = "AUTH_SMOKE_PRE_CUTOVER_TOKEN"
    private const val USER_PASSWORD_ENV = "AUTH_SMOKE_USER_PASSWORD"
    private const val ADMIN_PASSWORD_ENV = "AUTH_SMOKE_ADMIN_PASSWORD"
    private const val ADMIN_BOUNDARY_PATH = "/actuator/metrics"
    private val HTTP_TIMEOUT: Duration = Duration.ofSeconds(10)
    private val SHA256_PATTERN = Regex("^[0-9a-f]{64}$")
}

class AuthCutoverException(
    val checkCode: String,
    cause: Throwable? = null,
) : RuntimeException(checkCode, cause)
