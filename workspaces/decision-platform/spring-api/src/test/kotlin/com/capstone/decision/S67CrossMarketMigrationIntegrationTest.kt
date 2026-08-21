package com.capstone.decision

import org.flywaydb.core.Flyway
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.TestInstance
import org.junit.jupiter.api.assertThrows
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import java.security.MessageDigest
import java.sql.Connection
import java.sql.DriverManager
import java.sql.SQLException
import java.time.Instant
import java.util.HexFormat
import java.util.UUID

@Testcontainers
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class S67CrossMarketMigrationIntegrationTest {
    @BeforeAll
    fun migrate() {
        Flyway
            .configure()
            .dataSource(postgres.jdbcUrl, postgres.username, postgres.password)
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

    @Test
    fun `writer function is idempotent immutable and owner scoped reader is point in time bounded`() {
        val semanticHash = semanticHash(SCORE)
        DriverManager.getConnection(postgres.jdbcUrl, "decision_market_writer", MARKET_WRITER_PASSWORD).use { writer ->
            assertEquals("INSERTED", append(writer, semanticHash, ARTIFACT_HASH))
            assertEquals("NO_OP", append(writer, semanticHash, ARTIFACT_HASH))
            val conflict = assertThrows<SQLException> { append(writer, semanticHash, "9".repeat(64)) }
            assertEquals("23505", conflict.sqlState)
            assertThrows<SQLException> {
                writer.createStatement().use { it.executeQuery("select * from cross_market_risk_snapshots_v2") }
            }
        }

        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { app ->
            app.autoCommit = false
            app.prepareStatement("select set_config('app.actor_user_id', ?, true)").use {
                it.setString(1, "usr_demo_user")
                it.executeQuery().use { result -> assertTrue(result.next()) }
            }
            assertEquals(0, readCount(app, AVAILABLE_AT.minusSeconds(1)))
            assertEquals(1, readCount(app, AVAILABLE_AT))
            app.rollback()
        }

        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { owner ->
            val immutable =
                assertThrows<SQLException> {
                    owner.createStatement().use {
                        it.executeUpdate("update cross_market_risk_snapshots_v2 set score = 99")
                    }
                }
            assertEquals("55000", immutable.sqlState)
            owner.createStatement().use { statement ->
                statement
                    .executeQuery(
                        "select has_table_privilege('decision_market_writer'," +
                            "'cross_market_risk_snapshots_v2','INSERT')",
                    ).use { result ->
                        assertTrue(result.next())
                        assertFalse(result.getBoolean(1))
                    }
            }
        }
    }

    private fun append(
        writer: Connection,
        semanticHash: String,
        artifactHash: String,
    ): String =
        writer
            .prepareStatement(
                """
                select append_cross_market_risk_snapshot_v2(
                  ?::uuid, ?, ?, ?, ?::timestamptz, ?::timestamptz,
                  ?, ?, ?, ?, ?, ?::numeric, ?::numeric, ?, ?, ?,
                  ?::timestamptz, ?, ?, ?, ?, ?
                )
                """.trimIndent(),
            ).use { statement ->
                val values =
                    listOf(
                        SNAPSHOT_ID.toString(),
                        "usr_demo_user",
                        OWNER_SCOPE,
                        SYMBOL,
                        AVAILABLE_AT.toString(),
                        STALE_AT.toString(),
                        "SYNTHETIC_FIXTURE",
                        "STORED_SNAPSHOT",
                        "WARN_ONLY",
                        "AVAILABLE",
                        "PASS",
                        SCORE,
                        THRESHOLD,
                        THRESHOLD_HASH,
                        CONFIG_HASH,
                        "NEW_BUY",
                        AVAILABLE_AT.toString(),
                        EXPOSURE_HASH,
                        semanticHash,
                        artifactHash,
                        "{}",
                        "{}",
                    )
                values.forEachIndexed { index, value -> statement.setObject(index + 1, value) }
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    result.getString(1)
                }
            }

    private fun readCount(
        connection: Connection,
        evaluationAsOf: Instant,
    ): Int =
        connection
            .prepareStatement("select count(*) from read_cross_market_decision_input_v2(?, ?, ?::timestamptz)")
            .use { statement ->
                statement.setString(1, OWNER_SCOPE)
                statement.setString(2, SYMBOL)
                statement.setString(3, evaluationAsOf.toString())
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    result.getInt(1)
                }
            }

    private fun semanticHash(score: String): String {
        val preimage =
            listOf(
                "s6-cross-market-semantic-v2",
                SYMBOL,
                "2026-08-21T08:09:00.000000Z",
                "2026-08-22T08:09:00.000000Z",
                score,
                THRESHOLD,
                THRESHOLD_HASH,
                CONFIG_HASH,
                "NEW_BUY",
                EXPOSURE_HASH,
            ).joinToString("\n")
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(preimage.toByteArray()))
    }

    companion object {
        private const val APP_PASSWORD = "app-test"
        private const val MARKET_WRITER_PASSWORD = "market-writer-test"
        private const val SYMBOL = "005930"
        private const val SCORE = "98.125"
        private const val THRESHOLD = "97.5"
        private val AVAILABLE_AT = Instant.parse("2026-08-21T08:09:00Z")
        private val STALE_AT = Instant.parse("2026-08-22T08:09:00Z")
        private val SNAPSHOT_ID = UUID.fromString("11111111-1111-4111-8111-111111111111")
        private val OWNER_SCOPE = "2".repeat(64)
        private val THRESHOLD_HASH = "1".repeat(64)
        private val CONFIG_HASH = "3".repeat(64)
        private val EXPOSURE_HASH = "4".repeat(64)
        private val ARTIFACT_HASH = "5".repeat(64)
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
                .withDatabaseName("decision")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")
    }
}
