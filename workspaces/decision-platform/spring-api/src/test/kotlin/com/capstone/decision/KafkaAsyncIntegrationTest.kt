package com.capstone.decision

import com.capstone.decision.application.async.AsyncJobRequest
import com.capstone.decision.application.async.AsyncJobType
import com.capstone.decision.application.async.AsyncPipelinePort
import com.capstone.decision.infrastructure.async.AsyncAdapterMode
import com.capstone.decision.infrastructure.async.AsyncDeploymentMode
import com.capstone.decision.infrastructure.async.DbAsyncPipelineAdapter
import com.capstone.decision.infrastructure.async.KafkaAsyncPipelineAdapter
import com.capstone.decision.infrastructure.async.KafkaAsyncProperties
import com.capstone.decision.infrastructure.async.KafkaOutboxPublisher
import org.apache.kafka.clients.admin.AdminClient
import org.apache.kafka.clients.admin.AdminClientConfig
import org.apache.kafka.clients.admin.NewTopic
import org.apache.kafka.clients.consumer.ConsumerConfig
import org.apache.kafka.clients.consumer.KafkaConsumer
import org.apache.kafka.common.serialization.StringDeserializer
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.context.ApplicationContext
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.jdbc.datasource.DriverManagerDataSource
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.kafka.KafkaContainer
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import tools.jackson.databind.ObjectMapper
import java.io.IOException
import java.nio.file.Files
import java.nio.file.Path
import java.sql.Timestamp
import java.time.Duration
import java.util.Properties
import java.util.concurrent.TimeUnit
import javax.sql.DataSource

@Testcontainers
@SpringBootTest
class KafkaAsyncIntegrationTest(
    @Autowired private val context: ApplicationContext,
    @Autowired private val asyncPipelinePort: AsyncPipelinePort,
    @Autowired private val publisher: KafkaOutboxPublisher,
    @Autowired private val objectMapper: ObjectMapper,
    @Autowired private val applicationDataSource: DataSource,
) : SpringApiIntegrationTestBase() {
    private val appJdbc by lazy { JdbcTemplate(applicationDataSource) }
    private val ownerJdbc by lazy {
        JdbcTemplate(DriverManagerDataSource(postgres.jdbcUrl, postgres.username, postgres.password))
    }
    private val demoJdbc by lazy {
        JdbcTemplate(DriverManagerDataSource(postgres.jdbcUrl, "decision_demo", "demo-test-secret-0001"))
    }

    @BeforeEach
    fun clean() {
        ownerJdbc.update("delete from event_outbox")
        ownerJdbc.update("delete from async_job")
    }

    @Test
    fun `Kafka adapter publishes after commit and keeps DB update failure replay safe`() {
        assertEquals(1, context.getBeansOfType(KafkaAsyncPipelineAdapter::class.java).size)
        assertEquals(0, context.getBeansOfType(DbAsyncPipelineAdapter::class.java).size)
        val accepted =
            asyncPipelinePort.request(
                AsyncJobRequest(
                    type = AsyncJobType.ARTIFACT_INGEST,
                    requestedBy = "usr_demo_user",
                    references =
                        mapOf(
                            "artifactId" to "artifact_kafka_00000001",
                            "contentHash" to "sha256:" + "d".repeat(64),
                        ),
                ),
            )
        assertEquals(
            "PENDING",
            ownerJdbc.queryForObject("select status from event_outbox where event_id=?", String::class.java, accepted.eventId),
        )

        ownerJdbc.execute("revoke execute on function complete_event_outbox(text,uuid) from decision_app")
        try {
            publisher.poll()
        } finally {
            ownerJdbc.execute("grant execute on function complete_event_outbox(text,uuid) to decision_app")
        }
        assertEquals(
            "FAILED",
            ownerJdbc.queryForObject("select status from event_outbox where event_id=?", String::class.java, accepted.eventId),
        )
        ownerJdbc.update("update event_outbox set next_attempt_at=now() where event_id=?", accepted.eventId)
        publisher.poll()

        assertEquals(
            "PUBLISHED",
            ownerJdbc.queryForObject("select status from event_outbox where event_id=?", String::class.java, accepted.eventId),
        )
        assertNotNull(
            ownerJdbc.queryForObject(
                "select published_at from event_outbox where event_id=?",
                java.time.OffsetDateTime::class.java,
                accepted.eventId,
            ),
        )
        val records = consume("artifact.ingest-requested.v1", 2)
        val matching = records.filter { objectMapper.readTree(it).path("eventId").stringValue() == accepted.eventId }
        assertEquals(2, matching.size)
        val envelopes = matching.map { objectMapper.readTree(it) }
        assertEquals(setOf(accepted.eventId), envelopes.map { it.path("eventId").stringValue() }.toSet())
        assertEquals(setOf(1), envelopes.map { it.path("schemaVersion").intValue() }.toSet())
        assertFalse(matching.any { SECRET_PATTERN.containsMatchIn(it) })
        assertFalse(matching.any { it.contains("ownerRef") || it.contains("usr_demo_user") })
    }

    @Test
    fun `Kafka plaintext configuration fails closed outside loopback`() {
        assertThrows(IllegalArgumentException::class.java) {
            KafkaAsyncProperties(
                bootstrapServers = listOf("kafka.internal:9092"),
                deploymentMode = AsyncDeploymentMode.DEPLOY,
                securityProtocol = "PLAINTEXT",
            ).validate(AsyncAdapterMode.KAFKA)
        }
        assertThrows(IllegalArgumentException::class.java) {
            KafkaAsyncProperties(
                bootstrapServers = listOf("kafka.internal:9093"),
                deploymentMode = AsyncDeploymentMode.DEPLOY,
                securityProtocol = "SSL",
            ).validate(AsyncAdapterMode.KAFKA)
        }
    }

    @Test
    fun `Kafka publisher and real Python consumer materialize exactly once`() {
        val modelProjection = S8SyntheticProjectionFixture.modelProjection(objectMapper)
        val backtestProjection = S8SyntheticProjectionFixture.backtestProjection(objectMapper)
        stage("MODEL_EVALUATION", "model-evaluation.json", modelProjection)
        stage("BACKTEST", "backtest.json", backtestProjection)
        val process = startPythonConsumer()
        try {
            val accepted =
                asyncPipelinePort.request(
                    AsyncJobRequest(
                        type = AsyncJobType.ARTIFACT_INGEST,
                        requestedBy = "usr_demo_user",
                        references =
                            mapOf(
                                "artifactId" to S8SyntheticProjectionFixture.ARTIFACT_ID,
                                "contentHash" to S8SyntheticProjectionFixture.FILE_HASH,
                            ),
                    ),
                )
            publisher.poll()
            awaitCompleted(process, accepted.jobId)
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
                    "select count(*) from async_materialization_receipt where event_id=?",
                    Int::class.java,
                    accepted.eventId,
                ),
            )
            assertEquals(
                1,
                ownerJdbc.queryForObject(
                    "select count(*) from event_outbox where aggregate_id=? and event_type='artifact.ingested.v1'",
                    Int::class.java,
                    accepted.jobId,
                ),
            )
            assertEquals(
                S8SyntheticProjectionFixture.sha256(modelProjection),
                ownerJdbc.queryForObject(
                    "select projection_hash from dashboard_artifact_views where view_kind='MODEL_EVALUATION' and run_id=?",
                    String::class.java,
                    S8SyntheticProjectionFixture.RUN_ID,
                ),
            )
            assertEquals(
                S8SyntheticProjectionFixture.sha256(backtestProjection),
                ownerJdbc.queryForObject(
                    "select projection_hash from dashboard_artifact_views where view_kind='BACKTEST' and run_id=?",
                    String::class.java,
                    S8SyntheticProjectionFixture.RUN_ID,
                ),
            )
        } finally {
            terminateProcess(process)
        }
    }

    private fun stage(
        kind: String,
        fileName: String,
        projection: String,
    ) {
        assertEquals(
            true,
            demoJdbc.queryForObject(
                "select stage_synthetic_dashboard_view(?,?,?,?,?,?,?,?,?,?)",
                Boolean::class.java,
                S8SyntheticProjectionFixture.ARTIFACT_ID,
                "usr_demo_user",
                S8SyntheticProjectionFixture.RUN_ID,
                fileName,
                S8SyntheticProjectionFixture.FILE_HASH,
                kind,
                projection,
                S8SyntheticProjectionFixture.sha256(projection),
                Timestamp.from(S8SyntheticProjectionFixture.asOf),
                Timestamp.from(S8SyntheticProjectionFixture.freshUntil),
            ),
        )
    }

    private fun startPythonConsumer(): Process {
        val pythonServices = repositoryRoot().resolve(PYTHON_SERVICES_RELATIVE_PATH)
        check(Files.isRegularFile(pythonServices.resolve("pyproject.toml")))
        val bootstrap = kafka.bootstrapServers.substringAfter("://").replace("localhost:", "127.0.0.1:")
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
                "app.async_worker.kafka_consumer",
            ).directory(pythonServices.toFile())
                .redirectOutput(ProcessBuilder.Redirect.DISCARD)
                .redirectError(ProcessBuilder.Redirect.DISCARD)
        builder.environment().apply {
            put("PYTHONDONTWRITEBYTECODE", "1")
            put("UV_OFFLINE", "1")
            put("KAFKA_BOOTSTRAP_SERVERS", bootstrap)
            put("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
            put("ASYNC_WORKER_DATABASE_DSN", dsn)
            put("ASYNC_PARTITION_HMAC_KEY", TEST_ASYNC_PARTITION_HMAC_KEY)
            PROVIDER_KEYS.forEach(::remove)
        }
        return try {
            builder.start()
        } catch (exception: IOException) {
            throw AssertionError("S7 Kafka E2E requires the frozen uv Python runtime.", exception)
        }
    }

    private fun awaitCompleted(
        process: Process,
        jobId: String,
    ) {
        val deadline = System.nanoTime() + Duration.ofSeconds(15).toNanos()
        while (System.nanoTime() < deadline) {
            check(process.isAlive) { "S7 Python Kafka consumer exited before materialization." }
            val status = ownerJdbc.queryForObject("select status from async_job where job_id=?", String::class.java, jobId)
            if (status == "COMPLETED") return
            Thread.sleep(100)
        }
        throw AssertionError("S7 Python Kafka consumer did not complete the async job in time.")
    }

    private fun terminateProcess(process: Process) {
        val descendants = process.toHandle().descendants().use { it.toList() }
        descendants.filter { it.isAlive }.forEach { it.destroy() }
        if (process.isAlive) process.destroy()
        if (!process.waitFor(5, TimeUnit.SECONDS)) {
            descendants.filter { it.isAlive }.forEach { it.destroyForcibly() }
            process.destroyForcibly()
            check(process.waitFor(5, TimeUnit.SECONDS))
        }
    }

    private fun consume(
        topic: String,
        expected: Int,
    ): List<String> {
        val properties =
            Properties().apply {
                put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, kafka.bootstrapServers)
                put(ConsumerConfig.GROUP_ID_CONFIG, "spring-kafka-e2e-${System.nanoTime()}")
                put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest")
                put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, "false")
                put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer::class.java)
                put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer::class.java)
            }
        KafkaConsumer<String, String>(properties).use { consumer ->
            consumer.subscribe(listOf(topic))
            val values = mutableListOf<String>()
            val deadline = System.nanoTime() + Duration.ofSeconds(10).toNanos()
            while (values.size < expected && System.nanoTime() < deadline) {
                consumer.poll(Duration.ofMillis(250)).forEach { values += it.value() }
            }
            return values
        }
    }

    companion object {
        private const val APP_PASSWORD = "app-test"
        private const val FLYWAY_PASSWORD = "flyway-test"
        private val SECRET_PATTERN = Regex("(?i)(token|secret|password|account|authorization|cookie)")
        private val PROVIDER_KEYS =
            setOf(
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
                "KIS_APP_KEY",
                "KIS_APP_SECRET",
                "OPENAI_API_KEY",
                "VOYAGE_API_KEY",
            )
        private val BASE_TOPICS =
            listOf(
                "artifact.ingest-requested.v1",
                "artifact.ingested.v1",
                "signal.received.v1",
                "feature.updated.v1",
                "lightgbm.signal-generated.v1",
                "risk.context-updated.v1",
                "risk.decision-created.v1",
                "order.event-created.v1",
                "rag.index-requested.v1",
                "rag.index-completed.v1",
                "model.eval-requested.v1",
                "model.eval-completed.v1",
            )
        private val ALL_TOPICS =
            BASE_TOPICS.flatMap { base ->
                val stem = base.removeSuffix(".v1")
                listOf(base, "$stem.retry.v1", "$stem.dlq.v1")
            }
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:" +
                        "1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            stablePostgresContainer(postgresImage)
                .withDatabaseName("decision_kafka_async")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")

        @Container
        @JvmStatic
        val kafka: KafkaContainer = KafkaContainer("apache/kafka-native:3.8.0")

        @DynamicPropertySource
        @JvmStatic
        fun properties(registry: DynamicPropertyRegistry) {
            AdminClient
                .create(mapOf(AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG to kafka.bootstrapServers))
                .use { admin ->
                    admin.createTopics(ALL_TOPICS.map { NewTopic(it, 3, 1.toShort()) }).all().get()
                }
            registry.add("spring.datasource.url", postgres::getJdbcUrl)
            registry.add("spring.datasource.username") { "decision_app" }
            registry.add("spring.datasource.password") { APP_PASSWORD }
            registry.add("spring.flyway.user") { "flyway" }
            registry.add("spring.flyway.password") { FLYWAY_PASSWORD }
            registry.add("app.async.adapter") { "kafka" }
            registry.add("app.async.polling-enabled") { "false" }
            registry.add("app.async.worker.enabled") { "false" }
            val loopbackBootstrap = kafka.bootstrapServers.substringAfter("://").replace("localhost:", "127.0.0.1:")
            registry.add("app.async.kafka.bootstrap-servers") { loopbackBootstrap }
            registry.add("spring.kafka.bootstrap-servers") { loopbackBootstrap }
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
