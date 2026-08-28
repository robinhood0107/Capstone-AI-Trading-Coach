package com.capstone.decision
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import com.capstone.decision.infrastructure.security.DatabaseActorCapabilityAuthority
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
import java.security.KeyPairGenerator
import java.security.MessageDigest
import java.sql.DriverManager
import java.sql.SQLException
import java.util.HexFormat
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
            owner.createStatement().use {
                it.executeUpdate("create database s7_fresh")
                it.executeUpdate("create database s7_replay")
                it.executeUpdate("create database s7_p1_demo")
                it.executeUpdate("create database s7_kafka_roles")
            }
        }
        flyway(databaseName = "s7_fresh").migrate()
        flyway(databaseName = "s7_replay").migrate()
        flyway(databaseName = "s7_p1_demo").migrate()
        flyway(databaseName = "s7_kafka_roles").migrate()
    }

    @Test
    fun `P1 container smoke capability is exact idempotent and demo-only`() {
        val partitionKey = "hmac-sha256:${"a".repeat(64)}"
        val runId = "1".repeat(32)
        connection("s7_p1_demo", DEMO_USER, DEMO_PASSWORD).use { demo ->
            val inactive =
                assertThrows<SQLException> {
                    demo.prepareStatement("select stage_p1_synthetic_async_request('DB',?,?)").use { statement ->
                        statement.setString(1, partitionKey)
                        statement.setString(2, runId)
                        statement.executeQuery()
                    }
                }
            assertEquals("42501", inactive.sqlState)
        }
        connection("s7_p1_demo", postgres.username, postgres.password).use { owner ->
            owner
                .prepareStatement(
                    "insert into p1_offline_demo_authority(" +
                        "authority_id,active,credential_bundle_digest) values ('P1_OFFLINE_DEMO',true,?)",
                ).use { statement ->
                    statement.setString(1, "b".repeat(64))
                    assertEquals(1, statement.executeUpdate())
                }
        }
        connection("s7_p1_demo", DEMO_USER, DEMO_PASSWORD).use { demo ->
            repeat(2) {
                demo.prepareStatement("select stage_p1_synthetic_async_request('DB',?,?)").use { statement ->
                    statement.setString(1, partitionKey)
                    statement.setString(2, runId)
                    statement.executeQuery().use { rows ->
                        assertTrue(rows.next())
                        assertEquals("job_p1_container_db_$runId", rows.getString(1))
                    }
                }
            }
            val nextRunId = "2".repeat(32)
            demo.prepareStatement("select stage_p1_synthetic_async_request('DB',?,?)").use { statement ->
                statement.setString(1, partitionKey)
                statement.setString(2, nextRunId)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertEquals("job_p1_container_db_$nextRunId", rows.getString(1))
                }
            }
            demo.createStatement().use { statement ->
                assertFalse(booleanResult(statement, "select verify_p1_synthetic_async_request('DB','$runId')"))
                assertFalse(booleanResult(statement, "select verify_p1_synthetic_async_request('DB','$nextRunId')"))
                val directRead = assertThrows<SQLException> { statement.executeQuery("select * from async_job") }
                assertEquals("42501", directRead.sqlState)
                val authorityWrite =
                    assertThrows<SQLException> {
                        statement.executeUpdate("update p1_offline_demo_authority set active=false")
                    }
                assertEquals("42501", authorityWrite.sqlState)
            }
        }
        connection("s7_p1_demo", APP_USER, APP_PASSWORD).use { app ->
            val denied =
                assertThrows<SQLException> {
                    app.createStatement().use {
                        it.executeQuery("select stage_p1_synthetic_async_request('DB','$partitionKey','$runId')")
                    }
                }
            assertEquals("42501", denied.sqlState)
        }
    }

    @Test
    fun `V87 migrates fresh and V79 upgrade with exact role boundary`() {
        for (database in listOf("decision", "s7_fresh")) {
            connection(database, postgres.username, postgres.password).use { owner ->
                owner.createStatement().use { statement ->
                    statement.executeQuery("select version from flyway_schema_history order by installed_rank").use { rows ->
                        val versions = mutableListOf<String>()
                        while (rows.next()) versions += rows.getString(1)
                        assertEquals((1..92).map(Int::toString), versions)
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
                                "has_table_privilege('decision_worker','processed_event','INSERT')," +
                                "has_function_privilege('decision_worker','claim_async_jobs(text,integer)','EXECUTE')," +
                                "has_function_privilege('decision_worker','complete_async_job(text,uuid,jsonb)','EXECUTE')," +
                                "has_function_privilege('decision_worker','quarantine_async_job(text,uuid,text,text)','EXECUTE')",
                        ).use { rows ->
                            assertTrue(rows.next())
                            (1..6).forEach { assertFalse(rows.getBoolean(it)) }
                        }
                    statement
                        .executeQuery(
                            "select has_table_privilege('decision_app','users','SELECT')," +
                                "has_table_privilege('decision_app','principles','SELECT')," +
                                "has_table_privilege('decision_app','principles','INSERT')," +
                                "has_table_privilege('decision_app','principles','UPDATE')," +
                                "has_table_privilege('decision_app','principle_versions','INSERT')," +
                                "has_function_privilege('decision_worker','claim_async_job_by_id(text,text)','EXECUTE')",
                        ).use { rows ->
                            assertTrue(rows.next())
                            (1..6).forEach { assertFalse(rows.getBoolean(it)) }
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

        connection("decision", APP_USER, APP_PASSWORD).use { app ->
            val directOutbox =
                assertThrows<SQLException> {
                    app.createStatement().use {
                        it.executeUpdate(
                            "insert into event_outbox(event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json," +
                                "schema_version) values ('evt_direct_denied_0001','model.eval-requested.v1','ASYNC_JOB'," +
                                "'job_direct_denied_0001','hmac-sha256:${"a".repeat(64)}','{}'::jsonb,'1.0.0')",
                        )
                    }
                }
            assertEquals("42501", directOutbox.sqlState)
            val replayDenied =
                assertThrows<SQLException> {
                    app.createStatement().use {
                        it.executeQuery(
                            "select * from replay_async_work('usr_demo_admin',1,'replay_${"a".repeat(32)}','EVENT'," +
                                "array['evt_direct_denied_0001'],1,'OPERATOR_RECOVERY','sha256:${"b".repeat(64)}',false)",
                        )
                    }
                }
            assertEquals("42501", replayDenied.sqlState)

            val mismatchedJob = "job_owner_mismatch_0001"
            val mismatchedPayload =
                "{\"jobId\":\"$mismatchedJob\",\"ownerRef\":\"usr_demo_admin\"," +
                    "\"runId\":\"run_owner_mismatch_0001\",\"contentHash\":\"sha256:${"a".repeat(64)}\"}"
            val ownerMismatch =
                assertThrows<SQLException> {
                    app.prepareStatement("select create_async_job(?,?,?,?::jsonb)").use { statement ->
                        statement.setString(1, mismatchedJob)
                        statement.setString(2, "MODEL_EVAL")
                        statement.setString(3, "usr_demo_user")
                        statement.setString(4, mismatchedPayload)
                        statement.executeQuery()
                    }
                }
            assertEquals("42501", ownerMismatch.sqlState)

            for (invalidPayload in listOf(
                "{\"jobId\":\"job_missing_ref_0001\",\"ownerRef\":\"usr_demo_user\"," +
                    "\"runId\":\"run_missing_ref_0001\"}",
                "{\"jobId\":\"job_forged_replay_0001\",\"ownerRef\":\"usr_demo_user\"," +
                    "\"runId\":\"run_forged_replay_0001\",\"contentHash\":\"sha256:${"b".repeat(64)}\"," +
                    "\"replayOf\":\"evt_source_00000001\"}",
            )) {
                val jobId = if ("missing_ref" in invalidPayload) "job_missing_ref_0001" else "job_forged_replay_0001"
                val invalid =
                    assertThrows<SQLException> {
                        app.prepareStatement("select create_async_job(?,?,?,?::jsonb)").use { statement ->
                            statement.setString(1, jobId)
                            statement.setString(2, "MODEL_EVAL")
                            statement.setString(3, "usr_demo_user")
                            statement.setString(4, invalidPayload)
                            statement.executeQuery()
                        }
                    }
                assertEquals("42501", invalid.sqlState)
            }
        }
        connection("decision", WORKER_USER, WORKER_PASSWORD).use { worker ->
            val directProcessed =
                assertThrows<SQLException> {
                    worker.createStatement().use {
                        it.executeUpdate(
                            "insert into processed_event(event_id,consumer_name,payload_hash) values " +
                                "('evt_direct_denied_0002','python-async-worker-v1','sha256:${"c".repeat(64)}')",
                        )
                    }
                }
            assertEquals("42501", directProcessed.sqlState)
            val forgedClaim =
                assertThrows<SQLException> {
                    worker.createStatement().use {
                        it.executeQuery(
                            "select * from claim_async_job_by_event('decision-python-async-v1','evt_forged_00000001'," +
                                "'artifact.ingest-requested.v1','job_forged_00000001','sha256:${"d".repeat(64)}'," +
                                "'hmac-sha256:${"e".repeat(64)}')",
                        )
                    }
                }
            assertEquals("42501", forgedClaim.sqlState)
            val legacyClaim =
                assertThrows<SQLException> {
                    worker.createStatement().use {
                        it.executeQuery("select * from claim_async_job_by_id('decision-python-async-v1','job_forged_00000001')")
                    }
                }
            assertEquals("42501", legacyClaim.sqlState)
        }
    }

    @Test
    fun `Kafka publisher and poison recorder roles are exact and one purpose only`() {
        val database = "s7_kafka_roles"
        val eventId = "evt_kafka_role_boundary_0001"
        val partitionKey = "hmac-sha256:${"a".repeat(64)}"
        connection(database, postgres.username, postgres.password).use { owner ->
            owner.createStatement().use { statement ->
                statement.executeUpdate(
                    """
                    insert into event_outbox(
                      event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,
                      schema_version,status,retry_count,next_attempt_at
                    ) values (
                      '$eventId','artifact.ingest-requested.v1','ASYNC_JOB','job_kafka_role_boundary_0001',
                      '$partitionKey',
                      '{"jobId":"job_kafka_role_boundary_0001","artifactId":"artifact_kafka_role_boundary_0001",
                        "contentHash":"sha256:${"b".repeat(64)}"}'::jsonb,
                      '1.0.0','PENDING',0,statement_timestamp()
                    )
                    """.trimIndent(),
                )
            }
        }

        for ((user, password) in listOf(APP_USER to APP_PASSWORD, WORKER_USER to WORKER_PASSWORD)) {
            connection(database, user, password).use { denied ->
                val error =
                    assertThrows<SQLException> {
                        denied.createStatement().use {
                            it.executeQuery("select * from p1_claim_kafka_outbox('p1-kafka-outbox-publisher',100)")
                        }
                    }
                assertEquals("42501", error.sqlState)
            }
        }

        var claimToken: UUID? = null
        connection(database, OUTBOX_USER, OUTBOX_PASSWORD).use { publisher ->
            publisher.createStatement().use { statement ->
                statement.executeQuery("select * from p1_claim_kafka_outbox('p1-kafka-outbox-publisher',100)").use { rows ->
                    while (rows.next()) {
                        if (rows.getString("event_id") == eventId) claimToken = rows.getObject("claim_token", UUID::class.java)
                    }
                }
                assertTrue(claimToken != null)
                assertTrue(
                    booleanResult(
                        statement,
                        "select p1_bind_kafka_outbox_payload_hash('$eventId','$claimToken','sha256:${"c".repeat(64)}')",
                    ),
                )
                assertTrue(booleanResult(statement, "select p1_complete_kafka_outbox('$eventId','$claimToken')"))
                val tableRead = assertThrows<SQLException> { statement.executeQuery("select * from event_outbox") }
                assertEquals("42501", tableRead.sqlState)
                val poison =
                    assertThrows<SQLException> {
                        statement.executeQuery(
                            "select p1_record_kafka_poison_receipt(" +
                                "'evt_poison_denied_00000001','artifact.ingest-requested.v1','sha256:${"d".repeat(64)}'," +
                                "'artifact.ingest-requested.v1',1,7,1,'INVALID_EVENT_PAYLOAD','$partitionKey',null,null)",
                        )
                    }
                assertEquals("42501", poison.sqlState)
            }
        }

        val poisonEvent = "evt_poison_role_boundary_0001"
        connection(database, POISON_USER, POISON_PASSWORD).use { recorder ->
            recorder.createStatement().use { statement ->
                val sql =
                    "select p1_record_kafka_poison_receipt(" +
                        "'$poisonEvent','artifact.ingest-requested.v1','sha256:${"d".repeat(64)}'," +
                        "'artifact.ingest-requested.v1',1,7,1,'INVALID_EVENT_SIGNATURE','$partitionKey',null,null)"
                assertTrue(booleanResult(statement, sql))
                assertTrue(booleanResult(statement, sql))
                val tableRead = assertThrows<SQLException> { statement.executeQuery("select * from p1_kafka_poison_receipt") }
                assertEquals("42501", tableRead.sqlState)
                val publisherClaim =
                    assertThrows<SQLException> {
                        statement.executeQuery("select * from p1_claim_kafka_outbox('p1-kafka-outbox-publisher',100)")
                    }
                assertEquals("42501", publisherClaim.sqlState)
            }
        }
        connection(database, postgres.username, postgres.password).use { owner ->
            owner.createStatement().use { statement ->
                assertEquals(
                    1,
                    statement
                        .executeQuery(
                            "select count(*) from p1_kafka_poison_receipt where source_topic='artifact.ingest-requested.v1' " +
                                "and source_partition=1 and source_offset=7",
                        ).use { rows ->
                            assertTrue(rows.next())
                            rows.getInt(1)
                        },
                )
                val immutable = assertThrows<SQLException> { statement.executeUpdate("delete from p1_kafka_poison_receipt") }
                assertEquals("42501", immutable.sqlState)
            }
        }
    }

    @Test
    fun `actor capability is current owner bound one use and bounded cleanup`() {
        val jobId = "job_capability_00000001"
        connection("decision", postgres.username, postgres.password).use { owner ->
            owner
                .prepareStatement(
                    "insert into async_job(job_id,job_type,requested_by,payload_json) values (?,?,?,?::jsonb) on conflict do nothing",
                ).use { statement ->
                    statement.setString(1, jobId)
                    statement.setString(2, "MODEL_EVAL")
                    statement.setString(3, "usr_demo_user")
                    statement.setString(
                        4,
                        "{\"jobId\":\"$jobId\",\"ownerRef\":\"usr_demo_user\"," +
                            "\"runId\":\"run_capability_00000001\",\"contentHash\":\"sha256:${"a".repeat(64)}\"}",
                    )
                    statement.executeUpdate()
                }
        }
        val capability =
            issueCapability(
                "decision",
                "usr_demo_admin",
                ActorCapabilityBinding.target("READ_ASYNC_JOB", "ASYNC_JOB", jobId, ActorCapabilityRolePolicy.ADMIN_ONLY),
            )
        connection("decision", APP_USER, APP_PASSWORD).use { app ->
            app.prepareStatement("select * from read_async_job_status_authorized(?,?,?,?)").use { statement ->
                statement.setString(1, capability)
                statement.setString(2, "usr_demo_user")
                statement.setLong(3, 1)
                statement.setString(4, jobId)
                statement.executeQuery().use { rows ->
                    assertFalse(rows.next())
                }
                statement.setString(2, "usr_demo_admin")
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertEquals(jobId, rows.getString("job_id"))
                }
                statement.executeQuery().use { rows ->
                    assertFalse(rows.next())
                }
            }
        }
        connection("decision", postgres.username, postgres.password).use { owner ->
            owner.createStatement().use { statement ->
                statement.executeUpdate(
                    "update actor_request_capability set issued_at=statement_timestamp()-interval '90 seconds', " +
                        "expires_at=statement_timestamp()-interval '1 minute' " +
                        "where actor_user_id='usr_demo_admin'",
                )
            }
        }
        issueCapability(
            "decision",
            "usr_demo_admin",
            ActorCapabilityBinding.target("READ_ASYNC_JOB", "ASYNC_JOB", jobId, ActorCapabilityRolePolicy.ADMIN_ONLY),
        )
        connection("decision", postgres.username, postgres.password).use { owner ->
            owner.createStatement().use { statement ->
                statement
                    .executeQuery(
                        "select count(*) from actor_request_capability where expires_at<=statement_timestamp()",
                    ).use { rows ->
                        assertTrue(rows.next())
                        assertEquals(0, rows.getInt(1))
                    }
                statement.executeUpdate("delete from async_job where job_id='$jobId'")
            }
        }
    }

    @Test
    fun `replay is dry run by default count fenced append only and creates new identities`() {
        val sourceJob = "job_replay_source_0001"
        val sourceEvent = "evt_replay_source_0001"
        connection("s7_replay", postgres.username, postgres.password).use { owner ->
            owner.createStatement().use { statement ->
                statement.executeUpdate(
                    """
                    insert into async_job(
                      job_id,job_type,status,requested_by,payload_json,result_json,next_attempt_at,attempt_count,
                      error_code,error_class,error_message
                    ) values (
                      '$sourceJob','MODEL_EVAL','NEEDS_REVIEW','usr_demo_user',
                      '{"jobId":"$sourceJob","ownerRef":"usr_demo_user","runId":"run_replay_source_0001",
                        "contentHash":"sha256:${"c".repeat(64)}"}'::jsonb,'{}'::jsonb,
                      statement_timestamp()+interval '1 day',3,'RETRY_EXHAUSTED','RETRYABLE_TRANSIENT','RETRY_EXHAUSTED'
                    ) on conflict do nothing
                    """.trimIndent(),
                )
                statement.executeUpdate(
                    """
                    insert into event_outbox(
                      event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,schema_version,
                      status,retry_count,next_attempt_at,failure_code,error_class,last_error
                    ) select '$sourceEvent','model.eval-requested.v1','ASYNC_JOB','$sourceJob','hmac-sha256:${"d".repeat(64)}',
                      payload_json,'1.0.0','DLQ_REQUESTED',3,statement_timestamp()+interval '1 day',
                      'RETRY_EXHAUSTED','RETRYABLE_TRANSIENT','RETRY_EXHAUSTED'
                    from async_job where job_id='$sourceJob' on conflict do nothing
                    """.trimIndent(),
                )
            }
        }
        connection("s7_replay", REPLAY_USER, REPLAY_PASSWORD).use { app ->
            fun replay(
                batch: String,
                expected: Int,
                execute: Boolean,
                securityVersion: Long = 1,
                targetIds: Array<String> = arrayOf(sourceEvent),
            ): List<List<String?>> {
                val packetHash = "sha256:" + sha256Hex(batch.toByteArray())
                authorizeReplay(
                    databaseName = "s7_replay",
                    batch = batch,
                    targetIds = targetIds,
                    expected = expected,
                    execute = execute,
                    securityVersion = securityVersion,
                    packetHash = packetHash,
                )
                return app.prepareStatement("select * from replay_async_work(?,?,?,?,?::text[],?,?,?,?)").use { statement ->
                    statement.setString(1, "usr_demo_admin")
                    statement.setLong(2, securityVersion)
                    statement.setString(3, batch)
                    statement.setString(4, "EVENT")
                    statement.setArray(5, app.createArrayOf("text", targetIds))
                    statement.setInt(6, expected)
                    statement.setString(7, "OPERATOR_RECOVERY")
                    statement.setString(8, packetHash)
                    statement.setBoolean(9, execute)
                    statement.executeQuery().use { rows ->
                        val output = mutableListOf<List<String?>>()
                        while (rows.next()) output += (1..6).map(rows::getString)
                        output
                    }
                }
            }

            val dryRun = replay("replay_" + "1".repeat(32), 1, false)
            assertEquals(1, dryRun.size)
            assertEquals("DRY_RUN", dryRun.single()[5])
            assertEquals(null, dryRun.single()[3])

            val mismatch =
                replay(
                    "replay_" + "2".repeat(32),
                    2,
                    true,
                    targetIds = arrayOf(sourceEvent, "evt_missing_00000001"),
                )
            assertEquals(2, mismatch.size)
            assertTrue(mismatch.all { it[5] == "COUNT_MISMATCH" })

            val executed = replay("replay_" + "3".repeat(32), 1, true)
            assertEquals("EXECUTED", executed.single()[5])
            val newJob = requireNotNull(executed.single()[3])
            val newEvent = requireNotNull(executed.single()[4])
            connection("s7_replay", postgres.username, postgres.password).use { owner ->
                owner
                    .prepareStatement(
                        """
                        select job.status,event.status,job.payload_json->>'replayOf',event.payload_json->>'jobId'
                        from async_job job join event_outbox event on event.aggregate_id=job.job_id
                        where job.job_id=? and event.event_id=?
                        """.trimIndent(),
                    ).use { statement ->
                        statement.setString(1, newJob)
                        statement.setString(2, newEvent)
                        statement.executeQuery().use { rows ->
                            assertTrue(rows.next())
                            assertEquals("REQUESTED", rows.getString(1))
                            assertEquals("PENDING", rows.getString(2))
                            assertEquals(sourceEvent, rows.getString(3))
                            assertEquals(newJob, rows.getString(4))
                        }
                    }
            }
            val staleAdmin =
                assertThrows<SQLException> {
                    replay("replay_" + "4".repeat(32), 1, false, securityVersion = 999)
                }
            assertEquals("42501", staleAdmin.sqlState)
            val auditRead =
                assertThrows<SQLException> {
                    app.createStatement().use { it.executeQuery("select * from async_replay_audit") }
                }
            assertEquals("42501", auditRead.sqlState)
        }
        connection("s7_replay", postgres.username, postgres.password).use { owner ->
            owner.createStatement().use { statement ->
                statement.executeQuery("select status from async_job where job_id='$sourceJob'").use { rows ->
                    assertTrue(rows.next())
                    assertEquals("NEEDS_REVIEW", rows.getString(1))
                }
                assertEquals(
                    3,
                    statement.executeQuery("select count(*) from async_replay_audit").use { rows ->
                        assertTrue(rows.next())
                        rows.getInt(1)
                    },
                )
                val immutable = assertThrows<SQLException> { statement.executeUpdate("delete from async_replay_audit") }
                assertEquals("42501", immutable.sqlState)
            }
        }
    }

    @Test
    fun `stream metrics are accurate idempotent append only and signal absence is unavailable`() {
        connection("s7_fresh", APP_USER, APP_PASSWORD).use { app ->
            app.createStatement().use { statement ->
                assertTrue(booleanResult(statement, "select aggregate_decision_distribution()"))
                assertTrue(booleanResult(statement, "select aggregate_failed_jobs()"))
                assertTrue(booleanResult(statement, "select aggregate_signal_freshness()"))
                assertTrue(booleanResult(statement, "select aggregate_dlq_events()"))
            }
            app.prepareStatement("select * from read_stream_metric_status_authorized(?,?,?)").use { statement ->
                statement.setString(
                    1,
                    issueCapability(
                        "s7_fresh",
                        "usr_demo_admin",
                        ActorCapabilityBinding.request(
                            "READ_STREAM_METRICS",
                            "STREAM_METRICS",
                            "stream-metrics",
                            ActorCapabilityRolePolicy.ADMIN_ONLY,
                        ),
                    ),
                )
                statement.setString(2, "usr_demo_admin")
                statement.setLong(3, 1)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertEquals("UNAVAILABLE", rows.getString("pipeline_health"))
                    assertEquals("EMPTY", rows.getString("decision_status"))
                    assertEquals("UNAVAILABLE", rows.getString("signal_status"))
                    assertEquals(null, rows.getBigDecimal("stale_signal_ratio"))
                }
            }
        }

        connection("decision", postgres.username, postgres.password).use { owner ->
            owner.createStatement().use { statement ->
                statement.executeUpdate(
                    """
                    insert into principles(
                      principle_id,user_id,preset_id,title,mode,status,current_version
                    ) values (
                      'prn-stream-metric','usr_demo_admin','balanced','Stream metric fixture','GUIDE','ACTIVE',1
                    ) on conflict do nothing
                    """.trimIndent(),
                )
                statement.executeUpdate(
                    """
                    insert into principle_versions(
                      principle_version_id,principle_id,version,preset_id,title,mode,status,
                      rules_json,changed_fields,created_by
                    ) select 'prv-stream-metric-v1','prn-stream-metric',1,'balanced',
                      'Stream metric fixture','GUIDE','ACTIVE',rules_json,array['title'],'usr_demo_admin'
                    from principle_presets where preset_id='balanced'
                    on conflict do nothing
                    """.trimIndent(),
                )
                listOf("ALLOW", "WARN", "HOLD", "BLOCK").forEachIndexed { index, outcome ->
                    val canSubmit = outcome == "ALLOW" || outcome == "WARN"
                    val action =
                        when (outcome) {
                            "ALLOW" -> "NONE"
                            "WARN" -> "ACKNOWLEDGE_WARNING"
                            "HOLD" -> "RE_EVALUATE"
                            else -> "DO_NOT_SUBMIT"
                        }
                    statement.executeUpdate(
                        """
                        insert into decisions(
                          decision_id,evaluation_id,user_id,principle_id,principle_version_id,
                          principle_version,portfolio_source,symbol,side,outcome,mode,can_submit_order,
                          enforcement_action,evaluation_as_of,created_at,valid_until,result_schema_version,
                          snapshot_schema_version,catalog_version,readiness_policy_version,mapping_versions_json,
                          semantic_input_hash,snapshot_artifact_hash,result_json
                        ) values (
                          'dec-stream-$index','eval-stream-$index','usr_demo_admin','prn-stream-metric',
                          'prv-stream-metric-v1',1,'INTERNAL_PAPER','005930','BUY','$outcome','GUIDE',$canSubmit,
                          '$action',statement_timestamp(),statement_timestamp(),statement_timestamp()+interval '10 minutes',
                          'risk-decision.v1','s2.2-metric-snapshot-v2',1,'s2.3-readiness-v1','{}'::jsonb,
                          repeat('a',64),repeat('b',64),'{}'::jsonb
                        ) on conflict do nothing
                        """.trimIndent(),
                    )
                }
                statement.executeUpdate(
                    """
                    insert into ingested_signals(
                      signal_id,producer,source_workspace,symbol,as_of,timeframe,confidence,predicted_return,
                      feature_summary_json,payload_json,contract_version,status,reason,signal,evaluation_id,
                      model_version,model_report_id,artifact_sha256,payload_sha256,provenance_sha256,
                      logical_identity_sha256,fixture,provenance_class,payload_canonical_text,artifact_verified,session_date
                    ) values (
                      'sig_stream_metric_0001','RULE_BASELINE','return-engine','005930',statement_timestamp(),'1d',
                      0.5,0.0,'{}'::jsonb,'{}'::jsonb,'signal-v2-runtime-v1','AVAILABLE',null,'HOLD',
                      'eval_stream_metric_0001','model_stream_metric','report_stream_metric',repeat('1',64),
                      repeat('2',64),repeat('3',64),repeat('4',64),false,'PRODUCTION','{}',true,
                      (statement_timestamp() at time zone 'Asia/Seoul')::date
                    ) on conflict do nothing
                    """.trimIndent(),
                )
                statement.executeUpdate(
                    """
                    insert into async_job(job_id,job_type,status,payload_json,result_json,next_attempt_at,attempt_count)
                    values ('job_stream_failed_0001','MODEL_EVAL','FAILED','{}'::jsonb,'{}'::jsonb,
                      statement_timestamp()+interval '1 day',1) on conflict do nothing
                    """.trimIndent(),
                )
                statement.executeUpdate(
                    """
                    insert into event_outbox(
                      event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,schema_version,
                      status,next_attempt_at,failure_code,error_class,last_error
                    ) values (
                      'evt_stream_dlq_0001','model.eval-requested.v1','MODEL_EVAL','run_stream_metric',
                      'opaque_stream_metric','{}'::jsonb,'1.0.0','DLQ_REQUESTED',statement_timestamp()+interval '1 day',
                      'INVALID_EVENT_PAYLOAD','CONTRACT_VIOLATION','INVALID_EVENT_PAYLOAD'
                    ) on conflict do nothing
                    """.trimIndent(),
                )
            }
        }

        val expected =
            connection("decision", postgres.username, postgres.password).use { owner ->
                owner.createStatement().use { statement ->
                    statement
                        .executeQuery(
                            """
                            select
                              count(*) filter (where outcome='ALLOW'),
                              count(*) filter (where outcome='WARN'),
                              count(*) filter (where outcome='HOLD'),
                              count(*) filter (where outcome='BLOCK'),
                              (select count(*) from async_job where status in ('FAILED','NEEDS_REVIEW')),
                              (select count(*) from event_outbox where status='DLQ_REQUESTED')
                            from decisions
                            where created_at >= ((statement_timestamp() at time zone 'Asia/Seoul')::date::timestamp
                              at time zone 'Asia/Seoul')
                            """.trimIndent(),
                        ).use { rows ->
                            assertTrue(rows.next())
                            (1..6).map(rows::getLong)
                        }
                }
            }

        connection("decision", APP_USER, APP_PASSWORD).use { app ->
            app.createStatement().use { statement ->
                assertTrue(booleanResult(statement, "select aggregate_decision_distribution()"))
                assertTrue(booleanResult(statement, "select aggregate_failed_jobs()"))
                assertTrue(booleanResult(statement, "select aggregate_signal_freshness()"))
                assertTrue(booleanResult(statement, "select aggregate_dlq_events()"))
                assertFalse(booleanResult(statement, "select aggregate_decision_distribution()"))
                assertFalse(booleanResult(statement, "select aggregate_failed_jobs()"))
                assertFalse(booleanResult(statement, "select aggregate_signal_freshness()"))
                assertFalse(booleanResult(statement, "select aggregate_dlq_events()"))
            }
            app.prepareStatement("select * from read_stream_metric_status_authorized(?,?,?)").use { statement ->
                statement.setString(
                    1,
                    issueCapability(
                        "decision",
                        "usr_demo_admin",
                        ActorCapabilityBinding.request(
                            "READ_STREAM_METRICS",
                            "STREAM_METRICS",
                            "stream-metrics",
                            ActorCapabilityRolePolicy.ADMIN_ONLY,
                        ),
                    ),
                )
                statement.setString(2, "usr_demo_admin")
                statement.setLong(3, 1)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertEquals("DEGRADED", rows.getString("pipeline_health"))
                    assertEquals(0, rows.getBigDecimal("stale_signal_ratio").compareTo(java.math.BigDecimal.ZERO))
                    expected.forEachIndexed { index, value -> assertEquals(value, rows.getLong(index + 4)) }
                    assertFalse(rows.next())
                }
            }
            app.prepareStatement("select count(*) from read_stream_metric_status_authorized(?,?,?)").use { statement ->
                statement.setString(
                    1,
                    issueCapability(
                        "decision",
                        "usr_demo_admin",
                        ActorCapabilityBinding.request(
                            "READ_STREAM_METRICS",
                            "STREAM_METRICS",
                            "stream-metrics",
                            ActorCapabilityRolePolicy.ADMIN_ONLY,
                        ),
                    ),
                )
                statement.setString(2, "usr_demo_admin")
                statement.setLong(3, 999)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertEquals(0, rows.getInt(1))
                }
            }
            val baseRead =
                assertThrows<SQLException> {
                    app.createStatement().use { it.executeQuery("select * from stream_metric_snapshot") }
                }
            assertEquals("42501", baseRead.sqlState)
        }
        connection("decision", postgres.username, postgres.password).use { owner ->
            val immutable =
                assertThrows<SQLException> {
                    owner.createStatement().use { it.executeUpdate("delete from stream_metric_snapshot") }
                }
            assertEquals("42501", immutable.sqlState)
            owner.createStatement().use { statement ->
                statement.executeQuery("select count(*) from async_job where job_type like '%CROSS%'").use { rows ->
                    assertTrue(rows.next())
                    assertEquals(0, rows.getInt(1))
                }
            }
        }
    }

    @Test
    fun `job claim is fenced idempotent and append only`() {
        val jobId = "job_fixture_00000001"
        val eventId = "evt_fixture_00000001"
        val partitionKey = "hmac-sha256:${"a".repeat(64)}"
        val payload =
            "{\"jobId\":\"$jobId\",\"ownerRef\":\"usr_demo_user\"," +
                "\"runId\":\"run_fixture_00000001\",\"contentHash\":\"sha256:${"b".repeat(64)}\"}"
        createAsyncRequest("decision", jobId, eventId, payload, partitionKey)
        val payloadHash = claimAndBindOutbox("decision", eventId)

        val token =
            connection("decision", WORKER_USER, WORKER_PASSWORD).use { worker ->
                worker
                    .prepareStatement(
                        "select job_id,claim_token,attempt_count from claim_async_job_by_event(?,?,?,?,?,?)",
                    ).use { statement ->
                        statement.setString(1, "worker:test")
                        statement.setString(2, eventId)
                        statement.setString(3, "model.eval-requested.v1")
                        statement.setString(4, jobId)
                        statement.setString(5, payloadHash)
                        statement.setString(6, partitionKey)
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
            val bulkClaimDenied =
                assertThrows<SQLException> {
                    worker.prepareStatement("select count(*) from claim_async_jobs(?,?)").use { statement ->
                        statement.setString(1, "worker:other")
                        statement.setInt(2, 100)
                        statement.executeQuery()
                    }
                }
            assertEquals("42501", bulkClaimDenied.sqlState)

            worker.prepareStatement("select commit_async_work(?,?,?,?,?,?::uuid,?,?,?)").use { statement ->
                statement.setString(1, eventId)
                statement.setString(2, "model.eval-requested.v1")
                statement.setString(3, "python-async-worker-v1")
                statement.setString(4, payloadHash)
                statement.setString(5, jobId)
                statement.setObject(6, token)
                statement.setString(7, "async_result_fixture_00000001")
                statement.setString(8, "evt_completed_fixture_00000001")
                statement.setString(9, partitionKey)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertEquals("COMPLETED", rows.getString(1))
                }
            }
            val legacyCompleteDenied =
                assertThrows<SQLException> {
                    worker.prepareStatement("select complete_async_job(?,?,?::jsonb)").use { statement ->
                        statement.setString(1, jobId)
                        statement.setObject(2, token)
                        statement.setString(3, "{}")
                        statement.executeQuery()
                    }
                }
            assertEquals("42501", legacyCompleteDenied.sqlState)
            val baseRead = assertThrows<SQLException> { worker.createStatement().use { it.executeQuery("select * from async_job") } }
            assertEquals("42501", baseRead.sqlState)
            val flywayRead =
                assertThrows<SQLException> { worker.createStatement().use { it.executeQuery("select * from flyway_schema_history") } }
            assertEquals("42501", flywayRead.sqlState)
        }

        val expiredJobId = "job_expired_claim_00000001"
        val expiredEventId = "evt_expired_claim_00000001"
        val expiredPartitionKey = "hmac-sha256:${"f".repeat(64)}"
        val expiredPayload =
            "{\"jobId\":\"$expiredJobId\",\"ownerRef\":\"usr_demo_user\"," +
                "\"runId\":\"run_expired_claim_0001\"," +
                "\"contentHash\":\"sha256:${"f".repeat(64)}\"}"
        createAsyncRequest("decision", expiredJobId, expiredEventId, expiredPayload, expiredPartitionKey)
        val expiredPayloadHash = claimAndBindOutbox("decision", expiredEventId)
        val expiredToken =
            connection("decision", WORKER_USER, WORKER_PASSWORD).use { worker ->
                worker.prepareStatement("select claim_token from claim_async_job_by_event(?,?,?,?,?,?)").use { statement ->
                    statement.setString(1, "worker:expired")
                    statement.setString(2, expiredEventId)
                    statement.setString(3, "model.eval-requested.v1")
                    statement.setString(4, expiredJobId)
                    statement.setString(5, expiredPayloadHash)
                    statement.setString(6, expiredPartitionKey)
                    statement.executeQuery().use { rows ->
                        assertTrue(rows.next())
                        rows.getObject(1, UUID::class.java)
                    }
                }
            }
        connection("decision", postgres.username, postgres.password).use { owner ->
            owner.createStatement().use {
                it.executeUpdate(
                    "update async_job set lease_expires_at=statement_timestamp()-interval '1 second'," +
                        "hard_deadline_at=statement_timestamp()-interval '1 second' where job_id='$expiredJobId'",
                )
            }
        }
        connection("decision", WORKER_USER, WORKER_PASSWORD).use { worker ->
            worker.prepareStatement("select fail_async_job(?,?,?,?)").use { statement ->
                statement.setString(1, expiredJobId)
                statement.setObject(2, expiredToken)
                statement.setString(3, "ASYNC_DB_RETRY")
                statement.setString(4, "RETRYABLE_TRANSIENT")
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertEquals("CONFLICT", rows.getString(1))
                }
            }
            worker.prepareStatement("select quarantine_async_work(?,?::uuid,?,?,?,?,?,?,?,?)").use { statement ->
                statement.setString(1, expiredJobId)
                statement.setObject(2, expiredToken)
                statement.setString(3, "evt_dlq_${"d".repeat(32)}")
                statement.setString(4, "evt_expired_claim_00000001")
                statement.setString(5, "model.eval-requested.v1")
                statement.setString(6, "sha256:${"e".repeat(64)}")
                statement.setString(7, "model.eval-requested.v1")
                statement.setInt(8, 1)
                statement.setString(9, "INVALID_EVENT_PAYLOAD")
                statement.setString(10, "hmac-sha256:${"c".repeat(64)}")
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertFalse(rows.getBoolean(1))
                }
            }
            worker.prepareStatement("select count(*) from claim_async_job_by_event(?,?,?,?,?,?)").use { statement ->
                statement.setString(1, "worker:expired-reclaim")
                statement.setString(2, expiredEventId)
                statement.setString(3, "model.eval-requested.v1")
                statement.setString(4, expiredJobId)
                statement.setString(5, expiredPayloadHash)
                statement.setString(6, expiredPartitionKey)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertEquals(0, rows.getInt(1))
                }
            }
        }
        connection("decision", postgres.username, postgres.password).use { owner ->
            owner.createStatement().use { statement ->
                statement.executeQuery("select status,error_code from async_job where job_id='$expiredJobId'").use { rows ->
                    assertTrue(rows.next())
                    assertEquals("NEEDS_REVIEW", rows.getString(1))
                    assertEquals("LEASE_EXPIRED", rows.getString(2))
                }
                statement
                    .executeQuery(
                        "select count(*) from async_job_transition_audit where job_id='$expiredJobId' " +
                            "and previous_status='RUNNING' and next_status='FAILED' and failure_code='LEASE_EXPIRED'",
                    ).use { rows ->
                        assertTrue(rows.next())
                        assertEquals(1, rows.getInt(1))
                    }
            }
        }

        val reclaimJobId = "job_reclaim_claim_00000001"
        val reclaimEventId = "evt_reclaim_claim_00000001"
        val reclaimPartitionKey = "hmac-sha256:${"9".repeat(64)}"
        val reclaimPayload =
            "{\"jobId\":\"$reclaimJobId\",\"ownerRef\":\"usr_demo_user\"," +
                "\"runId\":\"run_reclaim_claim_0001\",\"contentHash\":\"sha256:${"9".repeat(64)}\"}"
        createAsyncRequest("decision", reclaimJobId, reclaimEventId, reclaimPayload, reclaimPartitionKey)
        val reclaimPayloadHash = claimAndBindOutbox("decision", reclaimEventId)
        val firstReclaimToken =
            claimJob(reclaimEventId, reclaimJobId, reclaimPayloadHash, reclaimPartitionKey, "worker:reclaim-1")
        connection("decision", postgres.username, postgres.password).use { owner ->
            owner.createStatement().use {
                it.executeUpdate(
                    "update async_job set lease_expires_at=statement_timestamp()-interval '1 second'," +
                        "hard_deadline_at=statement_timestamp()+interval '10 minutes' where job_id='$reclaimJobId'",
                )
            }
        }
        val secondReclaimToken =
            claimJob(reclaimEventId, reclaimJobId, reclaimPayloadHash, reclaimPartitionKey, "worker:reclaim-2")
        assertFalse(firstReclaimToken == secondReclaimToken)
        connection("decision", postgres.username, postgres.password).use { owner ->
            owner.createStatement().use { statement ->
                statement.executeQuery("select status,attempt_count from async_job where job_id='$reclaimJobId'").use { rows ->
                    assertTrue(rows.next())
                    assertEquals("RUNNING", rows.getString(1))
                    assertEquals(2, rows.getInt(2))
                }
                statement
                    .executeQuery(
                        "select count(*) from async_job_transition_audit where job_id='$reclaimJobId' " +
                            "and previous_status='RUNNING' and next_status='FAILED' and failure_code='LEASE_EXPIRED'",
                    ).use { rows ->
                        assertTrue(rows.next())
                        assertEquals(1, rows.getInt(1))
                    }
            }
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
                statement.executeUpdate(
                    "update event_outbox set status='PUBLISHED',published_at=statement_timestamp()," +
                        "claim_token=null,claimed_by=null,lease_expires_at=null " +
                        "where event_id in ('evt_fixture_00000001','evt_completed_fixture_00000001')",
                )
            }
        }
    }

    @Test
    fun `outbox claim skips locked rows and unknown events never leave database`() {
        connection("decision", postgres.username, postgres.password).use { owner ->
            owner.createStatement().use { statement ->
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
            }
        }
        connection("decision", APP_USER, APP_PASSWORD).use { app ->
            app.createStatement().use { statement ->
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
        val eventId = "evt_admin_view_00000001"
        val payload =
            "{\"jobId\":\"$jobId\",\"ownerRef\":\"usr_demo_user\"," +
                "\"runId\":\"run_fixture_00000001\",\"contentHash\":\"sha256:${"f".repeat(64)}\"}"
        createAsyncRequest("decision", jobId, eventId, payload, "hmac-sha256:${"a".repeat(64)}")
        connection("decision", APP_USER, APP_PASSWORD).use { app ->
            app.prepareStatement("select count(*) from read_async_job_status_authorized(?,?,?,?)").use { statement ->
                statement.setString(
                    1,
                    issueCapability(
                        "decision",
                        "usr_demo_user",
                        ActorCapabilityBinding.target("READ_ASYNC_JOB", "ASYNC_JOB", jobId, ActorCapabilityRolePolicy.OWNER),
                    ),
                )
                statement.setString(2, "usr_demo_user")
                statement.setLong(3, 1)
                statement.setString(4, jobId)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertEquals(0, rows.getInt(1))
                }
            }
            app.prepareStatement("select job_id,status from read_async_job_status_authorized(?,?,?,?)").use { statement ->
                statement.setString(
                    1,
                    issueCapability(
                        "decision",
                        "usr_demo_admin",
                        ActorCapabilityBinding.target("READ_ASYNC_JOB", "ASYNC_JOB", jobId, ActorCapabilityRolePolicy.ADMIN_ONLY),
                    ),
                )
                statement.setString(2, "usr_demo_admin")
                statement.setLong(3, 1)
                statement.setString(4, jobId)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertEquals(jobId, rows.getString(1))
                    assertEquals("REQUESTED", rows.getString(2))
                    assertFalse(rows.next())
                }
            }
            app.prepareStatement("select job_id from list_async_job_status_authorized(?,?,?,?,?,?,?,?)").use { statement ->
                statement.setString(
                    1,
                    issueCapability(
                        "decision",
                        "usr_demo_admin",
                        ActorCapabilityBinding.request(
                            "LIST_ASYNC_JOBS",
                            "ASYNC_JOB_LIST",
                            "async-jobs",
                            ActorCapabilityRolePolicy.ADMIN_ONLY,
                            "REQUESTED",
                            "MODEL_EVAL",
                            null,
                            null,
                            "51",
                        ),
                    ),
                )
                statement.setString(2, "usr_demo_admin")
                statement.setLong(3, 1)
                statement.setString(4, "REQUESTED")
                statement.setString(5, "MODEL_EVAL")
                statement.setObject(6, null)
                statement.setObject(7, null)
                statement.setInt(8, 51)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertEquals(jobId, rows.getString(1))
                    assertFalse(rows.next())
                }
            }
            app.prepareStatement("select count(*) from read_async_job_status_authorized(?,?,?,?)").use { statement ->
                statement.setString(
                    1,
                    issueCapability(
                        "decision",
                        "usr_demo_admin",
                        ActorCapabilityBinding.target("READ_ASYNC_JOB", "ASYNC_JOB", jobId, ActorCapabilityRolePolicy.ADMIN_ONLY),
                    ),
                )
                statement.setString(2, "usr_demo_admin")
                statement.setLong(3, 999)
                statement.setString(4, jobId)
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
        val originalEventId = "evt_poison_00000001"
        val dlqEventId =
            "evt_dlq_" +
                HexFormat
                    .of()
                    .formatHex(MessageDigest.getInstance("SHA-256").digest("artifact.ingest-requested.v1|1|42".toByteArray()))
                    .take(32)
        val payloadHash = "sha256:" + "b".repeat(64)
        val partitionKey = "hmac-sha256:" + "c".repeat(64)
        connection("decision", WORKER_USER, WORKER_PASSWORD).use { worker ->
            worker.prepareStatement("select record_kafka_poison(?,?,?,?,?,?,?,?,?)").use { statement ->
                statement.setString(1, originalEventId)
                statement.setString(2, "artifact.ingest-requested.v1")
                statement.setString(3, payloadHash)
                statement.setString(4, "artifact.ingest-requested.v1")
                statement.setInt(5, 1)
                statement.setLong(6, 42)
                statement.setInt(7, 1)
                statement.setString(8, "INVALID_EVENT_PAYLOAD")
                statement.setString(9, partitionKey)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertTrue(rows.getBoolean(1))
                }
            }
        }
        connection("decision", postgres.username, postgres.password).use { owner ->
            owner.createStatement().use {
                it.executeUpdate(
                    "insert into kafka_poison_receipt(source_topic,source_partition,source_offset,event_id,payload_hash,failure_code) " +
                        "select 'artifact.ingest-requested.v1',2,value,'evt_quota_'||lpad(value::text,12,'0')," +
                        "'sha256:${"d".repeat(64)}','INVALID_EVENT_PAYLOAD' from generate_series(1,999) value",
                )
            }
        }
        connection("decision", WORKER_USER, WORKER_PASSWORD).use { worker ->
            worker.prepareStatement("select record_kafka_poison(?,?,?,?,?,?,?,?,?)").use { statement ->
                statement.setString(1, originalEventId)
                statement.setString(2, "artifact.ingest-requested.v1")
                statement.setString(3, payloadHash)
                statement.setString(4, "artifact.ingest-requested.v1")
                statement.setInt(5, 1)
                statement.setLong(6, 42)
                statement.setInt(7, 1)
                statement.setString(8, "INVALID_EVENT_PAYLOAD")
                statement.setString(9, partitionKey)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertFalse(rows.getBoolean(1))
                }
            }
            val novelDenied =
                assertThrows<SQLException> {
                    worker.prepareStatement("select record_kafka_poison(?,?,?,?,?,?,?,?,?)").use { statement ->
                        statement.setString(1, "evt_poison_novel_0001")
                        statement.setString(2, "artifact.ingest-requested.v1")
                        statement.setString(3, payloadHash)
                        statement.setString(4, "artifact.ingest-requested.v1")
                        statement.setInt(5, 1)
                        statement.setLong(6, 43)
                        statement.setInt(7, 1)
                        statement.setString(8, "INVALID_EVENT_PAYLOAD")
                        statement.setString(9, partitionKey)
                        statement.executeQuery()
                    }
                }
            assertEquals("54000", novelDenied.sqlState)
        }
        connection("decision", postgres.username, postgres.password).use { owner ->
            owner.createStatement().use {
                it.executeUpdate("delete from kafka_poison_receipt where source_partition=2")
            }
        }
        val claimToken =
            connection("decision", APP_USER, APP_PASSWORD).use { app ->
                app
                    .prepareStatement(
                        "select storage_event_id,event_id,topic_name,payload_json::text,claim_token from claim_dlq_outbox(?,?)",
                    ).use { statement ->
                        statement.setString(1, "kafka-publisher-test")
                        statement.setInt(2, 100)
                        statement.executeQuery().use { rows ->
                            assertTrue(rows.next())
                            assertEquals(dlqEventId, rows.getString(1))
                            assertTrue(rows.getString(2).startsWith("evt_dlq_"))
                            assertEquals("artifact.ingest-requested.dlq.v1", rows.getString(3))
                            val payload = rows.getString(4)
                            assertTrue(payload.contains(payloadHash))
                            assertFalse(payload.contains("secret"))
                            rows.getObject(5, java.util.UUID::class.java)
                        }
                    }
            }
        connection("decision", postgres.username, postgres.password).use { owner ->
            owner.createStatement().use {
                it.executeUpdate(
                    "update event_outbox set lease_expires_at=statement_timestamp()-interval '1 second' where event_id='$dlqEventId'",
                )
            }
        }
        connection("decision", APP_USER, APP_PASSWORD).use { app ->
            app.prepareStatement("select fail_dlq_outbox(?,?)").use { statement ->
                statement.setString(1, dlqEventId)
                statement.setObject(2, claimToken)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertFalse(rows.getBoolean(1))
                }
            }
            app.prepareStatement("select complete_dlq_outbox(?,?)").use { statement ->
                statement.setString(1, dlqEventId)
                statement.setObject(2, claimToken)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertFalse(rows.getBoolean(1))
                }
            }
        }
        connection("decision", postgres.username, postgres.password).use { owner ->
            owner.createStatement().use {
                it.executeUpdate(
                    "update event_outbox set lease_expires_at=statement_timestamp()+interval '30 seconds' where event_id='$dlqEventId'",
                )
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
                    app.prepareStatement("select record_kafka_poison(?,?,?,?,?,?,?,?,?)").use { statement ->
                        (1..4).forEach { statement.setString(it, "x") }
                        statement.setInt(5, 1)
                        statement.setLong(6, 42)
                        statement.setInt(7, 1)
                        statement.setString(8, "X")
                        statement.setString(9, "x")
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

    private fun issueCapability(
        databaseName: String,
        actorUserId: String,
    ): String =
        connection(databaseName, IDENTITY_USER, IDENTITY_PASSWORD).use { identity ->
            identity.prepareStatement("select issue_actor_request_capability(?)").use { statement ->
                statement.setString(1, actorUserId)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    requireNotNull(rows.getString(1))
                }
            }
        }

    private fun issueCapability(
        databaseName: String,
        actorUserId: String,
        binding: ActorCapabilityBinding,
    ): String {
        val sessionHandle =
            connection(databaseName, AUTH_USER, AUTH_PASSWORD).use { auth ->
                auth.prepareStatement("select session_handle from authenticate_demo_actor_session_v1(?,?,43200)").use { statement ->
                    statement.setString(1, if (actorUserId == "usr_demo_admin") "demo-admin" else "demo-user")
                    statement.setString(
                        2,
                        if (actorUserId == "usr_demo_admin") {
                            SpringApiIntegrationTestBase.TEST_ADMIN_PASSWORD
                        } else {
                            SpringApiIntegrationTestBase.TEST_USER_PASSWORD
                        },
                    )
                    statement.executeQuery().use { rows ->
                        assertTrue(rows.next())
                        requireNotNull(rows.getString(1))
                    }
                }
            }
        val identityHandle =
            connection(databaseName, AUTH_USER, AUTH_PASSWORD).use { auth ->
                auth.prepareStatement("select register_actor_identity_handle_v1(?,?,?,?,?,?,15)").use { statement ->
                    statement.setString(1, sessionHandle)
                    statement.setString(2, binding.operation)
                    statement.setString(3, binding.targetKind)
                    statement.setString(4, binding.targetId)
                    statement.setString(5, binding.payloadHash)
                    statement.setString(6, binding.rolePolicy.name)
                    statement.executeQuery().use { rows ->
                        assertTrue(rows.next())
                        requireNotNull(rows.getString(1))
                    }
                }
            }
        return DatabaseActorCapabilityAuthority(
            dataSource =
                org.springframework.jdbc.datasource.DriverManagerDataSource(
                    jdbcUrl(databaseName),
                    IDENTITY_USER,
                    IDENTITY_PASSWORD,
                ),
            privateKey = ACTOR_CAPABILITY_KEY_PAIR.private,
            publicKey = ACTOR_CAPABILITY_KEY_PAIR.public,
        ).issue(identityHandle, binding)
    }

    private fun claimJob(
        eventId: String,
        jobId: String,
        payloadHash: String,
        partitionKey: String,
        workerId: String,
    ): UUID =
        connection("decision", WORKER_USER, WORKER_PASSWORD).use { worker ->
            worker.prepareStatement("select claim_token from claim_async_job_by_event(?,?,?,?,?,?)").use { statement ->
                statement.setString(1, workerId)
                statement.setString(2, eventId)
                statement.setString(3, "model.eval-requested.v1")
                statement.setString(4, jobId)
                statement.setString(5, payloadHash)
                statement.setString(6, partitionKey)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    rows.getObject(1, UUID::class.java)
                }
            }
        }

    private fun createAsyncRequest(
        databaseName: String,
        jobId: String,
        eventId: String,
        payload: String,
        partitionKey: String,
    ) {
        connection(databaseName, APP_USER, APP_PASSWORD).use { app ->
            app.prepareStatement("select create_async_request_authorized(?,?,?,?,?,?,?,?)").use { statement ->
                statement.setString(
                    1,
                    issueCapability(
                        databaseName,
                        "usr_demo_user",
                        ActorCapabilityBinding.request(
                            "CREATE_ASYNC_REQUEST",
                            "ASYNC_JOB",
                            jobId,
                            ActorCapabilityRolePolicy.OWNER,
                            eventId,
                            "model.eval-requested.v1",
                            partitionKey,
                            jobId,
                            "MODEL_EVAL",
                            "usr_demo_user",
                            payload,
                        ),
                    ),
                )
                statement.setString(2, eventId)
                statement.setString(3, "model.eval-requested.v1")
                statement.setString(4, partitionKey)
                statement.setString(5, jobId)
                statement.setString(6, "MODEL_EVAL")
                statement.setString(7, "usr_demo_user")
                statement.setString(8, payload)
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertTrue(rows.getBoolean(1))
                }
            }
        }
    }

    private fun claimAndBindOutbox(
        databaseName: String,
        eventId: String,
    ): String =
        connection(databaseName, APP_USER, APP_PASSWORD).use { app ->
            app
                .prepareStatement(
                    "select event_id,payload_json::text,claim_token from claim_db_async_outbox(?,?) where event_id=?",
                ).use { statement ->
                    statement.setString(1, "spring-db-test")
                    statement.setInt(2, 100)
                    statement.setString(3, eventId)
                    statement.executeQuery().use { rows ->
                        assertTrue(rows.next())
                        val payloadHash =
                            connection(databaseName, postgres.username, postgres.password).use { owner ->
                                owner.prepareStatement("select 'sha256:'||encode(digest(?,'sha256'),'hex')").use { hash ->
                                    hash.setString(1, rows.getString(2))
                                    hash.executeQuery().use { result ->
                                        assertTrue(result.next())
                                        result.getString(1)
                                    }
                                }
                            }
                        app.prepareStatement("select bind_claimed_outbox_payload_hash(?,?,?)").use { bind ->
                            bind.setString(1, eventId)
                            bind.setObject(2, rows.getObject(3, UUID::class.java))
                            bind.setString(3, payloadHash)
                            bind.executeQuery().use { result ->
                                assertTrue(result.next())
                                assertTrue(result.getBoolean(1))
                            }
                        }
                        payloadHash
                    }
                }
        }

    private fun authorizeReplay(
        databaseName: String,
        batch: String,
        targetIds: Array<String>,
        expected: Int,
        execute: Boolean,
        securityVersion: Long,
        packetHash: String,
    ) {
        connection(databaseName, REPLAY_AUTHORIZER_USER, REPLAY_AUTHORIZER_PASSWORD).use { authorizer ->
            authorizer.prepareStatement("select authorize_async_replay(?,?,?,?,?,?::text[],?,?,?,?,?)").use { statement ->
                val issuedAt = java.time.OffsetDateTime.now(java.time.ZoneOffset.UTC)
                statement.setString(1, packetHash)
                statement.setString(2, "usr_demo_admin")
                statement.setLong(3, securityVersion)
                statement.setString(4, batch)
                statement.setString(5, "EVENT")
                statement.setArray(6, authorizer.createArrayOf("text", targetIds))
                statement.setInt(7, expected)
                statement.setString(8, "OPERATOR_RECOVERY")
                statement.setBoolean(9, execute)
                statement.setObject(10, issuedAt)
                statement.setObject(11, issuedAt.plusMinutes(5))
                statement.executeQuery().use { rows ->
                    assertTrue(rows.next())
                    assertTrue(rows.getBoolean(1))
                }
            }
        }
    }

    private fun sha256Hex(value: ByteArray): String = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value))

    private fun jdbcUrl(databaseName: String): String = postgres.jdbcUrl.replace("/decision", "/$databaseName")

    @Test
    fun `V87 provider approval is atomic one-shot and replay role has no table access`() {
        val packetHash = "sha256:" + "1".repeat(64)
        val approvalHash = "sha256:" + "2".repeat(64)
        val nonceHash = "sha256:" + "3".repeat(64)
        val operationHash = "sha256:" + "4".repeat(64)
        connection("decision", REPLAY_USER, REPLAY_PASSWORD).use { replay ->
            replay.createStatement().use { statement ->
                val sql =
                    "select consume_p1_provider_approval(" +
                        "'$packetHash','$approvalHash','$nonceHash','$operationHash',8," +
                        "statement_timestamp() + interval '5 minutes')"
                assertTrue(booleanResult(statement, sql))
                assertFalse(booleanResult(statement, sql))
                val denied =
                    assertThrows<SQLException> {
                        statement.executeQuery("select count(*) from p1_provider_approval_claim")
                    }
                assertEquals("42501", denied.sqlState)
            }
        }
        connection("decision", APP_USER, APP_PASSWORD).use { app ->
            val denied =
                assertThrows<SQLException> {
                    app.createStatement().use {
                        it.executeQuery(
                            "select consume_p1_provider_approval(" +
                                "'sha256:${"5".repeat(64)}','sha256:${"6".repeat(64)}'," +
                                "'sha256:${"7".repeat(64)}','sha256:${"8".repeat(64)}',1," +
                                "statement_timestamp() + interval '5 minutes')",
                        )
                    }
                }
            assertEquals("42501", denied.sqlState)
        }
    }

    private fun booleanResult(
        statement: java.sql.Statement,
        sql: String,
    ): Boolean =
        statement.executeQuery(sql).use { rows ->
            assertTrue(rows.next())
            rows.getBoolean(1)
        }

    companion object {
        private const val APP_USER = "decision_app"
        private const val APP_PASSWORD = "app-test"
        private const val WORKER_USER = "decision_worker"
        private const val WORKER_PASSWORD = "worker-test-secret-0001"
        private const val OUTBOX_USER = "decision_outbox_publisher"
        private const val OUTBOX_PASSWORD = "outbox-publisher-test-0001"
        private const val POISON_USER = "decision_poison_recorder"
        private const val POISON_PASSWORD = "poison-recorder-test-0001"
        private const val REPLAY_USER = "decision_replay"
        private const val REPLAY_PASSWORD = "replay-test-secret-0001"
        private const val IDENTITY_USER = "decision_identity"
        private const val IDENTITY_PASSWORD = "identity-test-secret-0001"
        private const val AUTH_USER = "decision_auth"
        private const val AUTH_PASSWORD = "auth-test-secret-0001"
        private const val REPLAY_AUTHORIZER_USER = "decision_replay_authorizer"
        private const val REPLAY_AUTHORIZER_PASSWORD = "replay-authorizer-test-0001"
        private const val DEMO_USER = "decision_demo"
        private const val DEMO_PASSWORD = "demo-test-secret-0001"
        private val ACTOR_CAPABILITY_KEY_PAIR = KeyPairGenerator.getInstance("Ed25519").generateKeyPair()
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
