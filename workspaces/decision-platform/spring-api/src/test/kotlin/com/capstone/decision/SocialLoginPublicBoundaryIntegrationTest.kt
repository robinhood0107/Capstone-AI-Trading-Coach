package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.boot.test.context.TestConfiguration
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Import
import org.springframework.security.oauth2.client.registration.ClientRegistration
import org.springframework.security.oauth2.client.registration.ClientRegistrationRepository
import org.springframework.security.oauth2.client.registration.InMemoryClientRegistrationRepository
import org.springframework.security.oauth2.core.AuthorizationGrantType
import org.springframework.security.oauth2.core.ClientAuthenticationMethod
import org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity
import org.springframework.test.context.ActiveProfiles
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
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermission
import java.security.SecureRandom

/** Starts the full product security chains with offline provider registrations and an isolated DB. */
@Testcontainers
@ActiveProfiles("mars-full")
@Import(
    SocialLoginPublicBoundaryIntegrationTest.OfflineSocialLoginRegistrations::class,
    TestActorCapabilityConfiguration::class,
)
@SpringBootTest(
    properties = ["spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration"],
)
class SocialLoginPublicBoundaryIntegrationTest(
    @Autowired private val context: WebApplicationContext,
    @Autowired private val objectMapper: ObjectMapper,
) {
    private lateinit var mvc: MockMvc

    @BeforeEach
    fun setUp() {
        mvc =
            MockMvcBuilders
                .webAppContextSetup(context)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    @Test
    fun `public password login rejects invalid credentials and both provider starts use state`() {
        mvc
            .post("/api/v1/auth/login") {
                contentType = org.springframework.http.MediaType.APPLICATION_JSON
                content = """{"identifier":"demo-user","password":"wrong"}"""
            }.andExpect { status { isUnauthorized() } }
        mvc
            .post("/api/v1/auth/login") {
                contentType = org.springframework.http.MediaType.APPLICATION_JSON
                content = """{"identifier":"demo-admin","password":"wrong"}"""
            }.andExpect { status { isUnauthorized() } }
        mvc
            .post("/api/v1/auth/signup") {
                contentType = org.springframework.http.MediaType.APPLICATION_JSON
                content = """{"email":"new@example.test","password":"short"}"""
            }.andExpect { status { isBadRequest() } }
        mvc.get("/api/v1/auth/identities").andExpect { status { isUnauthorized() } }
        mvc.post("/api/v1/brokerage/mock/credential/connect").andExpect { status { isUnauthorized() } }
        mvc.post("/api/v1/brokerage/mock/credential/certify").andExpect { status { isUnauthorized() } }
        mvc.post("/api/v1/brokerage/mock/credential/certify/recovery-confirm").andExpect { status { isUnauthorized() } }
        mvc.get("/api/v1/auth/oidc/start/google").andExpect {
            status { isFound() }
            header { string("Location", org.hamcrest.Matchers.containsString("accounts.google.com")) }
            header { string("Location", org.hamcrest.Matchers.containsString("state=")) }
        }
        mvc.get("/api/v1/auth/oidc/start/kakao").andExpect {
            status { isFound() }
            header { string("Location", org.hamcrest.Matchers.containsString("kauth.kakao.com")) }
            header { string("Location", org.hamcrest.Matchers.containsString("state=")) }
        }
    }

    @Test
    fun `password signup login and authentication method reads stay owner scoped`() {
        val firstEmail = "first-${java.util.UUID.randomUUID()}@example.test"
        val secondEmail = "second-${java.util.UUID.randomUUID()}@example.test"
        val password = "test-password-" + "x".repeat(15)

        fun signup(email: String) =
            mvc
                .post("/api/v1/auth/signup") {
                    contentType = org.springframework.http.MediaType.APPLICATION_JSON
                    content = objectMapper.writeValueAsString(mapOf("email" to email, "password" to password))
                }.andReturn()
                .response

        val firstResponse = signup(firstEmail)
        assertEquals(200, firstResponse.status)
        val firstData = objectMapper.readTree(firstResponse.contentAsString).path("data")
        val firstUserId = firstData.path("user").path("userId").asText()
        val firstToken = firstData.path("accessToken").asText()
        assertTrue(firstUserId.startsWith("usr_"))
        assertTrue(firstToken.isNotBlank())

        val duplicate = signup(firstEmail)
        assertEquals(409, duplicate.status)

        val passwordLogin =
            mvc
                .post("/api/v1/auth/login") {
                    contentType = org.springframework.http.MediaType.APPLICATION_JSON
                    content = objectMapper.writeValueAsString(mapOf("identifier" to firstEmail, "password" to password))
                }.andReturn()
                .response
        assertEquals(200, passwordLogin.status)
        assertEquals(
            firstUserId,
            objectMapper
                .readTree(passwordLogin.contentAsString)
                .path("data")
                .path("user")
                .path("userId")
                .asText(),
        )

        val secondResponse = signup(secondEmail)
        assertEquals(200, secondResponse.status)
        val secondData = objectMapper.readTree(secondResponse.contentAsString).path("data")
        val secondUserId = secondData.path("user").path("userId").asText()
        assertNotEquals(firstUserId, secondUserId)

        val firstMethods =
            mvc
                .get("/api/v1/auth/identities") {
                    header("Authorization", "Bearer $firstToken")
                }.andReturn()
                .response
        assertEquals(200, firstMethods.status)
        val firstEmailRows =
            objectMapper.readTree(firstMethods.contentAsString).path("data").let { rows ->
                (0 until rows.size()).map { rows.get(it).path("email").asString() }
            }
        assertEquals(listOf(firstEmail), firstEmailRows)

        val secondMethods =
            mvc
                .get("/api/v1/auth/identities") {
                    header("Authorization", "Bearer ${secondData.path("accessToken").asText()}")
                }.andReturn()
                .response
        assertEquals(200, secondMethods.status)
        val secondEmailRows =
            objectMapper.readTree(secondMethods.contentAsString).path("data").let { rows ->
                (0 until rows.size()).map { rows.get(it).path("email").asString() }
            }
        assertEquals(listOf(secondEmail), secondEmailRows)
    }

    @TestConfiguration
    class OfflineSocialLoginRegistrations {
        @Bean
        fun clientRegistrationRepository(): ClientRegistrationRepository =
            InMemoryClientRegistrationRepository(
                listOf(
                    ClientRegistration
                        .withRegistrationId("google")
                        .clientId("fixture-client")
                        .clientSecret("fixture-secret")
                        .clientName("Google")
                        .clientAuthenticationMethod(ClientAuthenticationMethod.CLIENT_SECRET_BASIC)
                        .authorizationGrantType(AuthorizationGrantType.AUTHORIZATION_CODE)
                        .redirectUri("https://mars.example.test/api/v1/auth/oidc/callback/google")
                        .scope("openid", "email")
                        .authorizationUri("https://accounts.google.com/o/oauth2/v2/auth")
                        .tokenUri("https://oauth2.googleapis.com/token")
                        .jwkSetUri("https://www.googleapis.com/oauth2/v3/certs")
                        .issuerUri("https://accounts.google.com")
                        .userNameAttributeName("sub")
                        .build(),
                    ClientRegistration
                        .withRegistrationId("kakao")
                        .clientId("fixture-kakao-client")
                        .clientSecret("fixture-kakao-secret")
                        .clientName("Kakao")
                        .clientAuthenticationMethod(ClientAuthenticationMethod.CLIENT_SECRET_POST)
                        .authorizationGrantType(AuthorizationGrantType.AUTHORIZATION_CODE)
                        .redirectUri("https://mars.example.test/api/v1/auth/oidc/callback/kakao")
                        .authorizationUri("https://kauth.kakao.com/oauth/authorize")
                        .tokenUri("https://kauth.kakao.com/oauth/token")
                        .userInfoUri("https://kapi.kakao.com/v2/user/me")
                        .userNameAttributeName("id")
                        .build(),
                ),
            )
    }

    companion object {
        private val brokerageKekDirectory: Path =
            Files.createTempDirectory("mars-oidc-brokerage-kek").also { directory ->
                Files.setPosixFilePermissions(
                    directory,
                    setOf(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE, PosixFilePermission.OWNER_EXECUTE),
                )
                val key = ByteArray(32).also(SecureRandom()::nextBytes)
                val file = directory.resolve("brokerage-kek-v1.key")
                Files.write(file, key)
                Files.setPosixFilePermissions(file, setOf(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE))
                key.fill(0)
            }
        private val postgresImage =
            DockerImageName
                .parse("pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb")
                .asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            stablePostgresContainer(postgresImage)
                .withDatabaseName("decision_auth")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")

        @DynamicPropertySource
        @JvmStatic
        fun properties(registry: DynamicPropertyRegistry) {
            SpringApiIntegrationTestBase.registerSharedApplicationProperties(
                registry,
                includeDemoPasswordBundles = false,
            )
            registry.add("spring.datasource.url", postgres::getJdbcUrl)
            registry.add("spring.datasource.username", postgres::getUsername)
            registry.add("spring.datasource.password", postgres::getPassword)
            registry.add("spring.flyway.user", postgres::getUsername)
            registry.add("spring.flyway.password", postgres::getPassword)
            registry.add("MARS_PUBLIC_SURFACE_MODE") { "FULL" }
            registry.add("MARS_PUBLIC_ORIGIN") { "https://mars.example.test" }
            registry.add("GOOGLE_OIDC_ADMIN_SUBJECT_SHA256") { "a".repeat(64) }
            registry.add("GOOGLE_OIDC_CLIENT_ID") { "fixture-client" }
            registry.add("GOOGLE_OIDC_CLIENT_SECRET") { "fixture-secret" }
            registry.add("KAKAO_OAUTH_CLIENT_ID") { "fixture-kakao-client" }
            registry.add("KAKAO_OAUTH_CLIENT_SECRET") { "fixture-kakao-secret" }
            registry.add("MARS_BROKERAGE_KEK_DIRECTORY") { brokerageKekDirectory.toString() }
            registry.add("GOOGLE_OIDC_REDIRECT_URI") {
                "https://mars.example.test/api/v1/auth/oidc/callback/google"
            }
            registry.add("KAKAO_OAUTH_REDIRECT_URI") {
                "https://mars.example.test/api/v1/auth/oidc/callback/kakao"
            }
        }
    }
}
