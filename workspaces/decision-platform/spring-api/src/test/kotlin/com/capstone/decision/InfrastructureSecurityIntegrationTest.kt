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
import java.sql.Connection
import java.sql.DriverManager
import java.sql.SQLException
import java.util.Properties

// 실제 init script와 Flyway migration을 함께 적용해 runtime/migration role 분리가 선언뿐인지 검증한다.
@Testcontainers
class InfrastructureSecurityIntegrationTest {
    @Test
    fun `postgres runtime role keeps exact Principle privileges without migration or cluster privileges`() {
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
            .placeholders(
                mapOf(
                    "brokerageDbCapabilityTokenSha256" to
                        SpringApiIntegrationTestBase.TEST_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256,
                ),
            ).javaMigrations(s21ActorTrustMigration())
            .load()
            .migrate()

        // 기존 volume에서 bootstrap을 재적용해도 migration의 calendar·Principle 최소권한을 되돌리면 안 된다.
        assertTrue(postgres.execInContainer("bash", "-ec", "bash /tmp/02-application-roles.sh").exitCode == 0)

        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, adminPassword).use { connection ->
            assertTrue(hasTablePrivilege(connection, "decision_collector", "opendart_quota_usage", "UPDATE"))
            assertTrue(hasTablePrivilege(connection, "decision_collector", "calendar_observations", "INSERT"))
            assertFalse(hasTablePrivilege(connection, "decision_collector", "calendar_observations", "UPDATE"))
            assertFalse(hasTablePrivilege(connection, "decision_collector", "flyway_schema_history", "SELECT"))
            assertTrue(hasTablePrivilege(connection, "decision_app", "trading_sessions", "SELECT"))
            assertTrue(hasTablePrivilege(connection, "decision_app", "users", "SELECT"))
            listOf("INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(hasTablePrivilege(connection, "decision_app", "users", privilege))
            }
            assertFalse(hasTablePrivilege(connection, "decision_app", "calendar_observations", "SELECT"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "opendart_quota_usage", "SELECT"))

            assertTrue(hasTablePrivilege(connection, "decision_app", "principle_presets", "SELECT"))
            assertTrue(hasTablePrivilege(connection, "decision_app", "principles", "SELECT"))
            assertTrue(hasTablePrivilege(connection, "decision_app", "principles", "INSERT"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "principles", "UPDATE"))
            listOf("title", "mode", "status", "current_version", "updated_at").forEach { column ->
                assertTrue(hasColumnPrivilege(connection, "decision_app", "principles", column, "UPDATE"))
            }
            listOf("principle_id", "user_id", "preset_id", "created_at").forEach { column ->
                assertFalse(hasColumnPrivilege(connection, "decision_app", "principles", column, "UPDATE"))
            }
            assertTrue(hasTablePrivilege(connection, "decision_app", "principle_versions", "SELECT"))
            assertTrue(hasTablePrivilege(connection, "decision_app", "principle_versions", "INSERT"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "principle_versions", "UPDATE"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "principle_versions", "DELETE"))
            assertTrue(hasTablePrivilege(connection, "decision_app", "audit_logs", "INSERT"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "audit_logs", "SELECT"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "audit_logs", "UPDATE"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "audit_logs", "DELETE"))
            listOf("INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(hasTablePrivilege(connection, "decision_app", "principle_presets", privilege))
            }
            assertFalse(hasTablePrivilege(connection, "decision_app", "principles", "DELETE"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "principles", "TRUNCATE"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "principle_versions", "TRUNCATE"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "audit_logs", "TRUNCATE"))
            assertTrue(hasTablePrivilege(connection, "decision_app", "decisions", "INSERT"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "decisions", "SELECT"))
            listOf("UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(hasTablePrivilege(connection, "decision_app", "decisions", privilege))
            }
            assertTrue(hasTablePrivilege(connection, "decision_app", "decision_owner_projection", "SELECT"))
            assertTrue(hasTablePrivilege(connection, "decision_app", "decision_audit_projection", "SELECT"))
            assertTrue(hasTablePrivilege(connection, "decision_app", "risk_kill_switch", "SELECT"))
            assertTrue(hasTablePrivilege(connection, "decision_app", "risk_kill_switch_transitions", "INSERT"))
            assertTrue(hasTablePrivilege(connection, "decision_app", "kill_switch_user_projection", "SELECT"))
            listOf(
                "active",
                "reason_class",
                "generation",
                "changed_by",
                "changed_by_role",
                "changed_at",
                "request_id",
            ).forEach { column ->
                assertTrue(hasColumnPrivilege(connection, "decision_app", "risk_kill_switch", column, "UPDATE"))
            }
            listOf("INSERT", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(hasTablePrivilege(connection, "decision_app", "risk_kill_switch", privilege))
            }
            listOf("SELECT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(hasTablePrivilege(connection, "decision_app", "risk_kill_switch_transitions", privilege))
            }
            listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(hasTablePrivilege(connection, "decision_app", "decision_invalidations", privilege))
            }
            listOf(
                "read_kill_switch_gate()",
                "revalidate_kill_switch_admin(text,bigint)",
                "read_kill_switch_audit_projection()",
                "read_decision_usability()",
                "invalidate_unused_decisions_for_kill_switch(bigint,timestamp with time zone,text)",
                "read_mock_order_decision(text,text,text)",
                "find_mock_order_idempotency_result(text,text,timestamp with time zone,text)",
                "read_mock_order_owner_projection(text,text,text)",
                "create_mock_order(jsonb,text)",
                "request_mock_order_cancel(jsonb,text)",
            ).forEach { function ->
                assertTrue(hasFunctionPrivilege(connection, "decision_app", function))
            }
            listOf("orders", "order_events", "mock_order_owner_projection", "brokerage_db_capability_keys").forEach { table ->
                listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                    assertFalse(hasTablePrivilege(connection, "decision_app", table, privilege))
                }
            }
            listOf("user_sessions").forEach { table ->
                listOf("INSERT", "UPDATE", "DELETE").forEach { privilege ->
                    assertFalse(hasTablePrivilege(connection, "decision_app", table, privilege))
                }
            }
            listOf(
                "current_corporation_registry_projection",
                "disclosure_event_observation_projection",
                "disclosure_collection_status_projection",
            ).forEach { table ->
                assertTrue(
                    hasTablePrivilege(connection, "decision_disclosure_reader", table, "SELECT"),
                    "disclosure reader must read $table",
                )
            }
            listOf(
                "decisions",
                "audit_logs",
                "market_quote_observations",
                "corporation_registry_observations",
                "flyway_schema_history",
            ).forEach { table ->
                listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                    assertFalse(
                        hasTablePrivilege(connection, "decision_disclosure_reader", table, privilege),
                        "unexpected disclosure reader $privilege on $table",
                    )
                }
            }
        }

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
                listOf(
                    "insert into users (user_id, username, role, password_hash) " +
                        "values ('usr-runtime-denied', 'runtime-denied', 'USER', 'denied')",
                    "update users set status = 'LOCKED' where user_id = 'usr_demo_user'",
                    "delete from users where user_id = 'usr_demo_user'",
                    "truncate table users",
                    "insert into principle_presets (preset_id, name_ko, name_en, description_ko, description_en, " +
                        "mode, rules_json, display_order) values " +
                        "('runtime-denied', '거부', 'Denied', '거부', 'Denied', 'GUIDE', '[]'::jsonb, 99)",
                    "update principle_presets set is_active = false where preset_id = 'balanced'",
                    "delete from principle_presets where preset_id = 'balanced'",
                    "update principles set user_id = 'usr_demo_admin' where principle_id = 'missing'",
                    "update principles set preset_id = 'aggressive' where principle_id = 'missing'",
                    "delete from principles where principle_id = 'missing'",
                    "update principle_versions set title = 'denied' where principle_id = 'missing'",
                    "delete from principle_versions where principle_id = 'missing'",
                    "select count(*) from audit_logs",
                    "update audit_logs set action = 'denied' where audit_log_id = 'missing'",
                    "delete from audit_logs where audit_log_id = 'missing'",
                    "truncate table principles",
                    "truncate table principle_versions",
                    "truncate table audit_logs",
                    "insert into audit_logs (" +
                        "audit_log_id, user_id, actor_role, action, target_type, " +
                        "target_id, request_id, payload_json, created_at" +
                        ") values (" +
                        "'aud-runtime-forged-order', 'usr_demo_user', 'USER', " +
                        "'MOCK_ORDER_SUBMITTED', 'ORDER', 'ord_mock_0000000000000000000000000000f001', " +
                        "'req-runtime-forged-order', " +
                        "jsonb_build_object(" +
                        "'orderId', 'ord_mock_0000000000000000000000000000f001', " +
                        "'decisionId', 'dec-runtime-forged-order', " +
                        "'evaluationId', 'eval-runtime-forged-order', " +
                        "'brokerageMode', 'KIS_MOCK', " +
                        "'status', 'SUBMITTED', " +
                        "'idempotencyScopeHash', repeat('1', 64)" +
                        "), now())",
                    "insert into event_outbox (" +
                        "event_id, event_type, aggregate_type, aggregate_id, partition_key, " +
                        "payload_json, schema_version, status, retry_count, created_at, updated_at" +
                        ") values (" +
                        "'evt-runtime-forged-order', 'brokerage.mock-order-submitted.v1', " +
                        "'ORDER', 'ord_mock_0000000000000000000000000000f001', " +
                        "'ord_mock_0000000000000000000000000000f001', " +
                        "jsonb_build_object(" +
                        "'orderId', 'ord_mock_0000000000000000000000000000f001', " +
                        "'decisionId', 'dec-runtime-forged-order', " +
                        "'evaluationId', 'eval-runtime-forged-order', " +
                        "'brokerageMode', 'KIS_MOCK', " +
                        "'status', 'SUBMITTED', " +
                        "'idempotencyScopeHash', repeat('1', 64)" +
                        "), '1.0.0', 'PENDING', 0, now(), now())",
                    "select * from decisions limit 0",
                    "update decisions set outcome = outcome where false",
                    "delete from decisions where false",
                    "truncate table decisions",
                    "insert into risk_kill_switch (" +
                        "kill_switch_id,active,reason_class,generation,changed_by,changed_by_role,changed_at" +
                        ") values ('OTHER',true,'USER_MANUAL_STOP',2,'usr_demo_user','USER',now())",
                    "delete from risk_kill_switch where false",
                    "update risk_kill_switch_transitions set reason_class = reason_class where false",
                    "delete from risk_kill_switch_transitions where false",
                    "insert into decision_invalidations (" +
                        "invalidation_id,decision_id,evaluation_id,owner_user_id,reason_class,invalidated_at" +
                        ") values ('denied','denied','denied','usr_demo_user','KILL_SWITCH_ACTIVATED',now())",
                    "select * from orders",
                    "insert into orders default values",
                    "select * from order_events",
                    "insert into order_events default values",
                    "select * from mock_order_owner_projection",
                    "select * from brokerage_db_capability_keys",
                ).forEach { sql ->
                    val mutationFailure = assertThrows<SQLException> { statement.execute(sql) }
                    assertTrue(mutationFailure.sqlState == "42501")
                }
                listOf("audit_logs", "principle_versions", "order_events", "paper_order_events").forEach { table ->
                    assertFalse(
                        statement
                            .executeQuery(
                                "select " +
                                    "has_table_privilege(current_user, 'public.$table', 'UPDATE') or " +
                                    "has_table_privilege(current_user, 'public.$table', 'DELETE')",
                            ).use { it.next() && it.getBoolean(1) },
                        "runtime role must not rewrite append-only table $table",
                    )
                }
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

    private fun hasTablePrivilege(
        connection: Connection,
        role: String,
        table: String,
        privilege: String,
    ): Boolean =
        connection
            .prepareStatement("select has_table_privilege(?, 'public.' || ?, ?)")
            .use { statement ->
                statement.setString(1, role)
                statement.setString(2, table)
                statement.setString(3, privilege)
                statement.executeQuery().use { result -> result.next() && result.getBoolean(1) }
            }

    private fun hasColumnPrivilege(
        connection: Connection,
        role: String,
        table: String,
        column: String,
        privilege: String,
    ): Boolean =
        connection
            .prepareStatement("select has_column_privilege(?, 'public.' || ?, ?, ?)")
            .use { statement ->
                statement.setString(1, role)
                statement.setString(2, table)
                statement.setString(3, column)
                statement.setString(4, privilege)
                statement.executeQuery().use { result -> result.next() && result.getBoolean(1) }
            }

    private fun hasFunctionPrivilege(
        connection: Connection,
        role: String,
        functionSignature: String,
    ): Boolean =
        connection
            .prepareStatement("select has_function_privilege(?, ?, 'EXECUTE')")
            .use { statement ->
                statement.setString(1, role)
                statement.setString(2, functionSignature)
                statement.executeQuery().use { result -> result.next() && result.getBoolean(1) }
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
        private val collectorPassword: String = "c" + "p".repeat(24)
        private val disclosureReaderPassword: String = "d" + "r".repeat(24)
        private val marketWriterPassword: String = "w" + "m".repeat(24)
        private val portfolioWriterPassword: String = "w" + "p".repeat(24)
        private val riskWriterPassword: String = "w" + "r".repeat(24)
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
                .withEnv("POSTGRES_COLLECTOR_PASSWORD", collectorPassword)
                .withEnv("POSTGRES_DISCLOSURE_READER_PASSWORD", disclosureReaderPassword)
                .withEnv("POSTGRES_MARKET_WRITER_PASSWORD", marketWriterPassword)
                .withEnv("POSTGRES_PORTFOLIO_WRITER_PASSWORD", portfolioWriterPassword)
                .withEnv("POSTGRES_RISK_WRITER_PASSWORD", riskWriterPassword)
    }
}
