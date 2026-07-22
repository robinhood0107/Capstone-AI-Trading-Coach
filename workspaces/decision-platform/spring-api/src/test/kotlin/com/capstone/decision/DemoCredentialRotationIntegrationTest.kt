package com.capstone.decision

import com.capstone.decision.infrastructure.security.DemoCredentialRotation
import org.flywaydb.core.Flyway
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
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

// credential rotation은 runtime app 권한과 분리된 migration role transaction으로만 실행한다.
@Testcontainers
class DemoCredentialRotationIntegrationTest {
    private val passwordEncoder = BCryptPasswordEncoder(12)

    @BeforeEach
    fun restoreUser() {
        adminConnection().use { connection ->
            connection
                .prepareStatement(
                    """
                    update users
                    set password_hash = ?, security_version = 1, status = 'ACTIVE', role = 'USER', updated_at = now()
                    where user_id = 'usr_demo_user'
                    """.trimIndent(),
                ).use { statement ->
                    statement.setString(1, SpringApiIntegrationTestBase.TEST_USER_PASSWORD_HASH)
                    assertEquals(1, statement.executeUpdate())
                }
            connection.createStatement().use { statement ->
                statement.executeUpdate("delete from audit_logs where action = 'DEMO_CREDENTIAL_ROTATED'")
            }
        }
    }

    @AfterEach
    fun cleanAudit() {
        adminConnection().use { connection ->
            connection.createStatement().use { statement ->
                statement.executeUpdate("delete from audit_logs where action = 'DEMO_CREDENTIAL_ROTATED'")
            }
        }
    }

    @Test
    fun `rotation changes one allowlisted hash increments security version and writes sanitized audit`() {
        val newPassword = "n" + "p".repeat(16)
        val newHash = requireNotNull(passwordEncoder.encode(newPassword))

        DemoCredentialRotation.rotate(environment(newHash), auditId = "aud-rotation-success")

        adminConnection().use { connection ->
            connection
                .prepareStatement(
                    "select password_hash, security_version from users where user_id = 'usr_demo_user'",
                ).use { statement ->
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        val storedHash = requireNotNull(result.getString("password_hash"))
                        assertFalse(passwordEncoder.matches(SpringApiIntegrationTestBase.TEST_USER_PASSWORD, storedHash))
                        assertTrue(passwordEncoder.matches(newPassword, storedHash))
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
                        assertFalse(payload.contains(newHash))
                        assertFalse(payload.contains(newPassword))
                        assertFalse(payload.contains("change-ticket-36"))
                    }
                }
        }
    }

    @Test
    fun `audit failure rolls back password and version while invalid target is rejected before SQL`() {
        val newHash = requireNotNull(passwordEncoder.encode("another-runtime-password"))
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
            DemoCredentialRotation.rotate(environment(newHash), auditId = "aud-rotation-conflict")
        }

        adminConnection().use { connection ->
            connection
                .prepareStatement(
                    "select password_hash, security_version from users where user_id = 'usr_demo_user'",
                ).use { statement ->
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        assertEquals(
                            SpringApiIntegrationTestBase.TEST_USER_PASSWORD_HASH,
                            result.getString("password_hash"),
                        )
                        assertEquals(1L, result.getLong("security_version"))
                    }
                }
        }

        assertThrows<IllegalArgumentException> {
            DemoCredentialRotation.rotate(
                environment(newHash) + ("DEMO_CREDENTIAL_USER_ID" to "usr_unapproved"),
                auditId = "aud-never-written",
            )
        }
    }

    private fun environment(newHash: String): Map<String, String> =
        mapOf(
            "POSTGRES_HOST" to postgres.host,
            "POSTGRES_PORT" to postgres.getMappedPort(5432).toString(),
            "POSTGRES_DB" to postgres.databaseName,
            "POSTGRES_MIGRATION_PASSWORD" to MIGRATION_PASSWORD,
            "DEMO_CREDENTIAL_USER_ID" to "usr_demo_user",
            "DEMO_CREDENTIAL_PASSWORD_HASH" to newHash,
            "DEMO_CREDENTIAL_ROTATION_ACTOR" to "change-ticket-36",
        )

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
                        "demoUserPasswordHash" to SpringApiIntegrationTestBase.TEST_USER_PASSWORD_HASH,
                        "demoAdminPasswordHash" to SpringApiIntegrationTestBase.TEST_ADMIN_PASSWORD_HASH,
                    ),
                ).load()
                .migrate()
        }
    }
}
