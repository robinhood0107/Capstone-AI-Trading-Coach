package com.capstone.decision

import com.capstone.decision.application.async.AsyncJobRequest
import com.capstone.decision.application.async.AsyncJobType
import com.capstone.decision.application.async.AsyncPipelinePort
import com.capstone.decision.infrastructure.async.DbAsyncDispatcher
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.http.MediaType
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
import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.TimeUnit
import javax.sql.DataSource

@Testcontainers
@SpringBootTest(
    properties = [
        "spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration",
    ],
)
class AsyncJobApiIntegrationTest(
    @Autowired private val context: WebApplicationContext,
    @Autowired private val objectMapper: ObjectMapper,
    @Autowired private val applicationDataSource: DataSource,
    @Autowired private val asyncPipelinePort: AsyncPipelinePort,
    @Autowired private val dbAsyncDispatcher: DbAsyncDispatcher,
) : SpringApiIntegrationTestBase() {
    private lateinit var mockMvc: MockMvc
    private val appJdbc by lazy { JdbcTemplate(applicationDataSource) }
    private val ownerJdbc by lazy {
        JdbcTemplate(DriverManagerDataSource(postgres.jdbcUrl, postgres.username, postgres.password))
    }

    @BeforeEach
    fun setUp() {
        check(appJdbc.queryForObject("select current_user", String::class.java) == "decision_app")
        check(
            ownerJdbc.queryForObject(
                "select has_table_privilege('decision_app','public.users','SELECT')",
                Boolean::class.java,
            ) == true,
        ) { "decision_app users SELECT grant missing" }
        ownerJdbc.update("delete from event_outbox")
        ownerJdbc.update("delete from async_job")
        ownerJdbc.update("update users set role='ADMIN',status='ACTIVE',security_version=1 where user_id='usr_demo_admin'")
        mockMvc =
            MockMvcBuilders
                .webAppContextSetup(context)
                .apply<DefaultMockMvcBuilder>(springSecurity())
                .build()
    }

    @Test
    fun `async status requires current ADMIN and never exposes payload or requester`() {
        seed("job_api_status_00000001", "RAG_INDEX", "usr_demo_user", "{\"sourceId\":\"src_fixture_00000001\"}")

        mockMvc.get("/api/v1/async-jobs/job_api_status_00000001").andExpect {
            status { isUnauthorized() }
        }
        val userToken = login("demo-user", userPassword())
        mockMvc
            .get("/api/v1/async-jobs/job_api_status_00000001") { bearer(userToken) }
            .andExpect {
                status { isForbidden() }
                jsonPath("$.error.code") { value("FORBIDDEN") }
            }

        val adminToken = login("demo-admin", adminPassword())
        val result =
            mockMvc
                .get("/api/v1/async-jobs/job_api_status_00000001") {
                    bearer(adminToken)
                    header("X-Request-Id", "req-async-status")
                }.andExpect {
                    status { isOk() }
                    jsonPath("$.success") { value(true) }
                    jsonPath("$.requestId") { value("req-async-status") }
                    jsonPath("$.data.jobId") { value("job_api_status_00000001") }
                    jsonPath("$.data.type") { value("RAG_INDEX") }
                    jsonPath("$.data.status") { value("REQUESTED") }
                    jsonPath("$.data.sourceId") { value("src_fixture_00000001") }
                    jsonPath("$.data.error") { doesNotExist() }
                    jsonPath("$.data.payload") { doesNotExist() }
                    jsonPath("$.data.requestedBy") { doesNotExist() }
                }.andReturn()
        val body = result.response.contentAsString
        assertFalse("usr_demo_user" in body)
        assertEquals(
            1,
            ownerJdbc.queryForObject(
                "select count(*) from async_job_admin_read_audit where job_id='job_api_status_00000001' and read_kind='DETAIL'",
                Int::class.java,
            ),
        )
    }

    @Test
    fun `async list binds cursor to ADMIN filters and rejects tampering`() {
        seed("job_api_list_00000001", "MODEL_EVAL", "usr_demo_user", "{\"runId\":\"run_fixture_00000001\"}")
        seed("job_api_list_00000002", "MODEL_EVAL", "usr_demo_user", "{\"runId\":\"run_fixture_00000002\"}")
        val token = login("demo-admin", adminPassword())
        val first =
            mockMvc
                .get("/api/v1/async-jobs?status=REQUESTED&type=MODEL_EVAL&size=1") { bearer(token) }
                .andExpect {
                    status { isOk() }
                    jsonPath("$.data.items.length()") { value(1) }
                    jsonPath("$.data.nextCursor") { isString() }
                }.andReturn()
        val cursor = json(first).at("/data/nextCursor").stringValue()
        mockMvc
            .get("/api/v1/async-jobs?status=REQUESTED&type=MODEL_EVAL&size=1&cursor=$cursor") { bearer(token) }
            .andExpect {
                status { isOk() }
                jsonPath("$.data.items.length()") { value(1) }
            }
        val replacement = if (cursor.last() == 'A') 'B' else 'A'
        val tampered = cursor.dropLast(1) + replacement
        mockMvc
            .get("/api/v1/async-jobs?status=REQUESTED&type=MODEL_EVAL&size=1&cursor=$tampered") { bearer(token) }
            .andExpect {
                status { isBadRequest() }
                jsonPath("$.error.code") { value("VALIDATION_ERROR") }
            }
        mockMvc
            .get("/api/v1/async-jobs?status=FAILED&type=MODEL_EVAL&size=1&cursor=$cursor") { bearer(token) }
            .andExpect { status { isBadRequest() } }
        mockMvc
            .get("/api/v1/async-jobs?status=REQUESTED&type=MODEL_EVAL&size=1&size=2") { bearer(token) }
            .andExpect { status { isBadRequest() } }
    }

    @Test
    fun `stale ADMIN token loses async status access after DB role change`() {
        seed("job_api_drift_00000001", "MODEL_EVAL", "usr_demo_user", "{\"runId\":\"run_fixture_00000003\"}")
        val token = login("demo-admin", adminPassword())
        ownerJdbc.update("update users set role='USER',security_version=2 where user_id='usr_demo_admin'")

        mockMvc
            .get("/api/v1/async-jobs/job_api_drift_00000001") { bearer(token) }
            .andExpect {
                status { isUnauthorized() }
                jsonPath("$.error.code") { value("UNAUTHORIZED") }
            }
    }

    @Test
    fun `DB adapter commits job and outbox together and rolls both back on outbox denial`() {
        val accepted =
            asyncPipelinePort.request(
                AsyncJobRequest(
                    type = AsyncJobType.RAG_INDEX,
                    requestedBy = "usr_demo_user",
                    references =
                        mapOf(
                            "sourceId" to "src_fixture_00000011",
                            "sourceRevisionId" to "srv_fixture_00000011",
                            "importTicketId" to "rti_" + "1".repeat(32),
                            "profileId" to "bge_m3_local_1024_v1",
                        ),
                ),
            )
        assertEquals(
            1,
            ownerJdbc.queryForObject(
                "select count(*) from async_job where job_id=?",
                Int::class.java,
                accepted.jobId,
            ),
        )
        assertEquals(
            1,
            ownerJdbc.queryForObject(
                "select count(*) from event_outbox where event_id=? and aggregate_id=?",
                Int::class.java,
                accepted.eventId,
                accepted.jobId,
            ),
        )

        ownerJdbc.execute("revoke insert on event_outbox from decision_app")
        val jobsBefore = ownerJdbc.queryForObject("select count(*) from async_job", Int::class.java)
        try {
            assertThrows(RuntimeException::class.java) {
                asyncPipelinePort.request(
                    AsyncJobRequest(
                        type = AsyncJobType.MODEL_EVAL,
                        requestedBy = "usr_demo_user",
                        references =
                            mapOf(
                                "runId" to "run_fixture_00000021",
                                "contentHash" to "sha256:" + "a".repeat(64),
                            ),
                    ),
                )
            }
        } finally {
            ownerJdbc.execute("grant insert on event_outbox to decision_app")
        }
        assertEquals(jobsBefore, ownerJdbc.queryForObject("select count(*) from async_job", Int::class.java))
    }

    @Test
    fun `DB dispatcher completes synthetic artifact through the real Python worker`() {
        withPythonWorker {
            val accepted =
                asyncPipelinePort.request(
                    AsyncJobRequest(
                        type = AsyncJobType.ARTIFACT_INGEST,
                        requestedBy = "usr_demo_user",
                        references =
                            mapOf(
                                "artifactId" to "artifact_fixture_00000001",
                                "contentHash" to "sha256:" + "b".repeat(64),
                            ),
                    ),
                )

            dbAsyncDispatcher.poll()

            assertEquals(
                "COMPLETED",
                ownerJdbc.queryForObject(
                    "select status from async_job where job_id=?",
                    String::class.java,
                    accepted.jobId,
                ),
            )
            assertEquals(
                "PUBLISHED",
                ownerJdbc.queryForObject(
                    "select status from event_outbox where event_id=?",
                    String::class.java,
                    accepted.eventId,
                ),
            )
            assertEquals(
                1,
                ownerJdbc.queryForObject(
                    "select count(*) from processed_event where event_id=? and consumer_name='python-async-worker-v1'",
                    Int::class.java,
                    accepted.eventId,
                ),
            )
            assertEquals(
                1,
                ownerJdbc.queryForObject(
                    "select count(*) from async_materialization_receipt where event_id=? and artifact_id='artifact_fixture_00000001'",
                    Int::class.java,
                    accepted.eventId,
                ),
            )
            assertEquals(
                1,
                ownerJdbc.queryForObject(
                    "select count(*) from event_outbox where aggregate_id=? and event_type='artifact.ingested.v1' and status='PENDING'",
                    Int::class.java,
                    accepted.jobId,
                ),
            )
        }
    }

    private fun withPythonWorker(block: () -> Unit) {
        val process = startPythonWorker()
        try {
            awaitLoopbackReady(process)
            block()
        } finally {
            terminateProcess(process)
        }
    }

    private fun startPythonWorker(): Process {
        val pythonServices = repositoryRoot().resolve(PYTHON_SERVICES_RELATIVE_PATH)
        check(Files.isRegularFile(pythonServices.resolve("pyproject.toml"))) {
            "S7 Python worker project is unavailable."
        }
        val dsn =
            "postgresql://decision_worker:worker-test-secret-0001@${postgres.host}:${postgres.firstMappedPort}/${postgres.databaseName}"
        val builder =
            ProcessBuilder(
                "uv",
                "run",
                "--frozen",
                "--no-sync",
                "python",
                "-m",
                "app.async_worker.grpc_server",
            ).directory(pythonServices.toFile())
                .redirectOutput(ProcessBuilder.Redirect.DISCARD)
                .redirectError(ProcessBuilder.Redirect.DISCARD)
        builder.environment().apply {
            put("PYTHONDONTWRITEBYTECODE", "1")
            put("UV_OFFLINE", "1")
            put("ASYNC_WORKER_GRPC_BIND_ADDRESS", "127.0.0.1:$asyncWorkerPort")
            put("ASYNC_WORKER_GRPC_SHARED_SECRET", TEST_ASYNC_WORKER_GRPC_SHARED_SECRET)
            put("ASYNC_WORKER_DATABASE_DSN", dsn)
            put("ASYNC_PARTITION_HMAC_KEY", TEST_ASYNC_PARTITION_HMAC_KEY)
            PROVIDER_KEYS.forEach(::remove)
        }
        return try {
            builder.start()
        } catch (exception: IOException) {
            throw AssertionError("S7 DB E2E requires the frozen uv Python runtime.", exception)
        }
    }

    private fun awaitLoopbackReady(process: Process) {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10)
        while (System.nanoTime() < deadline) {
            check(process.isAlive) { "S7 Python worker exited before loopback readiness." }
            try {
                Socket().use { it.connect(InetSocketAddress("127.0.0.1", asyncWorkerPort), 250) }
                return
            } catch (_: IOException) {
                Thread.sleep(50)
            }
        }
        throw AssertionError("S7 Python worker did not bind numeric loopback in time.")
    }

    private fun terminateProcess(process: Process) {
        val descendants = process.toHandle().descendants().use { it.toList() }
        descendants.filter { it.isAlive }.forEach { it.destroy() }
        if (process.isAlive) process.destroy()
        if (!process.waitFor(5, TimeUnit.SECONDS)) {
            descendants.filter { it.isAlive }.forEach { it.destroyForcibly() }
            process.destroyForcibly()
            check(process.waitFor(5, TimeUnit.SECONDS)) { "S7 Python worker did not terminate." }
        }
    }

    private fun seed(
        jobId: String,
        type: String,
        requestedBy: String,
        payload: String,
    ) {
        assertEquals(
            true,
            appJdbc.queryForObject(
                "select create_async_job(?,?,?,?::jsonb)",
                Boolean::class.java,
                jobId,
                type,
                requestedBy,
                payload,
            ),
        )
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
                }.andExpect { status { isOk() } }
                .andReturn()
        return json(response).at("/data/accessToken").stringValue()
    }

    private fun json(result: MvcResult): JsonNode = objectMapper.readTree(result.response.contentAsString)

    private fun org.springframework.test.web.servlet.MockHttpServletRequestDsl.bearer(token: String) {
        header("Authorization", "Bearer $token")
    }

    companion object {
        private const val APP_PASSWORD = "app-test"
        private const val FLYWAY_PASSWORD = "flyway-test"
        private val asyncWorkerPort =
            ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { it.localPort }
        private val PROVIDER_KEYS =
            setOf(
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
                "KIS_APP_KEY",
                "KIS_APP_SECRET",
                "OPENAI_API_KEY",
                "VOYAGE_API_KEY",
            )
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:" +
                        "1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            PostgreSQLContainer(postgresImage)
                .withDatabaseName("decision_async_api")
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
            registry.add("app.async.polling-enabled") { "false" }
            registry.add("app.async.worker.jdbc-url", postgres::getJdbcUrl)
            registry.add("app.async.worker.grpc-target") { "127.0.0.1:$asyncWorkerPort" }
        }

        private fun repositoryRoot(): Path {
            var cursor = Path.of(System.getProperty("user.dir")).toAbsolutePath().normalize()
            while (true) {
                if (Files.isRegularFile(cursor.resolve(PYTHON_SERVICES_RELATIVE_PATH).resolve("pyproject.toml"))) {
                    return cursor
                }
                cursor = cursor.parent ?: break
            }
            throw IllegalStateException("repository root not found")
        }

        private val PYTHON_SERVICES_RELATIVE_PATH: Path =
            Path.of("workspaces", "decision-platform", "python-services")
    }
}
