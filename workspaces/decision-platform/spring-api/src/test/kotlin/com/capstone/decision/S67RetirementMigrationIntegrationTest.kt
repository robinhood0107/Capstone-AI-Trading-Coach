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
class S67RetirementMigrationIntegrationTest {
    @BeforeAll
    fun migrateAndRetire() {
        flyway("78").migrate()
        DriverManager.getConnection(postgres.jdbcUrl, "decision_market_writer", MARKET_WRITER_PASSWORD).use { writer ->
            assertEquals("INSERTED", appendHistoricalRow(writer))
        }
        flyway().migrate()
    }

    @Test
    fun `V86 preserves V84 immutable audit row and removed reader writer capabilities`() {
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { owner ->
            owner.createStatement().use { statement ->
                statement.executeQuery("select version from flyway_schema_history order by installed_rank").use { result ->
                    val versions = mutableListOf<String>()
                    while (result.next()) versions += result.getString(1)
                    assertEquals((1..116).map(Int::toString), versions)
                }
                statement.executeQuery("select count(*), min(artifact_hash) from cross_market_risk_snapshots_v2").use { result ->
                    assertTrue(result.next())
                    assertEquals(1, result.getInt(1))
                    assertEquals(ARTIFACT_HASH, result.getString(2))
                }
                statement
                    .executeQuery(
                        "select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace " +
                            "where n.nspname='public' and p.proname in " +
                            "('append_cross_market_risk_snapshot_v2','read_cross_market_decision_input_v2')",
                    ).use { result ->
                        assertTrue(result.next())
                        assertEquals(0, result.getInt(1))
                    }
                for (role in listOf("decision_market_writer", "decision_app")) {
                    statement
                        .executeQuery(
                            "select has_table_privilege('$role','cross_market_risk_snapshots_v2','SELECT')," +
                                "has_table_privilege('$role','cross_market_risk_snapshots_v2','INSERT')," +
                                "has_table_privilege('$role','cross_market_risk_snapshots_v2','UPDATE')," +
                                "has_table_privilege('$role','cross_market_risk_snapshots_v2','DELETE')",
                        ).use { result ->
                            assertTrue(result.next())
                            (1..4).forEach { column -> assertFalse(result.getBoolean(column)) }
                        }
                }
            }
            val immutable =
                assertThrows<SQLException> {
                    owner.createStatement().use {
                        it.executeUpdate("update cross_market_risk_snapshots_v2 set score = 99")
                    }
                }
            assertEquals("55000", immutable.sqlState)
        }

        DriverManager.getConnection(postgres.jdbcUrl, "decision_market_writer", MARKET_WRITER_PASSWORD).use { writer ->
            val unavailable = assertThrows<SQLException> { appendHistoricalRow(writer) }
            assertEquals("42883", unavailable.sqlState)
        }
        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { app ->
            val denied =
                assertThrows<SQLException> {
                    app.createStatement().use { it.executeQuery("select * from cross_market_risk_snapshots_v2") }
                }
            assertEquals("42501", denied.sqlState)
        }
    }

    private fun flyway(target: String? = null): Flyway {
        val configuration =
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
        if (target != null) configuration.target(target)
        return configuration.load()
    }

    private fun appendHistoricalRow(writer: Connection): String =
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
                        semanticHash(),
                        ARTIFACT_HASH,
                        "{}",
                        "{}",
                    )
                values.forEachIndexed { index, value -> statement.setObject(index + 1, value) }
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    result.getString(1)
                }
            }

    private fun semanticHash(): String {
        val preimage =
            listOf(
                "s6-cross-market-semantic-v2",
                SYMBOL,
                canonicalInstant(AVAILABLE_AT),
                canonicalInstant(STALE_AT),
                SCORE,
                THRESHOLD,
                THRESHOLD_HASH,
                CONFIG_HASH,
                "NEW_BUY",
                EXPOSURE_HASH,
            ).joinToString("\n")
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(preimage.toByteArray()))
    }

    private fun canonicalInstant(value: Instant): String = value.toString().replace("Z", ".000000Z")

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
            stablePostgresContainer(postgresImage)
                .withDatabaseName("decision")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")
    }
}
