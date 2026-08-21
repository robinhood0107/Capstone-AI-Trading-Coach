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
import java.sql.DriverManager
import java.sql.SQLException
import java.util.UUID

@Testcontainers
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class S7AsyncMigrationIntegrationTest {
    @BeforeAll
    fun migrateFreshAndUpgrade() {
        flyway(databaseName = "decision", target = "79").migrate()
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { owner ->
            owner.createStatement().use { statement ->
                statement.executeUpdate(
                    "insert into processed_event(event_id,consumer_name,payload_hash) " +
                        "values ('evt_legacy_00000001','legacy-consumer',null)",
                )
            }
        }
        flyway(databaseName = "decision").migrate()

        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { owner ->
            owner.createStatement().use { it.executeUpdate("create database s7_fresh") }
        }
        flyway(databaseName = "s7_fresh").migrate()
    }

    @Test
    fun `V80 migrates fresh and V79 upgrade with exact role boundary`() {
        for (database in listOf("decision", "s7_fresh")) {
            connection(database, postgres.username, postgres.password).use { owner ->
                owner.createStatement().use { statement ->
                    statement.executeQuery("select version from flyway_schema_history order by installed_rank").use { rows ->
                        val versions = mutableListOf<String>()
                        while (rows.next()) versions += rows.getString(1)
                        assertEquals((1..80).map(Int::toString), versions)
                    }
                    statement.executeQuery("select count(*) from async_event_registry").use { rows ->
                        assertTrue(rows.next())
                        assertEquals(12, rows.getInt(1))
                    }
                    statement
                        .executeQuery(
                            "select rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls " +
                                "from pg_roles where rolname='decision_worker'",
                        ).use { rows ->
                            assertTrue(rows.next())
                            (1..5).forEach { assertFalse(rows.getBoolean(it)) }
                        }
                    statement
                        .executeQuery(
                            "select has_table_privilege('decision_worker','async_job','SELECT')," +
                                "has_table_privilege('decision_worker','async_job','UPDATE')," +
                                "has_table_privilege('decision_worker','processed_event','INSERT')",
                        ).use { rows ->
                            assertTrue(rows.next())
                            assertFalse(rows.getBoolean(1))
                            assertFalse(rows.getBoolean(2))
                            assertTrue(rows.getBoolean(3))
                        }
                }
            }
        }

        connection("decision", postgres.username, postgres.password).use { owner ->
            owner.createStatement().use { statement ->
                statement.executeQuery("select payload_hash from processed_event where event_id='evt_legacy_00000001'").use { rows ->
                    assertTrue(rows.next())
                    assertEquals("sha256:" + "0".repeat(64), rows.getString(1))
                }
            }
        }
    }

    @Test
    fun `job claim is fenced idempotent and append only`() {
        val jobId = "job_fixture_00000001"
        connection("decision", APP_USER, APP_PASSWORD).use { app ->
            app.prepareStatement("select create_async_job(?,?,?,?::jsonb)").use { statement ->
                statement.setString(1, jobId)
                statement.setString(2, "RAG_INDEX")
                statement.setString(3, "usr_demo_user")
                statement.setString(4, "{\"sourceId\":\"src_fixture_00000001\"}")
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertTrue(rows.getBoolean(1))
                }
            }
        }

        val token =
            connection("decision", WORKER_USER, WORKER_PASSWORD).use { worker ->
                worker.prepareStatement("select job_id,claim_token,attempt_count from claim_async_jobs(?,?)").use { statement ->
                    statement.setString(1, "worker:test")
                    statement.setInt(2, 100)
                    statement.executeQuery().use { rows ->
                        assertTrue(rows.next())
                        assertEquals(jobId, rows.getString(1))
                        assertEquals(1, rows.getInt(3))
                        val claimed = rows.getObject(2, UUID::class.java)
                        assertFalse(rows.next())
                        claimed
                    }
                }
            }

        connection("decision", WORKER_USER, WORKER_PASSWORD).use { worker ->
            worker.prepareStatement("select count(*) from claim_async_jobs(?,?)").use { statement ->
                statement.setString(1, "worker:other")
                statement.setInt(2, 100)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertEquals(0, rows.getInt(1))
                }
            }
            worker.prepareStatement("select complete_async_job(?,?,?::jsonb)").use { statement ->
                statement.setString(1, jobId)
                statement.setObject(2, token)
                statement.setString(3, "{\"resultRef\":\"rag_index_result_00000001\"}")
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertTrue(rows.getBoolean(1))
                }
            }
            worker.prepareStatement("select complete_async_job(?,?,?::jsonb)").use { statement ->
                statement.setString(1, jobId)
                statement.setObject(2, token)
                statement.setString(3, "{}")
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertFalse(rows.getBoolean(1))
                }
            }
            val baseRead = assertThrows<SQLException> { worker.createStatement().use { it.executeQuery("select * from async_job") } }
            assertEquals("42501", baseRead.sqlState)
            val flywayRead =
                assertThrows<SQLException> { worker.createStatement().use { it.executeQuery("select * from flyway_schema_history") } }
            assertEquals("42501", flywayRead.sqlState)
        }

        connection("decision", postgres.username, postgres.password).use { owner ->
            owner.createStatement().use { statement ->
                statement
                    .executeQuery(
                        "select previous_status,next_status from async_job_transition_audit where job_id='$jobId' order by occurred_at",
                    ).use { rows ->
                        assertTrue(rows.next())
                        assertEquals("REQUESTED", rows.getString(1))
                        assertEquals("RUNNING", rows.getString(2))
                        assertTrue(rows.next())
                        assertEquals("RUNNING", rows.getString(1))
                        assertEquals("COMPLETED", rows.getString(2))
                        assertFalse(rows.next())
                    }
                val immutable = assertThrows<SQLException> { statement.executeUpdate("delete from async_job_transition_audit") }
                assertEquals("42501", immutable.sqlState)
            }
        }
    }

    @Test
    fun `outbox claim skips locked rows and unknown events never leave database`() {
        connection("decision", APP_USER, APP_PASSWORD).use { app ->
            app.createStatement().use { statement ->
                statement.executeUpdate(
                    """
                    INSERT INTO event_outbox(
                      event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,schema_version
                    ) VALUES
                      ('evt_rag_index_00000001','rag.index-requested.v1','RAG_SOURCE','src_fixture_00000001',
                       'opaque_fixture_00000001',
                       '{"jobId":"job_fixture_00000002","sourceRevisionId":"srv_fixture_00000001"}'::jsonb,'1.0.0'),
                      ('evt_unknown_00000001','unknown.event.v1','UNKNOWN','unknown_00000001',
                       'opaque_unknown_00000001','{}'::jsonb,'1.0.0')
                    """.trimIndent(),
                )
                statement.executeQuery("select quarantine_unknown_outbox(100)").use { rows ->
                    assertTrue(rows.next())
                    assertEquals(1, rows.getInt(1))
                }
                statement.executeQuery("select event_id,claim_token from claim_event_outbox('publisher:test',100)").use { rows ->
                    assertTrue(rows.next())
                    assertEquals("evt_rag_index_00000001", rows.getString(1))
                    assertFalse(rows.next())
                }
            }
        }

        connection("decision", postgres.username, postgres.password).use { owner ->
            val immutable =
                assertThrows<SQLException> {
                    owner.createStatement().use {
                        it.executeUpdate("update event_outbox set payload_json='{}'::jsonb where event_id='evt_rag_index_00000001'")
                    }
                }
            assertEquals("42501", immutable.sqlState)
            owner.createStatement().use { statement ->
                statement.executeQuery("select status,failure_code from event_outbox where event_id='evt_unknown_00000001'").use { rows ->
                    assertTrue(rows.next())
                    assertEquals("DLQ_REQUESTED", rows.getString(1))
                    assertEquals("UNREGISTERED_EVENT", rows.getString(2))
                }
            }
        }
    }

    @Test
    fun `ADMIN status read revalidates current actor and audits cross owner access`() {
        val jobId = "job_admin_view_00000001"
        connection("decision", APP_USER, APP_PASSWORD).use { app ->
            app.prepareStatement("select create_async_job(?,?,?,?::jsonb)").use { statement ->
                statement.setString(1, jobId)
                statement.setString(2, "MODEL_EVAL")
                statement.setString(3, "usr_demo_user")
                statement.setString(4, "{\"runId\":\"run_fixture_00000001\"}")
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertTrue(rows.getBoolean(1))
                }
            }
            app.prepareStatement("select count(*) from read_async_job_status(?,?,?)").use { statement ->
                statement.setString(1, "usr_demo_user")
                statement.setLong(2, 1)
                statement.setString(3, jobId)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertEquals(0, rows.getInt(1))
                }
            }
            app.prepareStatement("select job_id,status from read_async_job_status(?,?,?)").use { statement ->
                statement.setString(1, "usr_demo_admin")
                statement.setLong(2, 1)
                statement.setString(3, jobId)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertEquals(jobId, rows.getString(1))
                    assertEquals("REQUESTED", rows.getString(2))
                    assertFalse(rows.next())
                }
            }
            app.prepareStatement("select job_id from list_async_job_status(?,?,?,?,?,?,?)").use { statement ->
                statement.setString(1, "usr_demo_admin")
                statement.setLong(2, 1)
                statement.setString(3, "REQUESTED")
                statement.setString(4, "MODEL_EVAL")
                statement.setObject(5, null)
                statement.setObject(6, null)
                statement.setInt(7, 51)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertEquals(jobId, rows.getString(1))
                    assertFalse(rows.next())
                }
            }
            app.prepareStatement("select count(*) from read_async_job_status(?,?,?)").use { statement ->
                statement.setString(1, "usr_demo_admin")
                statement.setLong(2, 999)
                statement.setString(3, jobId)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertEquals(0, rows.getInt(1))
                }
            }
            val auditRead =
                assertThrows<SQLException> {
                    app.createStatement().use { it.executeQuery("select * from async_job_admin_read_audit") }
                }
            assertEquals("42501", auditRead.sqlState)
        }

        connection("decision", postgres.username, postgres.password).use { owner ->
            owner.createStatement().use { statement ->
                statement
                    .executeQuery(
                        "select read_kind,count(*) from async_job_admin_read_audit " +
                            "where job_id='$jobId' group by read_kind order by read_kind",
                    ).use { rows ->
                        assertTrue(rows.next())
                        assertEquals("DETAIL", rows.getString(1))
                        assertEquals(1, rows.getInt(2))
                        assertTrue(rows.next())
                        assertEquals("LIST", rows.getString(1))
                        assertEquals(1, rows.getInt(2))
                        assertFalse(rows.next())
                    }
                val immutable =
                    assertThrows<SQLException> {
                        statement.executeUpdate("delete from async_job_admin_read_audit where job_id='$jobId'")
                    }
                assertEquals("42501", immutable.sqlState)
            }
        }
    }

    @Test
    fun `worker records redacted poison and app publishes exact DLQ topic`() {
        val dlqEventId = "evt_dlq_" + "a".repeat(32)
        val originalEventId = "evt_poison_00000001"
        val payloadHash = "sha256:" + "b".repeat(64)
        val partitionKey = "hmac-sha256:" + "c".repeat(64)
        connection("decision", WORKER_USER, WORKER_PASSWORD).use { worker ->
            worker.prepareStatement("select record_kafka_poison(?,?,?,?,?,?,?,?)").use { statement ->
                statement.setString(1, dlqEventId)
                statement.setString(2, originalEventId)
                statement.setString(3, "artifact.ingest-requested.v1")
                statement.setString(4, payloadHash)
                statement.setString(5, "artifact.ingest-requested.v1")
                statement.setInt(6, 1)
                statement.setString(7, "INVALID_EVENT_PAYLOAD")
                statement.setString(8, partitionKey)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertTrue(rows.getBoolean(1))
                }
            }
        }
        val claimToken =
            connection("decision", APP_USER, APP_PASSWORD).use { app ->
                app
                    .prepareStatement(
                        "select event_id,topic_name,payload_json::text,claim_token from claim_dlq_outbox(?,?)",
                    ).use { statement ->
                        statement.setString(1, "kafka-publisher-test")
                        statement.setInt(2, 100)
                        statement.executeQuery().use { rows ->
                            assertTrue(rows.next())
                            assertEquals(dlqEventId, rows.getString(1))
                            assertEquals("artifact.ingest-requested.dlq.v1", rows.getString(2))
                            val payload = rows.getString(3)
                            assertTrue(payload.contains(payloadHash))
                            assertFalse(payload.contains("secret"))
                            rows.getObject(4, java.util.UUID::class.java)
                        }
                    }
            }
        connection("decision", APP_USER, APP_PASSWORD).use { app ->
            app.prepareStatement("select complete_dlq_outbox(?,?)").use { statement ->
                statement.setString(1, dlqEventId)
                statement.setObject(2, claimToken)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertTrue(rows.getBoolean(1))
                }
            }
            val denied =
                assertThrows<SQLException> {
                    app.prepareStatement("select record_kafka_poison(?,?,?,?,?,?,?,?)").use { statement ->
                        (1..5).forEach { statement.setString(it, "x") }
                        statement.setInt(6, 1)
                        statement.setString(7, "X")
                        statement.setString(8, "x")
                        statement.executeQuery()
                    }
                }
            assertEquals("42501", denied.sqlState)
        }
    }

    private fun flyway(
        databaseName: String,
        target: String? = null,
    ): Flyway {
        val configuration =
            Flyway
                .configure()
                .dataSource(jdbcUrl(databaseName), postgres.username, postgres.password)
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

    private fun connection(
        databaseName: String,
        username: String,
        password: String,
    ) = DriverManager.getConnection(jdbcUrl(databaseName), username, password)

    private fun jdbcUrl(databaseName: String): String = postgres.jdbcUrl.replace("/decision", "/$databaseName")

    companion object {
        private const val APP_USER = "decision_app"
        private const val APP_PASSWORD = "app-test"
        private const val WORKER_USER = "decision_worker"
        private const val WORKER_PASSWORD = "worker-test-secret-0001"
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
