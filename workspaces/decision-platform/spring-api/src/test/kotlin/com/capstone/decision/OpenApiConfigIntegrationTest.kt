package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertEquals
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
    fun `openapi exposes the locked dialect catalog digest and real Principle paths`() {
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
        assertTrue(
            paths.containsAll(
                setOf(
                    "/api/v1/principle-presets",
                    "/api/v1/principles",
                    "/api/v1/principles/{principleId}",
                    "/api/v1/principles/{principleId}/versions",
                ),
            ),
            "implementation OpenAPI must advertise only paths backed by the real S2.1 controllers",
        )
        assertEquals("listPrinciplePresets", document.at("/paths/~1api~1v1~1principle-presets/get/operationId").stringValue())
        assertEquals("createPrinciple", document.at("/paths/~1api~1v1~1principles/post/operationId").stringValue())
        assertEquals("listPrinciples", document.at("/paths/~1api~1v1~1principles/get/operationId").stringValue())
        assertEquals("getPrinciple", document.at("/paths/~1api~1v1~1principles~1{principleId}/get/operationId").stringValue())
        assertEquals("updatePrinciple", document.at("/paths/~1api~1v1~1principles~1{principleId}/put/operationId").stringValue())
        assertEquals(
            "listPrincipleVersions",
            document.at("/paths/~1api~1v1~1principles~1{principleId}~1versions/get/operationId").stringValue(),
        )
    }

    @Test
    fun `openapi documents Principle success headers typed errors and locked component constraints`() {
        val document =
            objectMapper.readTree(
                mockMvc
                    .get("/v3/api-docs")
                    .andExpect {
                        status { isOk() }
                    }.andReturn()
                    .response
                    .contentAsByteArray,
            )

        val create = document.at("/paths/~1api~1v1~1principles/post/responses")
        assertEquals(
            "#/components/schemas/ApiResponsePrincipleCurrent",
            create.at("/201/content/application~1json/schema/\$ref").stringValue(),
        )
        assertEquals(
            "string",
            create.at("/201/headers/Location/schema/type").stringValue(),
        )
        assertEquals(
            "#/components/schemas/PrincipleValidationErrorResponse",
            create.at("/400/content/application~1json/schema/\$ref").stringValue(),
        )
        assertEquals(
            "#/components/schemas/PrincipleUnauthorizedErrorResponse",
            create.at("/401/content/application~1json/schema/\$ref").stringValue(),
        )
        assertEquals(
            "#/components/schemas/PrincipleForbiddenErrorResponse",
            create.at("/403/content/application~1json/schema/\$ref").stringValue(),
        )
        assertEquals(
            "#/components/schemas/PrinciplePayloadTooLargeErrorResponse",
            create.at("/413/content/application~1json/schema/\$ref").stringValue(),
        )

        val updateConflict =
            document.at(
                "/paths/~1api~1v1~1principles~1{principleId}/put/responses/409/content/application~1json/schema/oneOf",
            )
        assertEquals(2, updateConflict.size())
        assertEquals(
            setOf(
                "#/components/schemas/PrincipleConflictErrorResponse",
                "#/components/schemas/PrincipleVersionExhaustedErrorResponse",
            ),
            updateConflict.values().map { it.path("\$ref").stringValue() }.toSet(),
        )

        val current = document.at("/components/schemas/PrincipleCurrent")
        assertTrue(
            current
                .path("required")
                .values()
                .map { it.stringValue() }
                .toSet()
                .containsAll(
                    setOf(
                        "principleId",
                        "presetId",
                        "title",
                        "mode",
                        "status",
                        "version",
                        "rules",
                        "createdAt",
                        "updatedAt",
                    ),
                ),
        )
        assertEquals("^prc_[0-9a-f]{32}$", current.at("/properties/principleId/pattern").stringValue())
        assertEquals(8, current.at("/properties/rules/maxItems").intValue())

        val rule = document.at("/components/schemas/PrincipleRule")
        assertEquals(8, rule.path("oneOf").size())
        assertTrue(
            rule
                .path("required")
                .values()
                .map { it.stringValue() }
                .toSet()
                .containsAll(
                    setOf(
                        "ruleId",
                        "ruleType",
                        "metric",
                        "operator",
                        "threshold",
                        "severity",
                        "enabled",
                        "evidenceRequirement",
                    ),
                ),
        )
    }

    @Test
    fun `openapi locks S2_4 idempotency header and fail closed error envelope`() {
        val document =
            objectMapper.readTree(
                mockMvc
                    .get("/v3/api-docs")
                    .andExpect {
                        status { isOk() }
                    }.andReturn()
                    .response
                    .contentAsByteArray,
            )
        val post = document.at("/paths/~1api~1v1~1risk~1kill-switch/post")
        val header = post.at("/parameters/0/schema")
        assertEquals(16, header.path("minLength").intValue())
        assertEquals(128, header.path("maxLength").intValue())
        assertEquals("^[A-Za-z0-9._:-]+\$", header.path("pattern").stringValue())
        listOf("400", "401", "403", "409", "503").forEach { status ->
            assertEquals(
                "#/components/schemas/S24RiskErrorResponse",
                post.at("/responses/$status/content/application~1json/schema/\$ref").stringValue(),
            )
        }

        val error = document.at("/components/schemas/S24RiskErrorResponse")
        assertEquals(false, error.at("/properties/success/const").booleanValue())
        assertEquals("null", error.at("/properties/data/type").stringValue())
        assertEquals(
            setOf("success", "requestId", "data", "warnings", "error"),
            error
                .path("required")
                .values()
                .map { it.stringValue() }
                .toSet(),
        )
    }

    @Test
    fun `openapi locks S3_2 paper routes digest and exact request response schemas`() {
        val document =
            objectMapper.readTree(
                mockMvc
                    .get("/v3/api-docs")
                    .andExpect {
                        status { isOk() }
                    }.andReturn()
                    .response
                    .contentAsByteArray,
            )
        val repositoryRoot = findRepositoryRoot()
        val catalogBytes =
            Files.readAllBytes(
                repositoryRoot.resolve("contracts/catalogs/s3-2-internal-paper-contract.v1.json"),
            )
        val expectedDigest =
            HexFormat
                .of()
                .formatHex(MessageDigest.getInstance("SHA-256").digest(catalogBytes))

        assertEquals("s3-2-internal-paper-contract/v1", document.path("x-s3-2-contract-id").stringValue())
        assertEquals(expectedDigest, document.path("x-s3-2-contract-sha256").stringValue())
        val paperPaths =
            document
                .path("paths")
                .propertyNames()
                .asSequence()
                .filter { "/api/v1/brokerage/paper/" in it }
                .toSet()
        assertEquals(
            setOf(
                "/api/v1/brokerage/paper/orders",
                "/api/v1/brokerage/paper/accounts/{accountId}/balances",
                "/api/v1/brokerage/paper/accounts/{accountId}/buyable",
            ),
            paperPaths,
        )
        assertEquals(
            "#/components/schemas/S32PaperOrderRequest",
            document
                .at(
                    "/paths/~1api~1v1~1brokerage~1paper~1orders/post/requestBody/content/application~1json/schema/\$ref",
                ).stringValue(),
        )
        assertEquals(
            "#/components/schemas/S32PaperOrderSuccessResponse",
            document
                .at(
                    "/paths/~1api~1v1~1brokerage~1paper~1orders/post/responses/200/content/application~1json/schema/\$ref",
                ).stringValue(),
        )
        assertEquals(
            "#/components/schemas/S32OrderDetailSuccessResponse",
            document
                .at(
                    "/paths/~1api~1v1~1brokerage~1orders~1{orderId}/get/responses/200/content/application~1json/schema/\$ref",
                ).stringValue(),
        )
        assertEquals(false, document.at("/components/schemas/S32PaperOrderRequest/additionalProperties").booleanValue())
        assertEquals(
            setOf("decisionId", "orderIntent", "userAcknowledgement"),
            document
                .at("/components/schemas/S32PaperOrderRequest/required")
                .values()
                .map { it.stringValue() }
                .toSet(),
        )
        assertTrue(document.at("/paths/~1api~1v1~1brokerage~1paper~1accounts~1{accountId}/post").isMissingNode)
        assertTrue(document.at("/paths/~1api~1v1~1brokerage~1paper~1accounts~1{accountId}/delete").isMissingNode)
    }

    private fun findRepositoryRoot(): Path {
        var current = Path.of(System.getProperty("user.dir")).toAbsolutePath()
        while (!Files.exists(current.resolve("AGENTS.md"))) {
            current = current.parent ?: error("repository root was not found")
        }
        return current
    }
}
