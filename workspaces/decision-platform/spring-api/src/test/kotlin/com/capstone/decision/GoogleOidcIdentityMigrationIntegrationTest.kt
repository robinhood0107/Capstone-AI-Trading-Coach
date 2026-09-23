package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import java.sql.DriverManager
import java.sql.SQLException
import java.util.UUID
import java.util.concurrent.Callable
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors

/** Verifies the database boundary that the Google callback will call after ID-token validation. */
@Testcontainers
@SpringBootTest(
    properties = ["spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration"],
)
class GoogleOidcIdentityMigrationIntegrationTest : SpringApiIntegrationTestBase() {
    @Test
    fun `concurrent first login creates one USER and separate subjects never share sessions`() {
        val subject = "test-" + UUID.randomUUID()
        val start = CountDownLatch(1)
        val executor = Executors.newFixedThreadPool(2)
        val sessions =
            try {
                val futures =
                    (1..2).map {
                        executor.submit(
                            Callable {
                                start.await()
                                issueSession(subject)
                            },
                        )
                    }
                start.countDown()
                futures.map { it.get() }
            } finally {
                executor.shutdownNow()
            }
        assertEquals(sessions[0].userId, sessions[1].userId)
        assertNotEquals(sessions[0].handle, sessions[1].handle)
        assertEquals("USER", sessions[0].role)
        assertTrue(sessions[0].userId.startsWith("usr_"))
        assertTrue(sessions[0].username.startsWith("oidc_"))

        val other = issueSession("test-" + UUID.randomUUID())
        assertNotEquals(sessions[0].userId, other.userId)
        assertEquals("USER", other.role)

        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.prepareStatement("select password_hash, role from users where user_id = ?").use { statement ->
                statement.setString(1, sessions[0].userId)
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    assertNull(result.getString("password_hash"))
                    assertEquals("USER", result.getString("role"))
                    assertFalse(result.next())
                }
            }
            connection.prepareStatement("update users set status = 'DISABLED' where user_id = ?").use { statement ->
                statement.setString(1, sessions[0].userId)
                assertEquals(1, statement.executeUpdate())
            }
        }
        val blocked = assertThrows(SQLException::class.java) { issueSession(subject) }
        assertEquals("42501", blocked.sqlState)
    }

    @Test
    fun `only decision_auth can issue a Google session and invalid issuer is rejected`() {
        val subject = "test-" + UUID.randomUUID()
        val wrongIssuer =
            assertThrows(SQLException::class.java) {
                issueSession(subject, issuer = "https://example.invalid")
            }
        assertEquals("42501", wrongIssuer.sqlState)
        val wrongRole =
            assertThrows(SQLException::class.java) {
                DriverManager.getConnection(postgres.jdbcUrl, "decision_app", "app-test").use { connection ->
                    connection.prepareStatement("select * from authenticate_google_oidc_actor_v1(?,?,?,?)").use { statement ->
                        statement.setString(1, GOOGLE_ISSUER)
                        statement.setString(2, subject)
                        statement.setBoolean(3, false)
                        statement.setInt(4, 3_600)
                        statement.executeQuery()
                    }
                }
            }
        assertEquals("42501", wrongRole.sqlState)
    }

    @Test
    fun `logout revokes only the verified actor session`() {
        val first = issueSession("test-" + UUID.randomUUID())
        val second = issueSession("test-" + UUID.randomUUID())
        assertTrue(sessionExists(first.handle))
        assertFalse(revokeSession(first.handle, second.userId))
        assertTrue(sessionExists(first.handle))
        assertTrue(revokeSession(first.handle, first.userId))
        assertFalse(sessionExists(first.handle))
        assertTrue(sessionExists(second.handle))
    }

    @Test
    fun `operator allowlist changes rotate role and invalidate old sessions`() {
        val subject = "test-" + UUID.randomUUID()
        val ordinary = issueSession(subject)
        assertEquals("USER", ordinary.role)
        val operator = issueSession(subject, operatorSubject = true)
        assertEquals(ordinary.userId, operator.userId)
        assertEquals("ADMIN", operator.role)
        assertFalse(sessionExists(ordinary.handle))
        val demoted = issueSession(subject)
        assertEquals(ordinary.userId, demoted.userId)
        assertEquals("USER", demoted.role)
        assertFalse(sessionExists(operator.handle))
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection
                .prepareStatement(
                    "select payload_json::text from audit_logs where user_id = ? and action = 'GOOGLE_OIDC_ROLE_CHANGED'",
                ).use { statement ->
                    statement.setString(1, ordinary.userId)
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        assertFalse(result.getString(1).contains(subject))
                        assertTrue(result.next())
                        assertFalse(result.getString(1).contains(subject))
                        assertFalse(result.next())
                    }
                }
        }
    }

    @Test
    fun `only current Google ADMIN can read and change the operator AI budget`() {
        val admin = issueSession("test-budget-admin-" + UUID.randomUUID(), operatorSubject = true)
        val user = issueSession("test-budget-user-" + UUID.randomUUID())
        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", "app-test").use { connection ->
            val denied =
                assertThrows(SQLException::class.java) {
                    connection.prepareStatement("select * from read_operator_ai_budget_policy_v1(?,?)").use { statement ->
                        statement.setString(1, user.userId)
                        statement.setLong(2, 1)
                        statement.executeQuery()
                    }
                }
            assertEquals("42501", denied.sqlState)

            val revision =
                connection.prepareStatement("select revision from read_operator_ai_budget_policy_v1(?,?)").use { statement ->
                    statement.setString(1, admin.userId)
                    statement.setLong(2, 1)
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        result.getLong(1)
                    }
                }
            connection.prepareStatement("select set_operator_ai_budget_policy_v1(?,?,?,?)").use { statement ->
                statement.setString(1, admin.userId)
                statement.setLong(2, 1)
                statement.setLong(3, 1_230_000)
                statement.setLong(4, revision)
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    assertEquals(revision + 1, result.getLong(1))
                }
            }
            val stale =
                assertThrows(SQLException::class.java) {
                    connection.prepareStatement("select set_operator_ai_budget_policy_v1(?,?,?,?)").use { statement ->
                        statement.setString(1, admin.userId)
                        statement.setLong(2, 1)
                        statement.setLong(3, 2_000_000)
                        statement.setLong(4, revision)
                        statement.executeQuery()
                    }
                }
            assertEquals("40001", stale.sqlState)
            val directRead =
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.executeQuery("select * from operator_ai_budget_policy")
                    }
                }
            assertEquals("42501", directRead.sqlState)
        }
    }

    private fun revokeSession(
        handle: String,
        userId: String,
    ): Boolean =
        DriverManager.getConnection(postgres.jdbcUrl, "decision_auth", "auth-test-secret-0001").use { connection ->
            connection.prepareStatement("select revoke_actor_auth_session_v1(?,?,?)").use { statement ->
                statement.setString(1, handle)
                statement.setString(2, userId)
                statement.setLong(3, 1)
                statement.executeQuery().use { result ->
                    check(result.next())
                    result.getBoolean(1)
                }
            }
        }

    private fun sessionExists(handle: String): Boolean =
        DriverManager.getConnection(postgres.jdbcUrl, "decision_auth", "auth-test-secret-0001").use { connection ->
            connection.prepareStatement("select * from read_actor_auth_session_v1(?)").use { statement ->
                statement.setString(1, handle)
                statement.executeQuery().use { result -> result.next() }
            }
        }

    private fun issueSession(
        subject: String,
        issuer: String = GOOGLE_ISSUER,
        operatorSubject: Boolean = false,
    ): IssuedSession =
        DriverManager.getConnection(postgres.jdbcUrl, "decision_auth", "auth-test-secret-0001").use { connection ->
            connection.prepareStatement("select * from authenticate_google_oidc_actor_v1(?,?,?,?)").use { statement ->
                statement.setString(1, issuer)
                statement.setString(2, subject)
                statement.setBoolean(3, operatorSubject)
                statement.setInt(4, 3_600)
                statement.executeQuery().use { result ->
                    check(result.next())
                    IssuedSession(
                        handle = result.getString("session_handle"),
                        userId = result.getString("actor_user_id"),
                        username = result.getString("username"),
                        role = result.getString("actor_role"),
                    )
                }
            }
        }

    private data class IssuedSession(
        val handle: String,
        val userId: String,
        val username: String,
        val role: String,
    )

    companion object {
        private const val GOOGLE_ISSUER = "https://accounts.google.com"
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
        fun postgresProperties(registry: DynamicPropertyRegistry) {
            registry.add("spring.datasource.url", postgres::getJdbcUrl)
            registry.add("spring.datasource.username", postgres::getUsername)
            registry.add("spring.datasource.password", postgres::getPassword)
            registry.add("spring.flyway.user", postgres::getUsername)
            registry.add("spring.flyway.password", postgres::getPassword)
        }
    }
}
