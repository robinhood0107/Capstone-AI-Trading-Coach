package com.capstone.decision

import com.capstone.decision.infrastructure.security.DemoAccountService
import com.capstone.decision.infrastructure.security.DemoCredentialRotation
import com.capstone.decision.infrastructure.security.DemoRole
import com.capstone.decision.infrastructure.security.JwtProperties
import com.capstone.decision.infrastructure.security.JwtService
import com.capstone.decision.infrastructure.security.UserSecurityActorRecord
import com.capstone.decision.infrastructure.security.UserSecurityRecord
import com.capstone.decision.infrastructure.security.UserSecurityRepository
import io.jsonwebtoken.JwtException
import org.flywaydb.core.Flyway
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import java.sql.DriverManager
import java.util.Base64
import java.util.HexFormat

// credential rotation은 runtime app 권한과 분리된 migration role transaction으로만 실행한다.
@Testcontainers
class DemoCredentialRotationIntegrationTest {
    private val passwordEncoder = BCryptPasswordEncoder(12)

    @BeforeEach
    fun restoreUser() {
        setFlywayLoggingPolicy(logStatement = "none", parameterMaxLength = 0, errorParameterMaxLength = 0)
        adminConnection().use { connection ->
            connection
                .prepareStatement(
                    """
                    update users
                    set password_hash = ?, credential_reuse_tag = ?, credential_bundle_mac = ?,
                        credential_policy_version = 1, security_version = 1,
                        status = 'ACTIVE', role = 'USER', updated_at = now()
                    where user_id = 'usr_demo_user'
                    """.trimIndent(),
                ).use { statement ->
                    statement.setString(1, SpringApiIntegrationTestBase.TEST_USER_PASSWORD_HASH)
                    statement.setBytes(2, SpringApiIntegrationTestBase.TEST_USER_VERIFIED_BUNDLE.reuseTag)
                    statement.setBytes(3, SpringApiIntegrationTestBase.TEST_USER_VERIFIED_BUNDLE.bundleMac)
                    assertEquals(1, statement.executeUpdate())
                }
            connection.createStatement().use { statement ->
                statement.executeUpdate("delete from audit_logs where action = 'DEMO_CREDENTIAL_ROTATED'")
            }
        }
    }

    @AfterEach
    fun cleanAudit() {
        setFlywayLoggingPolicy(logStatement = "none", parameterMaxLength = 0, errorParameterMaxLength = 0)
        adminConnection().use { connection ->
            connection.createStatement().use { statement ->
                statement.executeUpdate("delete from audit_logs where action = 'DEMO_CREDENTIAL_ROTATED'")
            }
        }
    }

    @Test
    fun `rotation changes one allowlisted hash increments security version and writes sanitized audit`() {
        val newPassword = "n" + "p".repeat(16)
        val newBundle = SpringApiIntegrationTestBase.prepareTestBundle("usr_demo_user", newPassword)

        DemoCredentialRotation.rotate(environment(newBundle), auditId = "aud-rotation-success")

        adminConnection().use { connection ->
            connection
                .prepareStatement(
                    """
                    select password_hash, credential_reuse_tag, credential_bundle_mac,
                           credential_policy_version, security_version
                    from users where user_id = 'usr_demo_user'
                    """.trimIndent(),
                ).use { statement ->
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        val storedHash = requireNotNull(result.getString("password_hash"))
                        assertFalse(passwordEncoder.matches(SpringApiIntegrationTestBase.TEST_USER_PASSWORD, storedHash))
                        assertTrue(passwordEncoder.matches(newPassword, storedHash))
                        assertEquals(32, result.getBytes("credential_reuse_tag").size)
                        assertEquals(32, result.getBytes("credential_bundle_mac").size)
                        assertEquals(1, result.getInt("credential_policy_version"))
                        assertEquals(2L, result.getLong("security_version"))
                        assertFalse(result.next())
                    }
                }
            connection
                .prepareStatement(
                    "select user_id, action, target_type, target_id, payload_json::text from audit_logs where audit_log_id = ?",
                ).use { statement ->
                    statement.setString(1, "aud-rotation-success")
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        assertEquals("usr_demo_user", result.getString("user_id"))
                        assertEquals("DEMO_CREDENTIAL_ROTATED", result.getString("action"))
                        assertEquals("USER", result.getString("target_type"))
                        assertEquals("usr_demo_user", result.getString("target_id"))
                        val payload = requireNotNull(result.getString("payload_json"))
                        assertFalse(payload.contains(newBundle))
                        assertFalse(payload.contains(storedCredentialTagHex("usr_demo_user")))
                        assertFalse(payload.contains(newPassword))
                        assertFalse(payload.contains("change-ticket-36"))
                    }
                }
        }
    }

    @Test
    fun `rotation immediately rejects old password and token while accepting the new password`() {
        val repository = testRepository()
        val accountService = DemoAccountService(repository, passwordEncoder)
        val jwtService =
            JwtService(
                JwtProperties(
                    secret = "j" + "s".repeat(63),
                    issuer = "rotation-test-issuer",
                    audience = "rotation-test-audience",
                ),
                repository,
            )
        val oldAccount = requireNotNull(accountService.authenticate("demo-user", SpringApiIntegrationTestBase.TEST_USER_PASSWORD))
        val oldToken = jwtService.issue(oldAccount).token
        val newPassword = "rotated-" + "p".repeat(24)
        val newBundle = SpringApiIntegrationTestBase.prepareTestBundle("usr_demo_user", newPassword)

        DemoCredentialRotation.rotate(environment(newBundle), auditId = "aud-rotation-auth-cutover")

        assertNull(accountService.authenticate("demo-user", SpringApiIntegrationTestBase.TEST_USER_PASSWORD))
        val newAccount = accountService.authenticate("demo-user", newPassword)
        assertNotNull(newAccount)
        assertThrows<JwtException> { jwtService.parse(oldToken) }
        val newPrincipal = jwtService.parse(jwtService.issue(requireNotNull(newAccount)).token)
        assertEquals("usr_demo_user", newPrincipal.userId)
        assertEquals(2L, newPrincipal.securityVersion)
    }

    @Test
    fun `rotation rejects peer plaintext under a fresh salt and exact replay without mutation`() {
        val peerPlaintextBundle =
            SpringApiIntegrationTestBase.prepareTestBundle(
                "usr_demo_user",
                SpringApiIntegrationTestBase.TEST_ADMIN_PASSWORD,
            )

        assertThrows<IllegalStateException> {
            DemoCredentialRotation.rotate(environment(peerPlaintextBundle), auditId = "aud-peer-password-rejected")
        }
        assertEquals(1L, queryUser("usr_demo_user").securityVersion)

        val newBundle = SpringApiIntegrationTestBase.prepareTestBundle("usr_demo_user", "one-shot-password")
        DemoCredentialRotation.rotate(environment(newBundle), auditId = "aud-one-shot-success")
        assertThrows<IllegalStateException> {
            DemoCredentialRotation.rotate(environment(newBundle), auditId = "aud-one-shot-replay")
        }

        assertEquals(2L, queryUser("usr_demo_user").securityVersion)
        adminConnection().use { connection ->
            connection.prepareStatement("select count(*) from audit_logs where action = 'DEMO_CREDENTIAL_ROTATED'").use { statement ->
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    assertEquals(1, result.getInt(1))
                }
            }
        }
    }

    @Test
    fun `rotation refuses non-loopback database targets before opening a connection`() {
        val newBundle = SpringApiIntegrationTestBase.prepareTestBundle("usr_demo_user", "remote-target-password")

        assertThrows<IllegalArgumentException> {
            DemoCredentialRotation.rotate(
                environment(newBundle) + ("POSTGRES_HOST" to "db.example.invalid"),
                auditId = "aud-remote-rejected",
            )
        }
    }

    @Test
    fun `rotation fails promptly when another operator transaction holds the demo credential lock`() {
        val newBundle = SpringApiIntegrationTestBase.prepareTestBundle("usr_demo_user", "concurrent-rotation-password")
        adminConnection().use { blocker ->
            blocker.autoCommit = false
            blocker.createStatement().use { statement ->
                statement.executeQuery("select user_id from users where user_id = 'usr_demo_user' for update").use { result ->
                    assertTrue(result.next())
                }
            }
            val startedAt = System.nanoTime()
            try {
                assertThrows<Exception> {
                    DemoCredentialRotation.rotate(environment(newBundle), auditId = "aud-concurrent-rejected")
                }
                val elapsedMillis = (System.nanoTime() - startedAt) / 1_000_000
                assertTrue(elapsedMillis < 5_000, "rotation lock failure took ${elapsedMillis}ms")
            } finally {
                blocker.rollback()
            }
        }

        assertEquals(1L, queryUser("usr_demo_user").securityVersion)
    }

    @Test
    fun `audit failure rolls back the whole credential bundle while invalid target is rejected before SQL`() {
        val newBundle = SpringApiIntegrationTestBase.prepareTestBundle("usr_demo_user", "another-runtime-password")
        adminConnection().use { connection ->
            connection
                .prepareStatement(
                    """
                    insert into audit_logs (audit_log_id, action, target_type)
                    values ('aud-rotation-conflict', 'TEST_SENTINEL', 'USER')
                    """.trimIndent(),
                ).use { it.executeUpdate() }
        }

        assertThrows<Exception> {
            DemoCredentialRotation.rotate(environment(newBundle), auditId = "aud-rotation-conflict")
        }

        adminConnection().use { connection ->
            connection
                .prepareStatement(
                    """
                    select password_hash, credential_reuse_tag, credential_bundle_mac,
                           credential_policy_version, security_version
                    from users where user_id = 'usr_demo_user'
                    """.trimIndent(),
                ).use { statement ->
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        assertEquals(
                            SpringApiIntegrationTestBase.TEST_USER_PASSWORD_HASH,
                            result.getString("password_hash"),
                        )
                        assertTrue(
                            result
                                .getBytes("credential_reuse_tag")
                                .contentEquals(SpringApiIntegrationTestBase.TEST_USER_VERIFIED_BUNDLE.reuseTag),
                        )
                        assertTrue(
                            result
                                .getBytes("credential_bundle_mac")
                                .contentEquals(SpringApiIntegrationTestBase.TEST_USER_VERIFIED_BUNDLE.bundleMac),
                        )
                        assertEquals(1, result.getInt("credential_policy_version"))
                        assertEquals(1L, result.getLong("security_version"))
                    }
                }
        }

        assertThrows<IllegalArgumentException> {
            DemoCredentialRotation.rotate(
                environment(newBundle) + ("DEMO_CREDENTIAL_USER_ID" to "usr_unapproved"),
                auditId = "aud-never-written",
            )
        }
    }

    @Test
    fun `rotation permits statement logging when credential bind values are suppressed`() {
        val newPassword = "logging-safe-" + "p".repeat(16)
        val newBundle = SpringApiIntegrationTestBase.prepareTestBundle("usr_demo_user", newPassword)
        setFlywayLoggingPolicy(logStatement = "all", parameterMaxLength = 0, errorParameterMaxLength = 0)
        val logOffset = postgres.logs.length

        DemoCredentialRotation.rotate(environment(newBundle), auditId = "aud-rotation-logging-safe")

        val rotationLogs = postgres.logs.drop(logOffset)
        assertTrue(rotationLogs.contains("update users"), "statement logging control did not observe the rotation SQL")
        assertCredentialEvidenceAbsent(rotationLogs, newPassword, newBundle)
        assertEquals(2L, queryUser("usr_demo_user").securityVersion)
        assertEquals(1, queryRotationAuditCount())
    }

    @Test
    fun `rotation fails closed before credential binds when parameter logging is unsafe`() {
        val newPassword = "logging-unsafe-" + "p".repeat(16)
        val newBundle = SpringApiIntegrationTestBase.prepareTestBundle("usr_demo_user", newPassword)
        setFlywayLoggingPolicy(logStatement = "all", parameterMaxLength = -1, errorParameterMaxLength = -1)
        val logOffset = postgres.logs.length

        assertThrows<IllegalStateException> {
            DemoCredentialRotation.rotate(environment(newBundle), auditId = "aud-rotation-logging-rejected")
        }

        assertCredentialEvidenceAbsent(postgres.logs.drop(logOffset), newPassword, newBundle)
        assertEquals(1L, queryUser("usr_demo_user").securityVersion)
        assertEquals(0, queryRotationAuditCount())
    }

    private fun environment(newBundle: String): Map<String, String> =
        mapOf(
            "POSTGRES_HOST" to postgres.host,
            "POSTGRES_PORT" to postgres.getMappedPort(5432).toString(),
            "POSTGRES_DB" to postgres.databaseName,
            "POSTGRES_MIGRATION_PASSWORD" to MIGRATION_PASSWORD,
            "DEMO_CREDENTIAL_USER_ID" to "usr_demo_user",
            "DEMO_CREDENTIAL_BUNDLE" to newBundle,
            "DEMO_CREDENTIAL_SEPARATION_KEY" to SpringApiIntegrationTestBase.TEST_CREDENTIAL_SEPARATION_KEY,
            "DEMO_CREDENTIAL_ROTATION_ACTOR" to "change-ticket-36",
        )

    private fun testRepository(): UserSecurityRepository =
        object : UserSecurityRepository {
            override fun findDemoCredentials(): List<UserSecurityRecord> =
                listOfNotNull(
                    queryUserBy("user_id", "usr_demo_user"),
                    queryUserBy("user_id", "usr_demo_admin"),
                )

            override fun findByUserId(userId: String): UserSecurityActorRecord? =
                queryUserBy("user_id", userId)?.let { user ->
                    UserSecurityActorRecord(
                        userId = user.userId,
                        username = user.username,
                        role = user.role,
                        status = user.status,
                        securityVersion = user.securityVersion,
                    )
                }
        }

    private fun queryUser(userId: String): UserSecurityRecord = requireNotNull(queryUserBy("user_id", userId))

    private fun storedCredentialTagHex(userId: String): String =
        adminConnection().use { connection ->
            connection
                .prepareStatement("select encode(credential_reuse_tag, 'hex') from users where user_id = ?")
                .use { statement ->
                    statement.setString(1, userId)
                    statement.executeQuery().use { result ->
                        check(result.next())
                        result.getString(1)
                    }
                }
        }

    private fun queryRotationAuditCount(): Int =
        adminConnection().use { connection ->
            connection.prepareStatement("select count(*) from audit_logs where action = 'DEMO_CREDENTIAL_ROTATED'").use { statement ->
                statement.executeQuery().use { result ->
                    check(result.next())
                    result.getInt(1)
                }
            }
        }

    private fun setFlywayLoggingPolicy(
        logStatement: String,
        parameterMaxLength: Int,
        errorParameterMaxLength: Int,
    ) {
        require(logStatement in setOf("none", "all"))
        require(parameterMaxLength in setOf(-1, 0))
        require(errorParameterMaxLength in setOf(-1, 0))
        adminConnection().use { connection ->
            connection.createStatement().use { statement ->
                // 테스트 allowlist로만 역할 기본값을 바꿔 실제 rotation 세션의 effective GUC를 검증한다.
                statement.execute("alter role flyway set log_statement = '$logStatement'")
                statement.execute("alter role flyway set log_parameter_max_length = $parameterMaxLength")
                statement.execute("alter role flyway set log_parameter_max_length_on_error = $errorParameterMaxLength")
            }
        }
    }

    private fun assertCredentialEvidenceAbsent(
        logs: String,
        plaintext: String,
        bundle: String,
    ) {
        val segments = bundle.split(':')
        check(segments.size == 5)
        val tag = Base64.getUrlDecoder().decode(segments[2])
        val mac = Base64.getUrlDecoder().decode(segments[4])
        try {
            val forbiddenEvidence =
                listOf(
                    plaintext,
                    bundle,
                    segments[2],
                    segments[3],
                    segments[4],
                    HexFormat.of().formatHex(tag),
                    HexFormat.of().formatHex(mac),
                )
            forbiddenEvidence.forEachIndexed { index, evidence ->
                assertFalse(logs.contains(evidence), "credential evidence index $index appeared in PostgreSQL logs")
            }
        } finally {
            tag.fill(0)
            mac.fill(0)
        }
    }

    private fun queryUserBy(
        column: String,
        value: String,
    ): UserSecurityRecord? {
        require(column in setOf("user_id", "username"))
        adminConnection().use { connection ->
            connection
                .prepareStatement(
                    "select user_id, username, password_hash, role, status, security_version from users where $column = ?",
                ).use { statement ->
                    statement.setString(1, value)
                    statement.executeQuery().use { result ->
                        if (!result.next()) return null
                        val user =
                            UserSecurityRecord(
                                userId = result.getString("user_id"),
                                username = result.getString("username"),
                                passwordHash = result.getString("password_hash"),
                                role = DemoRole.valueOf(result.getString("role")),
                                status = result.getString("status"),
                                securityVersion = result.getLong("security_version"),
                            )
                        check(!result.next())
                        return user
                    }
                }
        }
    }

    private fun adminConnection() = DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password)

    companion object {
        private val MIGRATION_PASSWORD: String = "m" + "p".repeat(24)
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            PostgreSQLContainer(postgresImage)
                .withDatabaseName("decision_rotation")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")

        @BeforeAll
        @JvmStatic
        fun migrateAsOperatorRole() {
            DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
                connection.createStatement().use { statement ->
                    // extension bootstrap은 superuser 책임이고 Flyway role은 schema migration만 수행한다.
                    statement.execute("create extension if not exists vector")
                    statement.execute("create extension if not exists pg_trgm")
                    statement.execute("alter role flyway login password '$MIGRATION_PASSWORD'")
                    statement.execute("grant create on schema public to flyway")
                }
            }
            Flyway
                .configure()
                .dataSource(postgres.jdbcUrl, "flyway", MIGRATION_PASSWORD)
                .locations("classpath:db/migration")
                .placeholders(
                    mapOf(
                        "brokerageDbCapabilityTokenSha256" to
                            SpringApiIntegrationTestBase.TEST_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256,
                    ),
                ).javaMigrations(s21ActorTrustMigration())
                .load()
                .migrate()
        }
    }
}
