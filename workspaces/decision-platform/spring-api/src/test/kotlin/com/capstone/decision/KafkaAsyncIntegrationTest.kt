package com.capstone.decision

import com.capstone.decision.application.async.AsyncJobRequest
import com.capstone.decision.application.async.AsyncJobType
import com.capstone.decision.application.async.AsyncPipelinePort
import com.capstone.decision.infrastructure.async.AsyncAdapterMode
import com.capstone.decision.infrastructure.async.AsyncDeploymentMode
import com.capstone.decision.infrastructure.async.DbAsyncPipelineAdapter
import com.capstone.decision.infrastructure.async.KafkaAsyncPipelineAdapter
import com.capstone.decision.infrastructure.async.KafkaAsyncProperties
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.context.ApplicationContext
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.kafka.core.KafkaAdmin
import org.springframework.kafka.core.KafkaTemplate
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import javax.sql.DataSource

@Testcontainers
@SpringBootTest
class KafkaAsyncIntegrationTest(
    @Autowired private val context: ApplicationContext,
    @Autowired private val asyncPipelinePort: AsyncPipelinePort,
    @Autowired private val applicationDataSource: DataSource,
    @Autowired private val actorCapabilityIssuer: TestActorCapabilityIssuer,
) : SpringApiIntegrationTestBase() {
    private val appJdbc by lazy { JdbcTemplate(applicationDataSource) }
    private val ownerJdbc by lazy {
        JdbcTemplate(
            postgres.createConnection("").let { connection ->
                org.springframework.jdbc.datasource
                    .SingleConnectionDataSource(connection, true)
            },
        )
    }

    @BeforeEach
    fun clean() {
        ownerJdbc.update("delete from event_outbox")
        ownerJdbc.update("delete from async_job")
    }

    @Test
    fun `Kafka adapter only records a pending transactional outbox event`() {
        assertEquals(1, context.getBeansOfType(KafkaAsyncPipelineAdapter::class.java).size)
        assertEquals(0, context.getBeansOfType(DbAsyncPipelineAdapter::class.java).size)
        val accepted =
            asTestActor(actorCapabilityIssuer) {
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
            }

        assertEquals(
            "PENDING",
            ownerJdbc.queryForObject(
                "select status from event_outbox where event_id=?",
                String::class.java,
                accepted.eventId,
            ),
        )
        assertNull(
            ownerJdbc.queryForObject(
                "select published_at from event_outbox where event_id=?",
                java.time.OffsetDateTime::class.java,
                accepted.eventId,
            ),
        )
        assertEquals(
            0,
            ownerJdbc.queryForObject(
                "select count(*) from processed_event where event_id=?",
                Int::class.java,
                accepted.eventId,
            ),
        )
    }

    @Test
    fun `Spring API has no Kafka client or publisher authority`() {
        assertEquals(0, context.getBeansOfType(KafkaAdmin::class.java).size)
        assertEquals(0, context.getBeansOfType(KafkaTemplate::class.java).size)
        assertFalse(context.beanDefinitionNames.any { it.contains("kafkaOutbox", ignoreCase = true) })
        assertFalse(context.beanDefinitionNames.any { it.contains("kafkaReadiness", ignoreCase = true) })
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

    companion object {
        private const val APP_PASSWORD = "app-test"
        private const val FLYWAY_PASSWORD = "flyway-test"
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

        @DynamicPropertySource
        @JvmStatic
        fun properties(registry: DynamicPropertyRegistry) {
            registry.add("spring.datasource.url", postgres::getJdbcUrl)
            registry.add("spring.datasource.username") { "decision_app" }
            registry.add("spring.datasource.password") { APP_PASSWORD }
            registry.add("spring.flyway.user") { "flyway" }
            registry.add("spring.flyway.password") { FLYWAY_PASSWORD }
            registry.add("spring.autoconfigure.exclude") {
                "org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration"
            }
            registry.add("app.async.adapter") { "kafka" }
            registry.add("app.async.polling-enabled") { "false" }
            registry.add("app.async.worker.enabled") { "false" }
        }
    }
}
