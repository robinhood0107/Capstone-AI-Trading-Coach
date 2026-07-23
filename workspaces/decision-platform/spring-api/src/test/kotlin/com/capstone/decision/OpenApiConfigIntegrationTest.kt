package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.context.annotation.Import
import org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.get
import org.springframework.test.web.servlet.setup.DefaultMockMvcBuilder
import org.springframework.test.web.servlet.setup.MockMvcBuilders
import org.springframework.web.context.WebApplicationContext
import tools.jackson.databind.ObjectMapper
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import java.util.HexFormat

// swagger-ui 수동 smoke 전에 OpenAPI security/group 계약이 자동으로 노출되는지 확인한다.
@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.jdbc.autoconfigure.DataSourceAutoConfiguration,org.springframework.boot.hibernate.autoconfigure.HibernateJpaAutoConfiguration,org.springframework.boot.data.jpa.autoconfigure.DataJpaRepositoriesAutoConfiguration,org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
@Import(TestAuthRepositoryConfiguration::class)
class OpenApiConfigIntegrationTest(
    @Autowired private val webApplicationContext: WebApplicationContext,
    @Autowired private val objectMapper: ObjectMapper,
) : SpringApiIntegrationTestBase() {
    private lateinit var mockMvc: MockMvc

    @BeforeEach
    fun setUpMockMvc() {
        // 문서 endpoint는 permitAll이지만 실제 보안 체인 안에서 접근 가능해야 한다.
        mockMvc =
            MockMvcBuilders
                .webAppContextSetup(webApplicationContext)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    // bearerAuth scheme이 없으면 swagger Authorize smoke가 토큰을 넣을 수 없다.
    @Test
    fun `openapi exposes bearer auth scheme`() {
        mockMvc
            .get("/v3/api-docs")
            .andExpect {
                status { isOk() }
                jsonPath("$.components.securitySchemes.bearerAuth.type") { value("http") }
                jsonPath("$.components.securitySchemes.bearerAuth.scheme") { value("bearer") }
            }
    }

    // public/admin 그룹은 이후 API가 늘어날 때 문서 경계를 유지하는 기준점이다.
    @Test
    fun `springdoc exposes public and admin groups`() {
        mockMvc.get("/v3/api-docs/public").andExpect { status { isOk() } }
        mockMvc.get("/v3/api-docs/admin").andExpect { status { isOk() } }
    }

    @Test
    fun `openapi documents login as public and exposes trust root response fields`() {
        mockMvc
            .get("/v3/api-docs")
            .andExpect {
                status { isOk() }
                jsonPath("$.paths['/api/v1/auth/login'].post.security.length()") { value(0) }
                jsonPath("$.components.schemas.LoginRequest.required") { isArray() }
                jsonPath("$.components.schemas.LoginResponse.properties.user") { exists() }
                jsonPath("$.components.schemas.LoginUserResponse.properties.userId") { exists() }
                jsonPath("$.components.schemas.LoginUserResponse.properties.role") { exists() }
                jsonPath("$.components.schemas.LoginUserResponse.properties.role.enum[0]") { value("USER") }
                jsonPath("$.components.schemas.LoginUserResponse.properties.role.enum[1]") { value("ADMIN") }
                jsonPath("$.paths['/api/v1/auth/login'].post.responses['200'].content['application/json'].schema['\$ref']") {
                    value("#/components/schemas/ApiResponseLoginResponse")
                }
                jsonPath("$.components.schemas.ApiResponseLoginResponse.properties.data.oneOf[0]['\$ref']") {
                    value("#/components/schemas/LoginResponse")
                }
                jsonPath("$.paths['/api/v1/auth/login'].post.responses['400']") { exists() }
                jsonPath("$.paths['/api/v1/auth/login'].post.responses['401']") { exists() }
                jsonPath("$.paths['/api/v1/auth/login'].post.responses['429']") { exists() }
            }
    }

    @Test
    fun `openapi exposes the locked dialect and catalog digest without premature Principle paths`() {
        val body =
            mockMvc
                .get("/v3/api-docs")
                .andExpect {
                    status { isOk() }
                }.andReturn()
                .response
                .contentAsByteArray
        val document = objectMapper.readTree(body)
        val repositoryRoot = findRepositoryRoot()
        val catalogBytes =
            Files.readAllBytes(
                repositoryRoot.resolve("contracts/catalogs/s2-1-principle-contract.v1.json"),
            )
        val expectedDigest =
            HexFormat
                .of()
                .formatHex(MessageDigest.getInstance("SHA-256").digest(catalogBytes))

        assertEquals("3.1.0", document.path("openapi").stringValue())
        assertEquals(
            "https://spec.openapis.org/oas/3.1/dialect/base",
            document.path("jsonSchemaDialect").stringValue(),
        )
        assertEquals("s2-1-principle-contract/v1", document.path("x-s2-1-contract-id").stringValue())
        assertEquals(expectedDigest, document.path("x-s2-1-contract-sha256").stringValue())
        assertTrue(Regex("^[0-9a-f]{64}$").matches(expectedDigest))

        val paths =
            document
                .path("paths")
                .propertyNames()
                .asSequence()
                .toList()
        assertFalse(
            paths.any { it == "/api/v1/principle-presets" || it.startsWith("/api/v1/principles") },
            "amendment OpenAPI must not advertise S2.1 endpoints before their controllers exist",
        )
    }

    private fun findRepositoryRoot(): Path {
        var current = Path.of(System.getProperty("user.dir")).toAbsolutePath()
        while (!Files.exists(current.resolve("AGENTS.md"))) {
            current = current.parent ?: error("repository root was not found")
        }
        return current
    }
}
