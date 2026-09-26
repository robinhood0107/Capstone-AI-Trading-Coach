package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
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

/** Verifies provider-neutral identity creation after the provider response is validated. */
@Testcontainers
@SpringBootTest(
    properties = ["spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration"],
)
class SocialLoginIdentityMigrationIntegrationTest : SpringApiIntegrationTestBase() {
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
        assertNotEquals("usr_demo_user", sessions[0].userId)
        assertTrue(sessions[0].username.startsWith("oidc_"))

        val other = issueSession("test-" + UUID.randomUUID())
        assertNotEquals(sessions[0].userId, other.userId)
        assertEquals("USER", other.role)

        val google = issueSession("123456789", issuer = GOOGLE_ISSUER)
        val kakao = issueSession("123456789", issuer = KAKAO_ISSUER)
        assertNotEquals(google.userId, kakao.userId)
        assertEquals("USER", kakao.role)

        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.executeQuery("select to_regclass('public.google_oidc_identities') is null").use { result ->
                    assertTrue(result.next())
                    assertTrue(result.getBoolean(1))
                }
            }
            connection
                .prepareStatement("select count(*) from social_login_identities where subject = ?")
                .use { statement ->
                    statement.setString(1, "123456789")
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        assertEquals(2, result.getInt(1))
                    }
                }
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
    fun `only decision_auth can issue social sessions and Kakao cannot become an operator`() {
        val subject = "test-" + UUID.randomUUID()
        val wrongIssuer =
            assertThrows(SQLException::class.java) {
                issueSession(subject, issuer = "https://example.invalid")
            }
        assertEquals("42501", wrongIssuer.sqlState)
        val wrongRole =
            assertThrows(SQLException::class.java) {
                DriverManager.getConnection(postgres.jdbcUrl, "decision_app", "app-test").use { connection ->
                    connection.prepareStatement("select * from authenticate_social_login_actor_v1(?,?,?,?)").use { statement ->
                        statement.setString(1, GOOGLE_ISSUER)
                        statement.setString(2, subject)
                        statement.setBoolean(3, false)
                        statement.setInt(4, 3_600)
                        statement.executeQuery()
                    }
                }
            }
        assertEquals("42501", wrongRole.sqlState)

        val kakaoAdmin =
            assertThrows(SQLException::class.java) {
                issueSession("123456789", issuer = KAKAO_ISSUER, operatorSubject = true)
            }
        assertEquals("42501", kakaoAdmin.sqlState)
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
                    "select payload_json::text from audit_logs where user_id = ? and action = 'SOCIAL_LOGIN_ROLE_CHANGED'",
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
    fun `password account owns its data and can explicitly link both providers`() {
        val email = "link-${UUID.randomUUID()}@example.test"
        val password = "a".repeat(20)
        val passwordEncoder = BCryptPasswordEncoder(12)
        val passwordHash = requireNotNull(passwordEncoder.encode(password))
        val dummyHash = requireNotNull(passwordEncoder.encode("z".repeat(20)))
        val created = registerPasswordAccount(email, passwordHash)
        assertTrue(created.userId.startsWith("usr_"))
        assertTrue(created.username.startsWith("member_"))

        assertNull(authenticatePasswordAccount(email, "wrong-password", dummyHash))
        val passwordLogin = authenticatePasswordAccount(email, password, dummyHash)
        assertEquals(created.userId, passwordLogin?.userId)

        val googleSubject = "link-google-" + UUID.randomUUID()
        val kakaoSubject = (100000000000L + kotlin.math.abs(UUID.randomUUID().mostSignificantBits % 899999999999L)).toString()
        val googleLogin = linkSocialIdentity(created.userId, GOOGLE_ISSUER, googleSubject)
        val kakaoLogin = linkSocialIdentity(created.userId, KAKAO_ISSUER, kakaoSubject)
        assertEquals(created.userId, googleLogin.userId)
        assertEquals(created.userId, kakaoLogin.userId)

        val methods = authenticationMethods(created.userId)
        assertEquals(setOf("password", "google", "kakao"), methods.map { it.first }.toSet())
        assertEquals(email, methods.single { it.first == "password" }.second)

        val otherAccount =
            registerPasswordAccount(
                "other-${UUID.randomUUID()}@example.test",
                requireNotNull(passwordEncoder.encode(password)),
            )
        val linkedElsewhere =
            assertThrows(SQLException::class.java) {
                linkSocialIdentity(otherAccount.userId, GOOGLE_ISSUER, googleSubject)
            }
        assertEquals("23505", linkedElsewhere.sqlState)

        assertTrue(unlinkSocialIdentity(created.userId, GOOGLE_ISSUER))
        assertTrue(unlinkSocialIdentity(created.userId, KAKAO_ISSUER))
        assertEquals(setOf("password"), authenticationMethods(created.userId).map { it.first }.toSet())
    }

    @Test
    fun `social only account cannot unlink its last login method`() {
        val session = issueSession("unlink-last-" + UUID.randomUUID())
        val rejected =
            assertThrows(SQLException::class.java) {
                unlinkSocialIdentity(session.userId, GOOGLE_ISSUER)
            }
        assertEquals("23514", rejected.sqlState)
    }

    @Test
    fun `explicit provider link keeps demo user owner rows in place`() {
        val principleId = "manual-link-principle-" + UUID.randomUUID()
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection
                .prepareStatement(
                    "insert into principles(principle_id,user_id,preset_id,title,mode,status,current_version) " +
                        "values (?,'usr_demo_user','balanced','manual link preservation','GUIDE','ACTIVE',1)",
                ).use { statement ->
                    statement.setString(1, principleId)
                    assertEquals(1, statement.executeUpdate())
                }
        }

        val linked = linkSocialIdentity("usr_demo_user", GOOGLE_ISSUER, "manual-link-" + UUID.randomUUID())
        assertEquals("usr_demo_user", linked.userId)
        assertEquals("demo-user", linked.username)

        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.prepareStatement("select user_id from principles where principle_id = ?").use { statement ->
                statement.setString(1, principleId)
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    assertEquals("usr_demo_user", result.getString(1))
                    assertFalse(result.next())
                }
            }
        }
        assertTrue(authenticationMethods("usr_demo_user").any { it.first == "google" })
    }

    @Test
    fun `provider usage estimates record past the former limit without blocking users`() {
        val users = (1..2).map { issueSession("test-usage-user-" + UUID.randomUUID()) }

        fun record(
            role: String,
            password: String,
            owner: String?,
            source: String,
            provider: String,
            amount: Long,
            id: String = "aibr_" + UUID.randomUUID().toString().replace("-", ""),
        ): Boolean =
            DriverManager.getConnection(postgres.jdbcUrl, role, password).use { connection ->
                connection.prepareStatement("select record_operator_ai_gross_usage_v1(?,?,?,?,?)").use { statement ->
                    statement.setString(1, id)
                    statement.setString(2, owner)
                    statement.setString(3, source)
                    statement.setString(4, provider)
                    statement.setLong(5, amount)
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        result.getBoolean(1)
                    }
                }
            }

        val sameIdentity = "aibr_" + "1".repeat(32)
        assertTrue(record("decision_app", "app-test", users[0].userId, "FULL_AGENT", "VERTEX", 600_000_000, sameIdentity))
        assertTrue(record("decision_app", "app-test", users[0].userId, "FULL_AGENT", "VERTEX", 600_000_000, sameIdentity))
        assertTrue(record("decision_app", "app-test", users[1].userId, "FULL_AGENT", "VERTEX", 600_000_000))
        assertTrue(record("decision_rag_writer", "rag-writer-test", users[0].userId, "RAG_VOYAGE", "VOYAGE", 600_000_000))
        assertTrue(
            record(
                "decision_automation_runtime",
                "automation-runtime-test-0001",
                users[1].userId,
                "TRADE_AI",
                "VERTEX",
                600_000_000,
            ),
        )
        val wrongSource =
            assertThrows(SQLException::class.java) {
                record("decision_rag_writer", "rag-writer-test", users[0].userId, "FULL_AGENT", "VERTEX", 1)
            }
        assertEquals("42501", wrongSource.sqlState)

        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement
                    .executeQuery(
                        "SELECT count(*), sum(max_gross_microusd) FROM operator_ai_gross_usage_reservations",
                    ).use { result ->
                        assertTrue(result.next())
                        assertEquals(4L, result.getLong(1))
                        assertTrue(result.getLong(2) > 1_000_000L)
                    }
            }
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
            connection.prepareStatement("select * from authenticate_social_login_actor_v1(?,?,?,?)").use { statement ->
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

    private fun registerPasswordAccount(
        email: String,
        passwordHash: String,
    ): IssuedSession =
        DriverManager.getConnection(postgres.jdbcUrl, "decision_auth", "auth-test-secret-0001").use { connection ->
            connection.prepareStatement("select * from register_password_login_actor_v1(?,?,?)").use { statement ->
                statement.setString(1, email)
                statement.setString(2, passwordHash)
                statement.setInt(3, 3_600)
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    IssuedSession(
                        handle = result.getString("session_handle"),
                        userId = result.getString("actor_user_id"),
                        username = result.getString("username"),
                        role = result.getString("actor_role"),
                    )
                }
            }
        }

    private fun authenticatePasswordAccount(
        email: String,
        password: String,
        dummyHash: String,
    ): IssuedSession? =
        DriverManager.getConnection(postgres.jdbcUrl, "decision_auth", "auth-test-secret-0001").use { connection ->
            connection.prepareStatement("select * from authenticate_password_login_actor_v1(?,?,?,?)").use { statement ->
                statement.setString(1, email)
                statement.setString(2, password)
                statement.setString(3, dummyHash)
                statement.setInt(4, 3_600)
                statement.executeQuery().use { result ->
                    if (!result.next()) {
                        null
                    } else {
                        IssuedSession(
                            handle = result.getString("session_handle"),
                            userId = result.getString("actor_user_id"),
                            username = result.getString("username"),
                            role = result.getString("actor_role"),
                        )
                    }
                }
            }
        }

    private fun linkSocialIdentity(
        userId: String,
        issuer: String,
        subject: String,
    ): IssuedSession =
        DriverManager.getConnection(postgres.jdbcUrl, "decision_auth", "auth-test-secret-0001").use { connection ->
            connection.prepareStatement("select * from link_social_login_actor_v1(?,?,?,?,?)").use { statement ->
                statement.setString(1, userId)
                statement.setString(2, issuer)
                statement.setString(3, subject)
                statement.setBoolean(4, false)
                statement.setInt(5, 3_600)
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    IssuedSession(
                        handle = result.getString("session_handle"),
                        userId = result.getString("actor_user_id"),
                        username = result.getString("username"),
                        role = result.getString("actor_role"),
                    )
                }
            }
        }

    private fun authenticationMethods(userId: String): List<Pair<String, String?>> =
        DriverManager.getConnection(postgres.jdbcUrl, "decision_auth", "auth-test-secret-0001").use { connection ->
            connection.prepareStatement("select provider, email_normalized from read_account_auth_methods_v1(?)").use { statement ->
                statement.setString(1, userId)
                statement.executeQuery().use { result ->
                    buildList {
                        while (result.next()) add(result.getString("provider") to result.getString("email_normalized"))
                    }
                }
            }
        }

    private fun unlinkSocialIdentity(
        userId: String,
        issuer: String,
    ): Boolean =
        DriverManager.getConnection(postgres.jdbcUrl, "decision_auth", "auth-test-secret-0001").use { connection ->
            connection.prepareStatement("select unlink_social_login_actor_v1(?,?)").use { statement ->
                statement.setString(1, userId)
                statement.setString(2, issuer)
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    result.getBoolean(1)
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
        private const val KAKAO_ISSUER = "https://kauth.kakao.com"
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
