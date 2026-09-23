package com.capstone.decision

import com.capstone.decision.application.security.ActorRlsScopePort
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.infrastructure.brokerage.BrokerageCredentialCrypto
import com.capstone.decision.infrastructure.brokerage.BrokerageKekFile
import com.capstone.decision.infrastructure.brokerage.MockCredentialConnectionRepository
import com.capstone.decision.infrastructure.brokerage.MockCredentialDisconnectRepository
import com.capstone.decision.infrastructure.brokerage.MockCredentialSettingsService
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.context.ApplicationContext
import org.springframework.dao.PessimisticLockingFailureException
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken
import org.springframework.security.core.context.SecurityContextHolder
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.springframework.transaction.PlatformTransactionManager
import org.springframework.transaction.support.TransactionTemplate
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermission
import java.security.SecureRandom
import java.sql.DriverManager
import java.sql.SQLException

@Testcontainers
@SpringBootTest(
    properties = ["spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration"],
)
class BoundMockCredentialIntegrationTest(
    @Autowired private val context: ApplicationContext,
    @Autowired private val actorRlsScope: ActorRlsScopePort,
    @Autowired private val transactionManager: PlatformTransactionManager,
    @Autowired private val testActorCapabilityIssuer: TestActorCapabilityIssuer,
) : SpringApiIntegrationTestBase() {
    @TempDir
    lateinit var root: Path

    @Test
    fun `owner removes only their settled mock credential`() {
        val directory = prepareKeyDirectory()
        val service =
            MockCredentialSettingsService(
                context.getBeanProvider(NamedParameterJdbcTemplate::class.java),
                actorRlsScope,
                BrokerageCredentialCrypto(BrokerageKekFile(directory.toString())),
            )
        val repository =
            MockCredentialDisconnectRepository(
                context.getBeanProvider(NamedParameterJdbcTemplate::class.java),
                actorRlsScope,
            )
        val transaction = TransactionTemplate(transactionManager)
        asActor("usr_demo_user") {
            transaction.executeWithoutResult {
                service.save("usr_demo_user", "K" + "A".repeat(19), "S" + "B".repeat(39), "5" + "0".repeat(9))
            }
        }
        asActor("usr_demo_admin") {
            transaction.executeWithoutResult {
                service.save("usr_demo_admin", "Z" + "C".repeat(19), "T" + "D".repeat(39), "6" + "1".repeat(9))
            }
        }
        val own = requireNotNull(asActor("usr_demo_user") { transaction.execute { service.summary("usr_demo_user") } })
        asActor("usr_demo_admin") {
            assertThrows(IllegalStateException::class.java) {
                transaction.execute { repository.disconnect("usr_demo_user", own.accountId, own.revision) }
            }
        }
        val result =
            asActor("usr_demo_user") {
                transaction.execute { repository.disconnect("usr_demo_user", own.accountId, own.revision) }
            }
        assertEquals("REMOVED", result)
        assertEquals(null, asActor("usr_demo_user") { transaction.execute { service.summary("usr_demo_user") } })
        assertNotNull(asActor("usr_demo_admin") { transaction.execute { service.summary("usr_demo_admin") } })
    }

    @Test
    fun `owner stores encrypted mock values and rotation resets connection without exposing another owner`() {
        val directory = prepareKeyDirectory()
        val service =
            MockCredentialSettingsService(
                context.getBeanProvider(NamedParameterJdbcTemplate::class.java),
                actorRlsScope,
                BrokerageCredentialCrypto(BrokerageKekFile(directory.toString())),
            )
        val key = "K" + "A".repeat(19)
        val secret = "S" + "B".repeat(39)
        val accountNo = "5" + "0".repeat(9)
        val transaction = TransactionTemplate(transactionManager)
        asActor("usr_demo_user") {
            transaction.executeWithoutResult { service.save("usr_demo_user", key, secret, accountNo) }
        }
        val first = asActor("usr_demo_user") { transaction.execute { service.summary("usr_demo_user") } }
        assertNotNull(first)
        assertEquals("STORED", first?.state)
        assertEquals(1L, first?.revision)
        assertFalse(first?.connected ?: true)
        assertFalse(first?.certified ?: true)
        assertEquals("AAAA", first?.appKeyLast4)
        assertEquals("0000", first?.accountNoLast4)
        assertTrue(first?.accountId?.matches(Regex("^acct_[0-9a-f]{32}$")) == true)
        val firstAccountId = requireNotNull(first?.accountId)
        val connectionRepository =
            MockCredentialConnectionRepository(
                context.getBeanProvider(NamedParameterJdbcTemplate::class.java),
                actorRlsScope,
            )
        asActor("usr_demo_user") {
            transaction.execute {
                service.resolveEnvelope("usr_demo_user", firstAccountId).use { envelope ->
                    assertEquals("STORED", envelope.state)
                    assertEquals(1L, envelope.revision)
                    BrokerageCredentialCrypto(BrokerageKekFile(directory.toString()))
                        .open("usr_demo_user", firstAccountId, envelope.sealed)
                        .use { opened ->
                            assertEquals(key, opened.appKey.toString(Charsets.US_ASCII))
                            assertEquals(secret, opened.appSecret.toString(Charsets.US_ASCII))
                            assertEquals(accountNo, opened.accountNo.toString(Charsets.US_ASCII))
                        }
                }
            }
        }
        asActor("usr_demo_user") {
            transaction.executeWithoutResult {
                connectionRepository.beginAttempt("usr_demo_user", firstAccountId, 1)
            }
            assertThrows(PessimisticLockingFailureException::class.java) {
                transaction.executeWithoutResult {
                    connectionRepository.beginAttempt("usr_demo_user", firstAccountId, 1)
                }
            }
            transaction.executeWithoutResult {
                connectionRepository.markConnected("usr_demo_user", firstAccountId, 1)
            }
        }
        val connected = asActor("usr_demo_user") { transaction.execute { service.summary("usr_demo_user") } }
        assertEquals("CONNECTED", connected?.state)
        assertTrue(connected?.connected == true)
        assertFalse(connected?.certified ?: true)

        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.prepareStatement("select secret_ciphertext from user_broker_credentials where owner_user_id = ?").use { statement ->
                statement.setString(1, "usr_demo_user")
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    assertFalse(result.getBytes(1).contentEquals(key.toByteArray()))
                    assertFalse(result.next())
                }
            }
            val auditSql =
                "select target_id, payload_json::text from audit_logs where user_id = ? and action = 'MOCK_CREDENTIAL_STORED' and target_id = ?"
            connection.prepareStatement(auditSql).use { statement ->
                statement.setString(1, "usr_demo_user")
                statement.setString(2, firstAccountId)
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    assertEquals(firstAccountId, result.getString("target_id"))
                    val payload = result.getString(2)
                    assertFalse(payload.contains(key))
                    assertFalse(payload.contains(secret))
                    assertFalse(payload.contains(accountNo))
                    assertFalse(result.next())
                }
            }
        }
        asActor("usr_demo_admin") {
            assertThrows(IllegalStateException::class.java) {
                transaction.execute { service.summary("usr_demo_user") }
            }
            assertThrows(IllegalStateException::class.java) {
                transaction.executeWithoutResult {
                    connectionRepository.markConnected("usr_demo_user", firstAccountId, 1)
                }
            }
        }
        asActor("usr_demo_admin") {
            transaction.executeWithoutResult {
                service.save("usr_demo_admin", "Z" + "C".repeat(19), "T" + "D".repeat(39), "6" + "1".repeat(9))
            }
        }
        val otherAccountId =
            asActor("usr_demo_admin") {
                transaction.execute { service.summary("usr_demo_admin")?.accountId }
            }
        assertNotNull(otherAccountId)
        asActor("usr_demo_user") {
            assertThrows(IllegalStateException::class.java) {
                transaction.execute { service.resolveEnvelope("usr_demo_user", requireNotNull(otherAccountId)).use { } }
            }
        }
        asActor("usr_demo_user") {
            transaction.executeWithoutResult { service.save("usr_demo_user", key, secret, accountNo) }
        }
        val rotated = asActor("usr_demo_user") { transaction.execute { service.summary("usr_demo_user") } }
        assertEquals(2L, rotated?.revision)
        assertNotEquals(firstAccountId, rotated?.accountId)
        assertEquals("STORED", rotated?.state)
        assertFalse(rotated?.connected ?: true)
        asActor("usr_demo_user") {
            assertThrows(IllegalStateException::class.java) {
                transaction.execute { service.resolveEnvelope("usr_demo_user", firstAccountId).use { } }
            }
            assertThrows(PessimisticLockingFailureException::class.java) {
                transaction.executeWithoutResult {
                    connectionRepository.markConnected("usr_demo_user", firstAccountId, 1)
                }
            }
        }
    }

    @Test
    fun `decision_app cannot read a credential with a forged owner GUC`() {
        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", "app-test").use { connection ->
            connection.autoCommit = false
            connection.createStatement().use { statement ->
                statement.execute("select set_config('app.actor_user_id','usr_demo_user',true)")
            }
            val denied =
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.executeQuery("select * from read_bound_mock_broker_summary_v2('usr_demo_user')")
                    }
                }
            assertEquals("42501", denied.sqlState)
            connection.rollback()
            connection.createStatement().use { statement ->
                statement.execute("select set_config('app.actor_user_id','usr_demo_user',true)")
            }
            val envelopeDenied =
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.executeQuery(
                            "select * from read_bound_mock_broker_envelope_v3('usr_demo_user','acct_" + "0".repeat(32) + "')",
                        )
                    }
                }
            assertEquals("42501", envelopeDenied.sqlState)
            connection.rollback()
            connection.createStatement().use { statement ->
                statement.execute("select set_config('app.actor_user_id','usr_demo_user',true)")
            }
            val disconnectDenied =
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.executeQuery(
                            "select disconnect_bound_mock_broker_credential_v1('usr_demo_user','acct_" + "0".repeat(32) + "',1)",
                        )
                    }
                }
            assertEquals("42501", disconnectDenied.sqlState)
        }
    }

    private fun <T> asActor(
        userId: String,
        block: () -> T,
    ): T {
        val previous = SecurityContextHolder.getContext()
        val context = SecurityContextHolder.createEmptyContext()
        context.authentication =
            UsernamePasswordAuthenticationToken(
                AppPrincipal(
                    userId,
                    "test-user",
                    if (userId ==
                        "usr_demo_admin"
                    ) {
                        "ADMIN"
                    } else {
                        "USER"
                    },
                    1,
                    testActorCapabilityIssuer.actorRef(userId),
                ),
                null,
                emptyList(),
            )
        SecurityContextHolder.setContext(context)
        return try {
            block()
        } finally {
            SecurityContextHolder.setContext(previous)
        }
    }

    private fun prepareKeyDirectory(): Path {
        val directory = Files.createDirectory(root.resolve("brokerage"))
        Files.setPosixFilePermissions(
            directory,
            setOf(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE, PosixFilePermission.OWNER_EXECUTE),
        )
        val key = ByteArray(32).also(SecureRandom()::nextBytes)
        val file = directory.resolve("brokerage-kek-v1.key")
        Files.write(file, key)
        Files.setPosixFilePermissions(file, setOf(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE))
        key.fill(0)
        return directory
    }

    companion object {
        private val postgresImage =
            DockerImageName
                .parse("pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb")
                .asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            stablePostgresContainer(postgresImage)
                .withDatabaseName("decision_bound_mock")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")

        @DynamicPropertySource
        @JvmStatic
        fun postgresProperties(registry: DynamicPropertyRegistry) {
            registry.add("spring.datasource.url", postgres::getJdbcUrl)
            registry.add("spring.datasource.username") { "decision_app" }
            registry.add("spring.datasource.password") { "app-test" }
            registry.add("spring.flyway.user") { "flyway" }
            registry.add("spring.flyway.password") { "flyway-test" }
        }
    }
}
