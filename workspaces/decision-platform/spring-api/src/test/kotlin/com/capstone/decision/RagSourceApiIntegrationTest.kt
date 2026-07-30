package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.dao.DataAccessException
import org.springframework.dao.DataIntegrityViolationException
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
    private val queryJdbc: JdbcTemplate by lazy {
        JdbcTemplate(
            DriverManagerDataSource(
                postgres.jdbcUrl,
                "decision_rag_query",
                QUERY_PASSWORD,
            ),
        )
    }

    @BeforeEach
    fun setUp() {
        ownerJdbc.update("delete from rag_embedding_policy_transitions")
        ownerJdbc.update("delete from rag_embedding_policy_state")
        ownerJdbc.update("delete from rag_chunk_embeddings")
        ownerJdbc.update("delete from rag_generation_chunks")
        ownerJdbc.update("delete from rag_corpus_generations")
        ownerJdbc.update("delete from rag_chunk_revisions")
        ownerJdbc.update("delete from rag_ingest_runs")
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
    fun `RAG source validation bounds oversized attacker controlled query names`() {
        val token = login("demo-user", userPassword())
        val longName = "x".repeat(600)

        val rejected =
            mockMvc
                .get("/api/v1/rag/sources") {
                    bearer(token)
                    queryParam(longName, "1")
                    header("X-Request-Id", "req-rag-sources-long-query")
                }.andExpect {
                    status { isBadRequest() }
                    jsonPath("$.error.code") { value("VALIDATION_ERROR") }
                }.andReturn()

        val field = json(rejected).at("/error/details/violations/0/field").stringValue()
        assertTrue(field.matches(Regex("^/query/__name_sha256_[0-9a-f]{64}$")))
        assertTrue(field.length <= 512)
    }

    @Test
    fun `RAG source validation escapes sorts and caps JSON Pointer violations`() {
        val token = login("demo-user", userPassword())
        val escaped =
            mockMvc
                .get("/api/v1/rag/sources") {
                    bearer(token)
                    queryParam("z~key", "1")
                    queryParam("a/key", "1")
                    header("X-Request-Id", "req-rag-sources-pointer")
                }.andExpect {
                    status { isBadRequest() }
                    jsonPath("$.error.code") { value("VALIDATION_ERROR") }
                }.andReturn()
        val escapedViolations = json(escaped).at("/error/details/violations")
        assertEquals(2, escapedViolations.size())
        assertEquals("/query/a~1key", escapedViolations[0].path("field").stringValue())
        assertEquals("/query/z~0key", escapedViolations[1].path("field").stringValue())

        val capped =
            mockMvc
                .get("/api/v1/rag/sources") {
                    bearer(token)
                    (0 until 70).forEach { index ->
                        queryParam("q${index.toString().padStart(3, '0')}", "1")
                    }
                    header("X-Request-Id", "req-rag-sources-pointer-cap")
                }.andExpect {
                    status { isBadRequest() }
                    jsonPath("$.error.code") { value("VALIDATION_ERROR") }
                }.andReturn()
        val cappedViolations = json(capped).at("/error/details/violations")
        assertEquals(64, cappedViolations.size())
        assertEquals("/query/q000", cappedViolations[0].path("field").stringValue())
        assertEquals("/query/q063", cappedViolations[63].path("field").stringValue())
    }

    @Test
    fun `RAG sources returns an empty bounded list before active generation`() {
        val token = login("demo-user", userPassword())

        mockMvc
            .get("/api/v1/rag/sources") {
                bearer(token)
                header("X-Request-Id", "req-rag-sources-empty")
            }.andExpect {
                status { isOk() }
                jsonPath("$.success") { value(true) }
                jsonPath("$.requestId") { value("req-rag-sources-empty") }
                jsonPath("$.data.items.length()") { value(0) }
                jsonPath("$.warnings") { isEmpty() }
                jsonPath("$.error") { doesNotExist() }
            }
    }

    @Test
    fun `RAG sources sanitizes storage failures as a bounded unavailable envelope`() {
        val token = login("demo-user", userPassword())
        ownerJdbc.execute("revoke execute on function read_rag_source_registry(text) from decision_app")
        try {
            val response =
                mockMvc
                    .get("/api/v1/rag/sources") {
                        bearer(token)
                        header("X-Request-Id", "req-rag-sources-unavailable")
                    }.andExpect {
                        status { isServiceUnavailable() }
                        jsonPath("$.success") { value(false) }
                        jsonPath("$.requestId") { value("req-rag-sources-unavailable") }
                        jsonPath("$.data") { doesNotExist() }
                        jsonPath("$.warnings") { isEmpty() }
                        jsonPath("$.error.code") { value("RAG_UNAVAILABLE") }
                        jsonPath("$.error.message") { value("RAG source registry is unavailable.") }
                        jsonPath("$.error.details") { isEmpty() }
                    }.andReturn()
            val body = response.response.contentAsString.lowercase()
            assertFalse("permission" in body)
            assertFalse("jdbc" in body)
            assertFalse("read_rag_source_registry" in body)
        } finally {
            ownerJdbc.execute("grant execute on function read_rag_source_registry(text) to decision_app")
        }
    }

    @Test
    fun `RAG sources returns exact seven active project card fields and excludes upstream rows`() {
        val firstCard =
            insertSourceRevisionAndChunk(
                sourceId = "src_project_gold_futures_etf_132030_001",
                sourceType = "PROJECT_SOURCE_CARD",
                institution = "samsungfund",
                topic = "gold_futures_etf_132030",
                title = "132030 금선물 ETF 구조",
                canonicalUrl = "https://www.samsungfund.com/etf/product/view.do?id=2ETF24",
                ordinal = 1,
            )
        val secondCard =
            insertSourceRevisionAndChunk(
                sourceId = "src_project_kis_adjusted_price_001",
                sourceType = "PROJECT_SOURCE_CARD",
                institution = "kis",
                topic = "adjusted_price",
                title = "KIS 조정주가 provenance",
                canonicalUrl = "https://github.com/koreainvestment/open-trading-api/blob/example/daily.py",
                ordinal = 2,
                includeCheck = false,
            )
        insertSourceRevisionAndChunk(
            sourceId = "src_kis_openapi_overview_001",
            sourceType = "UPSTREAM_REFERENCE",
            institution = "kis",
            topic = "openapi_overview",
            title = "KIS OpenAPI 개요",
            canonicalUrl = "https://apiportal.koreainvestment.com/about-open-api",
            ordinal = 3,
            materialize = false,
        )
        activateGeneration(
            listOf(
                firstCard.chunkRevisionId,
                secondCard.chunkRevisionId,
            ),
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
                    jsonPath("$.data.items[0].sourceId") { value("src_project_gold_futures_etf_132030_001") }
                    jsonPath("$.data.items[0].institution") { value("samsungfund") }
                    jsonPath("$.data.items[0].topic") { value("gold_futures_etf_132030") }
                    jsonPath("$.data.items[0].attribution") { value("공식 합성 fixture attribution") }
                    jsonPath("$.data.items[1].sourceId") { value("src_project_kis_adjusted_price_001") }
                    jsonPath("$.warnings") { isEmpty() }
                    jsonPath("$.error") { doesNotExist() }
                }.andReturn()

        val item = json(response).at("/data/items/0")
        assertEquals(
            setOf(
                "sourceId",
                "title",
                "institution",
                "topic",
                "attribution",
                "canonicalUrl",
                "lastCheckedAt",
            ),
            item.propertyNames().asSequence().toSet(),
        )
        assertFalse(item.has("contentHash"))
        assertFalse(item.has("peerIpHash"))
        assertFalse(item.has("sourceRevisionId"))
        assertFalse(item.has("licenseDecision"))
        assertFalse(item.has("retentionOwner"))
        assertFalse(item.has("sourceType"))
        assertFalse(item.has("raw"))
        val uncheckedItem = json(response).at("/data/items/1")
        assertTrue(uncheckedItem.has("lastCheckedAt"))
        assertTrue(uncheckedItem.path("lastCheckedAt").isNull)
        assertThrows(BadSqlGrammarException::class.java) {
            appJdbc.queryForObject("select count(*) from rag_sources", Int::class.java)
        }
        assertThrows(RuntimeException::class.java) {
            appJdbc.queryForObject(
                "select count(*) from read_rag_source_registry('usr_demo_user')",
                Int::class.java,
            )
        }
        applicationDataSource.connection.use { connection ->
            connection.createStatement().use { statement ->
                statement
                    .executeQuery(
                        """
                        select
                          coalesce(current_setting('app.actor_user_id', true), ''),
                          current_setting('statement_timeout'),
                          current_setting('lock_timeout'),
                          current_setting('idle_in_transaction_session_timeout')
                        """.trimIndent(),
                    ).use { result ->
                        result.next()
                        assertEquals("", result.getString(1))
                        assertEquals("2s", result.getString(2))
                        assertEquals("500ms", result.getString(3))
                        assertEquals("5s", result.getString(4))
                    }
            }
        }
    }

    @Test
    fun `RAG sources excludes internal and retired cards and caps the public projection at thirty`() {
        val publicChunks =
            (1..31).map { ordinal ->
                insertSourceRevisionAndChunk(
                    sourceId = "src_project_limit_card_${ordinal.toString().padStart(3, '0')}",
                    sourceType = "PROJECT_SOURCE_CARD",
                    institution = "project",
                    topic = "limit_card",
                    title = "bounded public card $ordinal",
                    canonicalUrl = "https://example.com/limit-card/$ordinal",
                    ordinal = ordinal,
                )
            }
        val internal =
            insertSourceRevisionAndChunk(
                sourceId = "src_project_limit_internal_001",
                sourceType = "PROJECT_SOURCE_CARD",
                institution = "project",
                topic = "limit_internal",
                title = "bounded internal card",
                canonicalUrl = "https://example.com/limit-internal",
                ordinal = 32,
                accessLevel = "INTERNAL",
            )
        val retired =
            insertSourceRevisionAndChunk(
                sourceId = "src_project_limit_retired_001",
                sourceType = "PROJECT_SOURCE_CARD",
                institution = "project",
                topic = "limit_retired",
                title = "bounded retired card",
                canonicalUrl = "https://example.com/limit-retired",
                ordinal = 33,
            )
        ownerJdbc.update(
            "update rag_sources set retired_at = now() where source_id = 'src_project_limit_retired_001'",
        )
        activateGeneration(
            publicChunks.map { it.chunkRevisionId } +
                internal.chunkRevisionId +
                retired.chunkRevisionId,
        )

        val token = login("demo-user", userPassword())
        val response =
            mockMvc
                .get("/api/v1/rag/sources") {
                    bearer(token)
                    header("X-Request-Id", "req-rag-sources-bounded")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.data.items.length()") { value(30) }
                }.andReturn()
        val sourceIds =
            json(response)
                .at("/data/items")
                .values()
                .map { item -> item.path("sourceId").stringValue() }
        assertEquals(
            (1..30).map { ordinal ->
                "src_project_limit_card_${ordinal.toString().padStart(3, '0')}"
            },
            sourceIds,
        )
        assertFalse("src_project_limit_card_031" in sourceIds)
        assertFalse("src_project_limit_internal_001" in sourceIds)
        assertFalse("src_project_limit_retired_001" in sourceIds)
    }

    @Test
    fun `RAG query role reads only ordered active public chunks through bounded function`() {
        val first =
            insertSourceRevisionAndChunk(
                sourceId = "src_project_query_risk_001",
                sourceType = "PROJECT_SOURCE_CARD",
                institution = "project",
                topic = "risk",
                title = "risk first",
                canonicalUrl = "https://example.com/query-risk-first",
                ordinal = 41,
            )
        val second =
            insertSourceRevisionAndChunk(
                sourceId = "src_project_query_risk_002",
                sourceType = "PROJECT_SOURCE_CARD",
                institution = "project",
                topic = "risk",
                title = "risk second",
                canonicalUrl = "https://example.com/query-risk-second",
                ordinal = 42,
            )
        val otherTopic =
            insertSourceRevisionAndChunk(
                sourceId = "src_project_query_api_001",
                sourceType = "PROJECT_SOURCE_CARD",
                institution = "project",
                topic = "api",
                title = "api card",
                canonicalUrl = "https://example.com/query-api",
                ordinal = 43,
            )
        activateGeneration(
            listOf(
                second.chunkRevisionId,
                otherTopic.chunkRevisionId,
                first.chunkRevisionId,
            ),
        )

        val rows =
            queryJdbc.queryForList(
                """
                select chunk_revision_id, source_id, title, heading_path,
                       canonical_content, canonical_content_hash
                from read_active_rag_chunks(?, ?)
                """.trimIndent(),
                "risk",
                30,
            )
        assertEquals(2, rows.size)
        assertEquals(second.chunkRevisionId, rows[0]["chunk_revision_id"])
        assertEquals("src_project_query_risk_002", rows[0]["source_id"])
        assertEquals(first.chunkRevisionId, rows[1]["chunk_revision_id"])
        assertEquals("src_project_query_risk_001", rows[1]["source_id"])
        assertEquals(6, rows[0].keys.size)

        listOf(
            "" to 1,
            "x".repeat(129) to 1,
            "risk" to 0,
            "risk" to 31,
        ).forEach { (topic, limit) ->
            assertThrows(DataAccessException::class.java) {
                queryJdbc.queryForList(
                    "select * from read_active_rag_chunks(?, ?)",
                    topic,
                    limit,
                )
            }
        }
        assertThrows(DataAccessException::class.java) {
            queryJdbc.queryForObject("select count(*) from rag_chunk_revisions", Int::class.java)
        }
    }

    private fun insertSourceRevisionAndChunk(
        sourceId: String,
        sourceType: String,
        institution: String,
        topic: String,
        title: String,
        canonicalUrl: String,
        ordinal: Int,
        materialize: Boolean = true,
        includeCheck: Boolean = true,
        accessLevel: String = "PUBLIC",
    ): SourceChunkFixture {
        require(accessLevel in setOf("PUBLIC", "INTERNAL"))
        val (allowedOrigin, allowedPath) = splitLocator(canonicalUrl)
        val suffix = ordinal.toString().padStart(32, '0')
        val revisionId = "src_rev_$suffix"
        val checkId = "src_chk_$suffix"
        val ingestRunId = "rag_ing_$suffix"
        val chunkRevisionId = "rag_chk_$suffix"
        ownerJdbc.update(
            """
            insert into rag_sources (
              source_id, source_type, institution, topic, owner_identity
            )
            values (?, ?, ?, ?, 'python-rag-corpus-privacy')
            """.trimIndent(),
            sourceId,
            sourceType,
            institution,
            topic,
        )
        ownerJdbc.update(
            """
            insert into rag_source_revisions (
              source_revision_id, source_id, revision_seq, registry_version,
              title, tier, access_level, license_decision, license_note, attribution,
              retention_mode, retention_days, retention_owner, external_processing_allowed,
              initial_processing, canonical_url, allowed_origin, allowed_path,
              locator_sha256, metadata_hash
            )
            values (
              ?, ?, 1, 's4-rag-source-registry-v1',
              ?, case when ? = 'PROJECT_SOURCE_CARD' then 'PROJECT' else 'OFFICIAL' end,
              ?,
              case when ? = 'PROJECT_SOURCE_CARD'
                then case when ? = 'PUBLIC' then 'PROJECT_AUTHORED_PUBLIC' else 'PROJECT_AUTHORED_INTERNAL' end
                else 'REFERENCE_ONLY_NO_EXTERNAL_PROCESSING'
              end,
              '공식 합성 fixture license note', '공식 합성 fixture attribution',
              case when ? = 'PROJECT_SOURCE_CARD' then 'PROJECT_CARD' else 'REFERENCE_METADATA_ONLY' end,
              365, 'python-rag-corpus-privacy', false,
              case when ? = 'PROJECT_SOURCE_CARD' then 'PROJECT_AUTHORED_CARD' else 'REFERENCE_ONLY' end,
              ?, ?, ?, repeat('a', 64), repeat(?, 64)
            )
            """.trimIndent(),
            revisionId,
            sourceId,
            title,
            sourceType,
            accessLevel,
            sourceType,
            accessLevel,
            sourceType,
            sourceType,
            canonicalUrl,
            allowedOrigin,
            allowedPath,
            hexDigit(ordinal),
        )
        if (includeCheck) {
            ownerJdbc.update(
                """
                insert into rag_source_checks (
                  source_check_id, source_id, source_revision_id, check_result,
                  content_hash, response_status, bytes_read, peer_ip_fingerprint
                )
                values (?, ?, ?, 'UNCHANGED', repeat('c', 64), 200, 1024, repeat('d', 64))
                """.trimIndent(),
                checkId,
                sourceId,
                revisionId,
            )
        }
        if (!materialize) {
            return SourceChunkFixture(chunkRevisionId)
        }
        require(sourceType == "PROJECT_SOURCE_CARD") {
            "Only project source cards can be materialized into the RAG corpus."
        }
        ownerJdbc.update(
            """
            insert into rag_ingest_runs (
              ingest_run_id, source_revision_id, parser_version, canonicalizer_version,
              card_schema_version, input_content_hash, status, expected_chunk_count
            )
            values (
              ?, ?, 'fixture-parser-v1', 'fixture-canonical-v1', 'rag-source-card-v1',
              repeat('e', 64), 'PLANNED', 1
            )
            """.trimIndent(),
            ingestRunId,
            revisionId,
        )
        ownerJdbc.update(
            """
            update rag_ingest_runs
            set status = 'RUNNING', started_at = now()
            where ingest_run_id = ?
            """.trimIndent(),
            ingestRunId,
        )
        ownerJdbc.update(
            """
            insert into rag_chunk_revisions (
              chunk_revision_id, ingest_run_id, source_revision_id, chunk_seq, heading_path,
              canonical_content, canonical_content_hash, token_count, topic, access_level, tier
            )
            values (?, ?, ?, 1, array['핵심 claim'], ?, repeat(?, 64), 400, ?, ?, ?)
            """.trimIndent(),
            chunkRevisionId,
            ingestRunId,
            revisionId,
            "$title 합성 fixture",
            hexDigit(ordinal + 7),
            topic,
            accessLevel,
            if (sourceType == "PROJECT_SOURCE_CARD") "PROJECT" else "OFFICIAL",
        )
        ownerJdbc.update(
            """
            update rag_ingest_runs
            set status = 'SUCCEEDED', actual_chunk_count = 1, completed_at = now()
            where ingest_run_id = ?
            """.trimIndent(),
            ingestRunId,
        )
        return SourceChunkFixture(chunkRevisionId)
    }

    private fun activateGeneration(chunkRevisionIds: List<String>) {
        val generationId = "rag_gen_11111111111111111111111111111111"
        ownerJdbc.update(
            """
            insert into rag_corpus_generations (
              corpus_generation_id, corpus_hash, embedding_profile_id, vector_space,
              status, expected_chunk_count
            )
            values (
              ?, repeat('1', 64), 'bge_m3_local_1024_v1', 'bge_m3_local_1024_v1',
              'REGISTERED', ?
            )
            """.trimIndent(),
            generationId,
            chunkRevisionIds.size,
        )
        ownerJdbc.update(
            "update rag_corpus_generations set status = 'PLANNED' where corpus_generation_id = ?",
            generationId,
        )
        ownerJdbc.update(
            "update rag_corpus_generations set status = 'MATERIALIZING' where corpus_generation_id = ?",
            generationId,
        )
        chunkRevisionIds.forEachIndexed { index, chunkRevisionId ->
            ownerJdbc.update(
                """
                insert into rag_generation_chunks (
                  corpus_generation_id, chunk_revision_id, embedding_profile_id,
                  embedding_input_hash, context_set_hash, ordinal
                )
                values (?, ?, 'bge_m3_local_1024_v1', repeat('f', 64), null, ?)
                """.trimIndent(),
                generationId,
                chunkRevisionId,
                index + 1,
            )
        }
        assertThrows(DataIntegrityViolationException::class.java) {
            ownerJdbc.update(
                """
                update rag_corpus_generations
                set status = 'MATERIALIZED', actual_chunk_count = ?
                where corpus_generation_id = ?
                """.trimIndent(),
                chunkRevisionIds.size,
                generationId,
            )
        }
        chunkRevisionIds.forEachIndexed { index, chunkRevisionId ->
            ownerJdbc.update(
                """
                insert into rag_chunk_embeddings (
                  chunk_embedding_id, corpus_generation_id, chunk_revision_id,
                  embedding_profile_id, vector_space, embedding_input_hash,
                  context_set_hash, embedding
                )
                values (
                  ?, ?, ?, 'bge_m3_local_1024_v1', 'bge_m3_local_1024_v1',
                  repeat('f', 64), null, ?::vector
                )
                """.trimIndent(),
                "rag_emb_${(index + 1).toString().padStart(32, '0')}",
                generationId,
                chunkRevisionId,
                normalizedFixtureVector(),
            )
        }
        ownerJdbc.update(
            """
            update rag_corpus_generations
            set status = 'MATERIALIZED', actual_chunk_count = ?
            where corpus_generation_id = ?
            """.trimIndent(),
            chunkRevisionIds.size,
            generationId,
        )
        ownerJdbc.update(
            """
            update rag_corpus_generations
            set status = 'EVAL_PASSED', evaluation_status = 'PASSED', evaluated_at = now()
            where corpus_generation_id = ?
            """.trimIndent(),
            generationId,
        )
        ownerJdbc.update(
            """
            update rag_corpus_generations
            set status = 'ACTIVE', activated_at = now()
            where corpus_generation_id = ?
            """.trimIndent(),
            generationId,
        )
        assertThrows(DataIntegrityViolationException::class.java) {
            ownerJdbc.update(
                """
                insert into rag_generation_chunks (
                  corpus_generation_id, chunk_revision_id, embedding_profile_id,
                  embedding_input_hash, context_set_hash, ordinal
                )
                values (?, ?, 'bge_m3_local_1024_v1', repeat('f', 64), null, 9999)
                """.trimIndent(),
                generationId,
                chunkRevisionIds.first(),
            )
        }
        ownerJdbc.update(
            """
            insert into rag_embedding_policy_state (
              state_id, policy_id, effective_profile_id, active_generation_id,
              version, changed_at, changed_by_audit_ref
            )
            values (
              'default', 'bge_only_v1', 'bge_m3_local_1024_v1', ?,
              1, now(), 'audit_fixture_0000000000000001'
            )
            """.trimIndent(),
            generationId,
        )
    }

    private fun splitLocator(canonicalUrl: String): Pair<String, String> {
        val uri = URI.create(canonicalUrl)
        val origin = "${uri.scheme}://${uri.host}"
        val path = uri.rawPath.ifBlank { "/" } + uri.rawQuery?.let { "?$it" }.orEmpty()
        return origin to path
    }

    private fun normalizedFixtureVector(): String =
        buildString {
            append("[1")
            repeat(1023) { append(",0") }
            append("]")
        }

    private fun hexDigit(value: Int): String = "0123456789abcdef"[Math.floorMod(value, 16)].toString()

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

    private data class SourceChunkFixture(
        val chunkRevisionId: String,
    )

    companion object {
        private const val APP_PASSWORD = "app-test"
        private const val FLYWAY_PASSWORD = "flyway-test"
        private const val QUERY_PASSWORD = "rag-query-test"
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
