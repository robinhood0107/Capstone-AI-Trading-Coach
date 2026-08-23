package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.flywaydb.core.Flyway
import org.junit.jupiter.api.Test
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import java.sql.Connection
import java.sql.DriverManager

// 이미 original V37을 기록한 DB도 additive V41 이후 validate해야 한다. migration bytes의 재작성은 허용하지 않는다.
@Testcontainers
class RagExternalExact30VoyageFlywayLifecycleIntegrationTest {
    @Test
    fun `historical V37 database validates and receives V41 canonical hash hardening`() {
        flyway(target = "37").migrate()

        adminConnection().use { connection ->
            assertThat(
                queryString(
                    connection,
                    "select checksum::text from flyway_schema_history where version = '37' and success",
                ),
            ).isEqualTo("-619486304")
        }

        val full = flyway()
        full.migrate()
        full.validate()

        adminConnection().use { connection ->
            assertThat(
                queryString(
                    connection,
                    "select count(*)::text from rag_v2_immutable_external_exact30_source_allowlist",
                ),
            ).isEqualTo("30")
            assertThat(
                queryString(
                    connection,
                    """
                    select count(*)::text
                    from rag_v2_immutable_external_exact30_source_allowlist
                    where canonical_text_sha256 ~ '^[0-9a-f]{64}$'
                    """.trimIndent(),
                ),
            ).isEqualTo("30")
        }
    }

    private fun flyway(target: String? = null): Flyway {
        val configuration =
            Flyway
                .configure()
                .dataSource(postgres.jdbcUrl, "flyway", FLYWAY_PASSWORD)
                .locations("classpath:db/migration")
                .placeholders(
                    mapOf(
                        "brokerageDbCapabilityTokenSha256" to
                            SpringApiIntegrationTestBase.TEST_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256,
                    ),
                ).javaMigrations(s21ActorTrustMigration())
        target?.let(configuration::target)
        return configuration.load()
    }

    private fun adminConnection(): Connection = DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password)

    private fun queryString(
        connection: Connection,
        sql: String,
    ): String =
        connection.createStatement().use { statement ->
            statement.executeQuery(sql).use { result ->
                check(result.next())
                result.getString(1)
            }
        }

    private companion object {
        const val FLYWAY_PASSWORD = "flyway-test"
        val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            stablePostgresContainer(postgresImage)
                .withDatabaseName("decision_v37_lifecycle")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")
    }
}
