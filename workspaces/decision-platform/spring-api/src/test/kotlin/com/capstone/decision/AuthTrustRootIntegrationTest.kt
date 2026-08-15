package com.capstone.decision

import com.capstone.decision.infrastructure.security.DemoAccountService
import com.capstone.decision.infrastructure.security.DemoCredentialHashPolicy
import com.capstone.decision.infrastructure.security.LoginAttemptLimiter
import io.jsonwebtoken.Jwts
import io.jsonwebtoken.security.Keys
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.http.MediaType
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
import org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.get
import org.springframework.test.web.servlet.post
import org.springframework.test.web.servlet.setup.DefaultMockMvcBuilder
import org.springframework.test.web.servlet.setup.MockMvcBuilders
import org.springframework.web.context.WebApplicationContext
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import tools.jackson.databind.ObjectMapper
import java.nio.charset.StandardCharsets
import java.time.Instant
import java.util.Date

// 실제 PostgreSQL user row를 기준으로 login과 매 요청 token 재검증이 함께 움직이는지 잠근다.
@Testcontainers
@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
class AuthTrustRootIntegrationTest(
    @Autowired private val webApplicationContext: WebApplicationContext,
    @Autowired private val objectMapper: ObjectMapper,
    @Autowired private val jdbcTemplate: JdbcTemplate,
    @Autowired private val demoAccountService: DemoAccountService,
    @Autowired private val loginAttemptLimiter: LoginAttemptLimiter,
) : SpringApiIntegrationTestBase() {
    private lateinit var mockMvc: MockMvc

    @BeforeEach
    fun setUp() {
        restoreDemoUsers()
        clearLoginAttempts()
        mockMvc =
            MockMvcBuilders
                .webAppContextSetup(webApplicationContext)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    @AfterEach
    fun restoreDatabase() {
        restoreDemoUsers()
    }

    @Test
    fun `V57 preserves the V7 active demo trust root with strength twelve bcrypt hashes`() {
        val versions = jdbcTemplate.queryForList("select version from flyway_schema_history order by installed_rank", String::class.java)
        // V7 is the Java migration that installs the active demo credential trust root.
        assertEquals((1..70).map(Int::toString), versions)

        val users =
            jdbcTemplate.query(
                """
                select user_id, username, role, status, security_version, password_hash
                from users
                where user_id in ('usr_demo_user', 'usr_demo_admin')
                order by user_id
                """.trimIndent(),
            ) { result, _ ->
                listOf(
                    result.getString("user_id"),
                    result.getString("username"),
                    result.getString("role"),
                    result.getString("status"),
                    result.getLong("security_version").toString(),
                    result.getString("password_hash"),
                )
            }

        assertEquals(2, users.size)
        assertEquals(listOf("usr_demo_admin", "demo-admin", "ADMIN", "ACTIVE", "1"), users[0].take(5))
        assertEquals(listOf("usr_demo_user", "demo-user", "USER", "ACTIVE", "1"), users[1].take(5))
        assertTrue(users.all { BCRYPT_12_PATTERN.matches(it.last()) })
        assertNotEquals(userPassword(), users[1].last())
        assertNotEquals(adminPassword(), users[0].last())
        val evidenceLengths =
            jdbcTemplate.queryForList(
                """
                select octet_length(credential_reuse_tag) as tag_length,
                       octet_length(credential_bundle_mac) as mac_length,
                       credential_policy_version
                from users
                where user_id in ('usr_demo_user', 'usr_demo_admin')
                """.trimIndent(),
            )
        assertEquals(2, evidenceLengths.size)
        assertTrue(
            evidenceLengths.all {
                (it["tag_length"] as Number).toInt() == 32 &&
                    (it["mac_length"] as Number).toInt() == 32 &&
                    (it["credential_policy_version"] as Number).toInt() == 1
            },
        )
    }

    @Test
    fun `issued JWT pins HS256 and uses internal user id subject with all required claims`() {
        val token = login("demo-user", userPassword(), "usr_demo_user", "USER")
        val parsed =
            Jwts
                .parser()
                .verifyWith(signingKey())
                .requireIssuer(jwtIssuer())
                .requireAudience(jwtAudience())
                .build()
                .parseSignedClaims(token)

        assertEquals("HS256", parsed.header.algorithm)
        assertEquals("usr_demo_user", parsed.payload.subject)
        assertEquals("USER", parsed.payload["role"])
        assertEquals(1L, (parsed.payload["securityVersion"] as Number).toLong())
        assertFalse(parsed.payload.containsKey("userId"))
        assertTrue(parsed.payload.issuedAt != null)
        assertTrue(parsed.payload.expiration.after(parsed.payload.issuedAt))
    }

    @Test
    fun `JWT rejects wrong algorithm issuer audience subject timing role version and missing issued at`() {
        val now = Instant.now()
        val invalidTokens =
            listOf(
                token(issuer = "wrong-issuer"),
                token(audience = "wrong-audience"),
                token(additionalAudience = "unexpected-audience"),
                token(subject = "demo-user"),
                token(issuedAt = now.plusSeconds(120), expiresAt = now.plusSeconds(3_600)),
                token(role = "ADMIN"),
                token(securityVersion = 2),
                token(includeIssuedAt = false),
                token(issuedAt = now.minusSeconds(7_200), expiresAt = now.minusSeconds(1)),
                token(algorithm = "HS512"),
            )

        invalidTokens.forEachIndexed { index, token ->
            assertUnauthorized(token, "req-invalid-token-$index")
        }
    }

    @Test
    fun `DB missing locked disabled role and security version changes revoke an issued token immediately`() {
        val token = login("demo-user", userPassword(), "usr_demo_user", "USER")

        jdbcTemplate.update("update users set status = 'LOCKED' where user_id = 'usr_demo_user'")
        assertUnauthorized(token, "req-locked-token")

        jdbcTemplate.update("update users set status = 'DISABLED' where user_id = 'usr_demo_user'")
        assertUnauthorized(token, "req-disabled-token")

        jdbcTemplate.update("update users set status = 'ACTIVE', security_version = 2 where user_id = 'usr_demo_user'")
        assertUnauthorized(token, "req-version-token")

        jdbcTemplate.update("update users set security_version = 1, role = 'ADMIN' where user_id = 'usr_demo_user'")
        assertUnauthorized(token, "req-role-token")

        jdbcTemplate.update("delete from users where user_id = 'usr_demo_user'")
        assertUnauthorized(token, "req-missing-token")
    }

    @Test
    fun `unknown user and wrong password share one response while dummy verification remains strength twelve`() {
        val wrongPassword = postInvalidLogin("demo-user", "not-the-password", "req-invalid-credential")
        val unknownUser = postInvalidLogin("missing-user", "not-the-password", "req-invalid-credential")

        assertEquals(wrongPassword, unknownUser)
        val dummyHashField = DemoAccountService::class.java.getDeclaredField("dummyPasswordHash")
        dummyHashField.isAccessible = true
        assertTrue(BCRYPT_12_PATTERN.matches(dummyHashField.get(demoAccountService) as String))
        assertThrows<IllegalArgumentException> { DemoCredentialHashPolicy.requireValid("") }
        assertThrows<IllegalArgumentException> { DemoCredentialHashPolicy.requireValid("not-bcrypt") }
        assertThrows<IllegalArgumentException> {
            DemoCredentialHashPolicy.requireValid(
                (dummyHashField.get(demoAccountService) as String).replace("\$12\$", "\$10\$"),
            )
        }
    }

    @Test
    fun `public login ignores a stale bearer token and authenticates the supplied credential`() {
        mockMvc
            .post("/api/v1/auth/login") {
                contentType = MediaType.APPLICATION_JSON
                content = objectMapper.writeValueAsString(mapOf("username" to "demo-user", "password" to userPassword()))
                header("Authorization", "Bearer stale-pre-cutover-token")
                header("X-Request-Id", "req-login-with-stale-bearer")
            }.andExpect {
                status { isOk() }
                jsonPath("$.data.user.userId") { value("usr_demo_user") }
                jsonPath("$.data.user.role") { value("USER") }
            }
    }

    @Test
    fun `public login rejects both roles when separately salted rows accept one plaintext`() {
        val sharedAdminHash = requireNotNull(BCryptPasswordEncoder(12).encode(userPassword()))
        assertNotEquals(TEST_USER_PASSWORD_HASH, sharedAdminHash)
        jdbcTemplate.update(
            "update users set password_hash = ? where user_id = 'usr_demo_admin'",
            sharedAdminHash,
        )

        postInvalidLogin("demo-admin", userPassword(), "req-shared-password-admin")
        postInvalidLogin("demo-user", userPassword(), "req-shared-password-user")
    }

    @Test
    fun `public login rejects input beyond the bcrypt byte boundary even when its prefix matches`() {
        val boundaryPassword = "가".repeat(24)
        val overlongPassword = boundaryPassword + "x"
        assertEquals(72, boundaryPassword.toByteArray(StandardCharsets.UTF_8).size)
        assertEquals(73, overlongPassword.toByteArray(StandardCharsets.UTF_8).size)
        val boundaryHash = requireNotNull(BCryptPasswordEncoder(12).encode(boundaryPassword))
        assertTrue(BCryptPasswordEncoder(12).matches(overlongPassword, boundaryHash))
        jdbcTemplate.update(
            "update users set password_hash = ? where user_id = 'usr_demo_user'",
            boundaryHash,
        )

        login("demo-user", boundaryPassword, "usr_demo_user", "USER")
        postInvalidLogin("demo-user", overlongPassword, "req-overlong-bcrypt-password")
    }

    private fun login(
        username: String,
        password: String,
        expectedUserId: String,
        expectedRole: String,
    ): String {
        val response =
            mockMvc
                .post("/api/v1/auth/login") {
                    contentType = MediaType.APPLICATION_JSON
                    content = objectMapper.writeValueAsString(mapOf("username" to username, "password" to password))
                    header("X-Request-Id", "req-auth-login-$username")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.data.user.userId") { value(expectedUserId) }
                    jsonPath("$.data.user.role") { value(expectedRole) }
                }.andReturn()
                .response
                .contentAsString
        return objectMapper.readTree(response).at("/data/accessToken").stringValue()
    }

    private fun postInvalidLogin(
        username: String,
        password: String,
        requestId: String,
    ): String =
        mockMvc
            .post("/api/v1/auth/login") {
                contentType = MediaType.APPLICATION_JSON
                content = objectMapper.writeValueAsString(mapOf("username" to username, "password" to password))
                header("X-Request-Id", requestId)
            }.andExpect {
                status { isUnauthorized() }
                jsonPath("$.error.code") { value("UNAUTHORIZED") }
            }.andReturn()
            .response
            .contentAsString

    private fun assertUnauthorized(
        token: String,
        requestId: String,
    ) {
        mockMvc
            .get("/api/v1/system/health") {
                bearer(token)
                header("X-Request-Id", requestId)
            }.andExpect {
                status { isUnauthorized() }
                jsonPath("$.error.code") { value("UNAUTHORIZED") }
            }
    }

    private fun token(
        issuer: String = jwtIssuer(),
        audience: String = jwtAudience(),
        additionalAudience: String? = null,
        subject: String = "usr_demo_user",
        issuedAt: Instant = Instant.now(),
        expiresAt: Instant = issuedAt.plusSeconds(3_600),
        role: String = "USER",
        securityVersion: Long = 1,
        includeIssuedAt: Boolean = true,
        algorithm: String = "HS256",
    ): String {
        val audienceBuilder =
            Jwts
                .builder()
                .issuer(issuer)
                .audience()
                .add(audience)
        additionalAudience?.let(audienceBuilder::add)
        val builder =
            audienceBuilder
                .and()
                .subject(subject)
                .claim("role", role)
                .claim("securityVersion", securityVersion)
                .expiration(Date.from(expiresAt))
        if (includeIssuedAt) {
            builder.issuedAt(Date.from(issuedAt))
        }
        return when (algorithm) {
            "HS256" -> builder.signWith(signingKey(), Jwts.SIG.HS256).compact()
            "HS512" -> builder.signWith(signingKey(), Jwts.SIG.HS512).compact()
            else -> error("unsupported test algorithm")
        }
    }

    private fun signingKey() = Keys.hmacShaKeyFor(jwtSecret().toByteArray(StandardCharsets.UTF_8))

    private fun clearLoginAttempts() {
        val attemptsField = LoginAttemptLimiter::class.java.getDeclaredField("attempts")
        attemptsField.isAccessible = true
        @Suppress("UNCHECKED_CAST")
        (attemptsField.get(loginAttemptLimiter) as MutableMap<String, *>).clear()
    }

    private fun restoreDemoUsers() {
        jdbcTemplate.update("delete from users where user_id in ('usr_demo_user', 'usr_demo_admin')")
        jdbcTemplate.update(
            """
            insert into users (
                user_id, username, role, password_hash, status, security_version,
                credential_reuse_tag, credential_bundle_mac, credential_policy_version
            )
            values (?, 'demo-user', 'USER', ?, 'ACTIVE', 1, ?, ?, 1),
                   (?, 'demo-admin', 'ADMIN', ?, 'ACTIVE', 1, ?, ?, 1)
            """.trimIndent(),
            "usr_demo_user",
            TEST_USER_PASSWORD_HASH,
            TEST_USER_VERIFIED_BUNDLE.reuseTag,
            TEST_USER_VERIFIED_BUNDLE.bundleMac,
            "usr_demo_admin",
            TEST_ADMIN_PASSWORD_HASH,
            TEST_ADMIN_VERIFIED_BUNDLE.reuseTag,
            TEST_ADMIN_VERIFIED_BUNDLE.bundleMac,
        )
    }

    companion object {
        private val BCRYPT_12_PATTERN = Regex("^\\$2[aby]\\$12\\$[./A-Za-z0-9]{53}$")
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            PostgreSQLContainer(postgresImage)
                .withDatabaseName("decision_auth")
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
