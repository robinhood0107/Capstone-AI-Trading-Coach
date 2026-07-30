package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.http.MediaType
import org.springframework.jdbc.BadSqlGrammarException
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.jdbc.datasource.DriverManagerDataSource
import org.springframework.security.test.web.servlet.setup.SecurityMockMvcConfigurers.springSecurity
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.MvcResult
import org.springframework.test.web.servlet.get
import org.springframework.test.web.servlet.post
import org.springframework.test.web.servlet.setup.DefaultMockMvcBuilder
import org.springframework.test.web.servlet.setup.MockMvcBuilders
import org.springframework.web.context.WebApplicationContext
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.net.URI
import javax.sql.DataSource

// S4.1 RAG sources API는 source registry metadata만 인증 subject에게 열고 DB raw/ledger 권한은 닫아 둔다.
@Testcontainers
@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
class RagSourceApiIntegrationTest(
    @Autowired private val webApplicationContext: WebApplicationContext,
    @Autowired private val objectMapper: ObjectMapper,
    @Autowired private val applicationDataSource: DataSource,
) : SpringApiIntegrationTestBase() {
    private lateinit var mockMvc: MockMvc
    private val ownerJdbc: JdbcTemplate by lazy {
        JdbcTemplate(
            DriverManagerDataSource(
                postgres.jdbcUrl,
                postgres.username,
                postgres.password,
            ),
        )
    }
    private val appJdbc: JdbcTemplate by lazy { JdbcTemplate(applicationDataSource) }

    @BeforeEach
    fun setUp() {
        ownerJdbc.update("delete from rag_source_checks")
        ownerJdbc.update("delete from rag_source_revisions")
        ownerJdbc.update("delete from rag_sources")
        mockMvc =
            MockMvcBuilders
                .webAppContextSetup(webApplicationContext)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    @Test
    fun `RAG sources requires authentication and rejects ad hoc filters`() {
        mockMvc
            .get("/api/v1/rag/sources") {
                header("X-Request-Id", "req-rag-sources-unauthorized")
            }.andExpect {
                status { isUnauthorized() }
                jsonPath("$.error.code") { value("UNAUTHORIZED") }
            }

        val token = login("demo-user", userPassword())
        val rejected =
            mockMvc
                .get("/api/v1/rag/sources?sourceTier=OFFICIAL") {
                    bearer(token)
                    header("X-Request-Id", "req-rag-sources-query")
                }.andExpect {
                    status { isBadRequest() }
                    jsonPath("$.error.code") { value("VALIDATION_ERROR") }
                }.andReturn()
        assertEquals("/query/sourceTier", json(rejected).at("/error/details/violations/0/field").stringValue())
    }

    @Test
    fun `RAG sources returns bounded metadata through definer function without table privilege`() {
        insertSource("src_kis_openapi_overview_001", "KIS OpenAPI 개요", "https://apiportal.koreainvestment.com/apiservice")
        insertSource("src_opendart_disclosure_search_001", "OpenDART 공시검색", "https://opendart.fss.or.kr/api/list.json")
        insertSource(
            sourceId = "src_krx_openapi_service_catalog_001",
            title = "KRX OpenAPI 서비스 목록",
            canonicalUrl = "https://data.krx.co.kr/contents/OPN/99/OPN99000001.jspx",
            retired = true,
        )
        insertRevisionAndCheck(
            sourceId = "src_kis_openapi_overview_001",
            revisionId = "src_rev_11111111111111111111111111111111",
            checkId = "src_chk_11111111111111111111111111111111",
            canonicalUrl = "https://apiportal.koreainvestment.com/apiservice",
        )

        val token = login("demo-user", userPassword())
        val response =
            mockMvc
                .get("/api/v1/rag/sources") {
                    bearer(token)
                    header("X-Request-Id", "req-rag-sources-list")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.success") { value(true) }
                    jsonPath("$.requestId") { value("req-rag-sources-list") }
                    jsonPath("$.data.items.length()") { value(2) }
                    jsonPath("$.data.items[0].sourceId") { value("src_kis_openapi_overview_001") }
                    jsonPath("$.data.items[0].licenseDecision") { value("REFERENCE_ONLY_NO_EXTERNAL_PROCESSING") }
                    jsonPath("$.data.items[0].externalProcessingAllowed") { value(false) }
                    jsonPath("$.data.items[0].retentionOwner") { value("python-rag-corpus-privacy") }
                    jsonPath("$.data.items[0].latestCheckResult") { value("UNCHANGED") }
                    jsonPath("$.data.items[1].sourceId") { value("src_opendart_disclosure_search_001") }
                    jsonPath("$.warnings") { isEmpty() }
                    jsonPath("$.error") { doesNotExist() }
                }.andReturn()

        val item = json(response).at("/data/items/0")
        assertFalse(item.has("contentHash"))
        assertFalse(item.has("peerIpHash"))
        assertFalse(item.has("sourceRevisionId"))
        assertFalse(item.has("raw"))
        assertThrows(BadSqlGrammarException::class.java) {
            appJdbc.queryForObject("select count(*) from rag_sources", Int::class.java)
        }
        assertThrows(RuntimeException::class.java) {
            appJdbc.queryForObject(
                "select count(*) from read_rag_source_registry('usr_demo_user')",
                Int::class.java,
            )
        }
    }

    private fun insertSource(
        sourceId: String,
        title: String,
        canonicalUrl: String,
        retired: Boolean = false,
    ) {
        val (allowedOrigin, allowedPath) = splitLocator(canonicalUrl)
        ownerJdbc.update(
            """
            insert into rag_sources (
              source_id, title, source_type, tier, url, license_note, access_level, ingest_status,
              registry_version, source_owner, license_decision, external_processing_allowed,
              initial_processing, retention_mode, retention_days, retention_owner,
              canonical_url, allowed_origin, allowed_path, retired_at
            )
            values (
              ?, ?, 'UPSTREAM_REFERENCE', 'OFFICIAL', ?, '공식 reference metadata only',
              'PUBLIC', 'REGISTERED', 's4-rag-source-registry-v1', 'python-rag-corpus-privacy',
              'REFERENCE_ONLY_NO_EXTERNAL_PROCESSING', false, 'REFERENCE_ONLY',
              'REFERENCE_METADATA_ONLY', 365, 'python-rag-corpus-privacy',
              ?, ?, ?, case when ? then now() else null end
            )
            """.trimIndent(),
            sourceId,
            title,
            canonicalUrl,
            canonicalUrl,
            allowedOrigin,
            allowedPath,
            retired,
        )
    }

    private fun insertRevisionAndCheck(
        sourceId: String,
        revisionId: String,
        checkId: String,
        canonicalUrl: String,
    ) {
        val (allowedOrigin, allowedPath) = splitLocator(canonicalUrl)
        ownerJdbc.update(
            """
            insert into rag_source_revisions (
              source_revision_id, source_id, revision_seq, registry_version,
              canonical_url, allowed_origin, allowed_path, locator_sha256, metadata_hash, content_hash
            )
            values (?, ?, 1, 's4-rag-source-registry-v1', ?, ?, ?, repeat('a', 64), repeat('b', 64), repeat('c', 64))
            """.trimIndent(),
            revisionId,
            sourceId,
            canonicalUrl,
            allowedOrigin,
            allowedPath,
        )
        ownerJdbc.update(
            """
            insert into rag_source_checks (
              source_check_id, source_id, source_revision_id, check_result,
              content_hash, response_status, bytes_read, peer_ip_hash
            )
            values (?, ?, ?, 'UNCHANGED', repeat('c', 64), 200, 1024, repeat('d', 64))
            """.trimIndent(),
            checkId,
            sourceId,
            revisionId,
        )
    }

    private fun splitLocator(canonicalUrl: String): Pair<String, String> {
        val uri = URI.create(canonicalUrl)
        val origin = "${uri.scheme}://${uri.host}"
        val path = uri.rawPath.ifBlank { "/" } + uri.rawQuery?.let { "?$it" }.orEmpty()
        return origin to path
    }

    private fun login(
        username: String,
        password: String,
    ): String {
        val response =
            mockMvc
                .post("/api/v1/auth/login") {
                    contentType = MediaType.APPLICATION_JSON
                    content = objectMapper.writeValueAsString(mapOf("username" to username, "password" to password))
                    header("X-Request-Id", "req-rag-login-$username")
                }.andExpect {
                    status { isOk() }
                }.andReturn()
        return json(response).at("/data/accessToken").stringValue()
    }

    private fun json(result: MvcResult): JsonNode = objectMapper.readTree(result.response.contentAsString)

    private fun org.springframework.test.web.servlet.MockHttpServletRequestDsl.bearer(token: String) {
        header("Authorization", "Bearer $token")
    }

    companion object {
        private const val APP_PASSWORD = "app-test"
        private const val FLYWAY_PASSWORD = "flyway-test"
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            PostgreSQLContainer(postgresImage)
                .withDatabaseName("decision_rag_sources")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")

        @DynamicPropertySource
        @JvmStatic
        fun postgresProperties(registry: DynamicPropertyRegistry) {
            registry.add("spring.datasource.url", postgres::getJdbcUrl)
            registry.add("spring.datasource.username") { "decision_app" }
            registry.add("spring.datasource.password") { APP_PASSWORD }
            registry.add("spring.flyway.user") { "flyway" }
            registry.add("spring.flyway.password") { FLYWAY_PASSWORD }
        }
    }
}
