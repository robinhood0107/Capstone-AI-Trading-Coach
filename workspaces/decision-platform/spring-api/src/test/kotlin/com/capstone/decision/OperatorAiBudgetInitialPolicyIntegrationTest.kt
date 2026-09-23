package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
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

/** Replays V200..V203 with the restricted migration login, then checks the first operator policy. */
@Testcontainers
@SpringBootTest(properties = ["spring.autoconfigure.exclude=org.springframework.boot.kafka.autoconfigure.KafkaAutoConfiguration"])
class OperatorAiBudgetInitialPolicyIntegrationTest : SpringApiIntegrationTestBase() {
    @Test
    fun `fresh policy starts at the chosen dollar and temporary migration access is removed`() {
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.executeQuery("SELECT daily_soft_cap_microusd, revision FROM operator_ai_budget_policy").use { row ->
                    assertTrue(row.next())
                    assertEquals(1_000_000L, row.getLong(1))
                    assertEquals(2L, row.getLong(2))
                    assertFalse(row.next())
                }
                statement
                    .executeQuery(
                        "SELECT EXISTS(SELECT 1 FROM pg_policies WHERE schemaname='public' AND policyname='operator_ai_budget_seed_v203')",
                    ).use { row ->
                        assertTrue(row.next())
                        assertFalse(row.getBoolean(1))
                    }
            }
        }
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
                .withDatabaseName("decision_ai_budget_first")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")

        @DynamicPropertySource
        @JvmStatic
        fun properties(registry: DynamicPropertyRegistry) {
            registry.add("spring.datasource.url", postgres::getJdbcUrl)
            registry.add("spring.datasource.username") { "decision_app" }
            registry.add("spring.datasource.password") { "app-test" }
            registry.add("spring.flyway.user") { "flyway" }
            registry.add("spring.flyway.password") { "flyway-test" }
        }
    }
}
