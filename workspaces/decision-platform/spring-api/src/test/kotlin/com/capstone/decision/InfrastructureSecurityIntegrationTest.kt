package com.capstone.decision

import org.flywaydb.core.Flyway
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import org.testcontainers.utility.MountableFile
import java.nio.file.Files
import java.nio.file.Path
import java.sql.DriverManager
import java.sql.SQLException
import java.util.Properties

// 실제 init script와 Flyway migration을 함께 적용해 runtime/migration role 분리가 선언뿐인지 검증한다.
@Testcontainers
class InfrastructureSecurityIntegrationTest {
    @Test
    fun `postgres runtime role has dml access without ddl or cluster privileges`() {
        val repositoryRoot = findRepositoryRoot()
        postgres.copyFileToContainer(
            MountableFile.forHostPath(repositoryRoot.resolve("infra/init/01-extensions.sql")),
            "/tmp/01-extensions.sql",
        )
        postgres.copyFileToContainer(
            MountableFile.forHostPath(repositoryRoot.resolve("infra/init/02-application-roles.sh")),
            "/tmp/02-application-roles.sh",
        )
        assertTrue(
            postgres
                .execInContainer(
                    "bash",
                    "-ec",
                    "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -v ON_ERROR_STOP=1 " +
                        "--username \"\$POSTGRES_USER\" --dbname \"\$POSTGRES_DB\" -f /tmp/01-extensions.sql && " +
                        "bash /tmp/02-application-roles.sh",
                ).exitCode == 0,
        )

        Flyway
            .configure()
            .dataSource(postgres.jdbcUrl, MIGRATION_USER, migrationPassword)
            .locations("classpath:db/migration")
            .load()
            .migrate()

        DriverManager.getConnection(postgres.jdbcUrl, runtimeProperties()).use { connection ->
            connection.createStatement().use { statement ->
                statement
                    .executeQuery(
                        "select rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls " +
                            "from pg_roles where rolname = current_user",
                    ).use { result ->
                        assertTrue(result.next())
                        for (column in 1..5) {
                            assertFalse(result.getBoolean(column))
                        }
                    }
                assertTrue(
                    statement
                        .executeQuery("select count(*) >= 1 from market_calendar")
                        .use { it.next() && it.getBoolean(1) },
                )
                assertFalse(
                    statement
                        .executeQuery(
                            "select " +
                                "has_table_privilege(current_user, 'public.flyway_schema_history', 'SELECT') or " +
                                "has_table_privilege(current_user, 'public.flyway_schema_history', 'INSERT') or " +
                                "has_table_privilege(current_user, 'public.flyway_schema_history', 'UPDATE') or " +
                                "has_table_privilege(current_user, 'public.flyway_schema_history', 'DELETE')",
                        ).use { it.next() && it.getBoolean(1) },
                    "runtime role must not read or mutate Flyway migration history",
                )
                val ddlFailure =
                    assertThrows<SQLException> {
                        statement.execute("create table runtime_must_not_create(id int)")
                    }
                assertTrue(ddlFailure.sqlState == "42501")
                val setRoleFailure =
                    assertThrows<SQLException> {
                        statement.execute("set role $MIGRATION_USER")
                    }
                assertTrue(setRoleFailure.sqlState == "42501")
            }
        }
    }

    private fun runtimeProperties(): Properties =
        Properties().apply {
            setProperty("user", RUNTIME_USER)
            setProperty("password", runtimePassword)
        }

    private fun findRepositoryRoot(): Path {
        var current = Path.of(System.getProperty("user.dir")).toAbsolutePath()
        while (!Files.exists(current.resolve("AGENTS.md"))) {
            current = current.parent ?: error("repository root was not found")
        }
        return current
    }

    companion object {
        private const val RUNTIME_USER = "decision_app"
        private const val MIGRATION_USER = "flyway"
        private val adminPassword: String = "a" + "p".repeat(24)
        private val runtimePassword: String = "r" + "p".repeat(24)
        private val migrationPassword: String = "m" + "p".repeat(24)
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            PostgreSQLContainer(postgresImage)
                .withDatabaseName("trading")
                .withUsername("postgres")
                .withPassword(adminPassword)
                .withEnv("POSTGRES_APP_PASSWORD", runtimePassword)
                .withEnv("POSTGRES_MIGRATION_PASSWORD", migrationPassword)
    }
}
