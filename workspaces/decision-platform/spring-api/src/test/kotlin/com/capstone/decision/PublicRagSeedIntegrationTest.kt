package com.capstone.decision

import org.flywaydb.core.Flyway
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.TestInstance
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import tools.jackson.databind.json.JsonMapper
import java.nio.charset.StandardCharsets
import java.nio.file.Path
import java.sql.DriverManager
import java.util.concurrent.TimeUnit

@Testcontainers
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class PublicRagSeedIntegrationTest {
    private var baselineUserCount = -1

    @BeforeAll
    fun migrateFreshTarget() {
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
            .load()
            .migrate()
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.executeQuery("select count(*) from users").use { rows ->
                    assertTrue(rows.next())
                    baselineUserCount = rows.getInt(1)
                }
            }
        }
    }

    @Test
    fun `public seed restores a fresh V91 target and is idempotent`() {
        val first = runImporter()
        assertEquals("IMPORTED_FULL_READY", first["status"])
        assertEquals(142, first["sourceCount"])
        assertEquals(7871, first["chunkCount"])
        assertEquals(2, first["partCount"])

        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement
                    .executeQuery(
                        """
                        with pointer as (
                          select exact30_generation_id as generation_id
                          from rag_v2_immutable_public_bundle_pointers where state_id='default'
                          union all
                          select oa112_generation_id
                          from rag_v2_immutable_public_bundle_pointers where state_id='default'
                        )
                        select
                          (select version from flyway_schema_history where success
                           order by installed_rank desc limit 1),
                          (select state from rag_v2_immutable_public_bundle_pointers where state_id='default'),
                          (select count(*) from rag_v2_immutable_source_revisions where owner_user_id is null),
                          (select count(*) from rag_v2_immutable_chunks where owner_user_id is null),
                          (select count(*) from rag_v2_immutable_generation_embeddings
                           where component_generation_id in (select generation_id from pointer)),
                          (select count(*) from rag_v2_immutable_oa_source_cards),
                          (select count(*) from users),
                          (select count(*) from rag_v2_immutable_source_revisions
                           where owner_user_id is not null or source_scope='OWNER_PRIVATE'),
                          (select count(*) from rag_answers_v2_legacy),
                          (select count(*) from rag_v2_immutable_vertex_usage_reservations),
                          (select count(*) from rag_v2_immutable_voyage_usage_reservations)
                        """.trimIndent(),
                    ).use { rows ->
                        assertTrue(rows.next())
                        assertEquals("106", rows.getString(1))
                        assertEquals("ACTIVE", rows.getString(2))
                        assertEquals(142, rows.getInt(3))
                        assertEquals(7871, rows.getInt(4))
                        assertEquals(7871, rows.getInt(5))
                        assertEquals(112, rows.getInt(6))
                        assertEquals(baselineUserCount, rows.getInt(7))
                        assertEquals(0, rows.getInt(8))
                        assertEquals(0, rows.getInt(9))
                        assertEquals(0, rows.getInt(10))
                        assertEquals(0, rows.getInt(11))
                    }
            }
        }

        val second = runImporter()
        assertEquals("NOOP_MATCHING_ACTIVE_SEED", second["status"])
    }

    private fun runImporter(): Map<String, Any> {
        val repositoryRoot = Path.of(System.getProperty("user.dir"), "../../..").normalize()
        val pythonServices = repositoryRoot.resolve("workspaces/decision-platform/python-services")
        val manifest = repositoryRoot.resolve("deploy/p1/seed/public-rag/public-rag-seed.v1.manifest.json")
        val processBuilder =
            ProcessBuilder(
                "uv",
                "run",
                "--frozen",
                "python",
                "-m",
                "app.release.public_rag_seed_cli",
                "import",
                "--manifest",
                manifest.toString(),
            ).directory(pythonServices.toFile())
                .redirectErrorStream(true)
                .apply {
                    environment()["P1_SEED_DATABASE_DSN"] =
                        "host=${postgres.host} port=${postgres.firstMappedPort} " +
                        "dbname=${postgres.databaseName} user=flyway sslmode=disable"
                    environment()["PGPASSWORD"] = FLYWAY_PASSWORD
                }
        val process = processBuilder.start()
        assertTrue(process.waitFor(4, TimeUnit.MINUTES), "Seed importer timed out")
        val output =
            process.inputStream
                .readAllBytes()
                .toString(StandardCharsets.UTF_8)
                .trim()
        assertEquals(0, process.exitValue(), "Seed importer failed with content-free output: $output")
        @Suppress("UNCHECKED_CAST")
        return JsonMapper
            .builder()
            .build()
            .readValue(output, Map::class.java) as Map<String, Any>
    }

    companion object {
        private const val FLYWAY_PASSWORD = "flyway-test"
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            stablePostgresContainer(postgresImage)
                .withDatabaseName("p1_public_seed")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")
    }
}
