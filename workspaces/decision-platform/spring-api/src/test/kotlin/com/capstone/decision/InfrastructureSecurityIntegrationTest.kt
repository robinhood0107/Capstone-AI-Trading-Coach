package com.capstone.decision

import com.capstone.decision.infrastructure.risk.ActorScopedReadQuery
import com.zaxxer.hikari.HikariConfig
import com.zaxxer.hikari.HikariDataSource
import org.flywaydb.core.Flyway
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.springframework.beans.factory.support.StaticListableBeanFactory
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
import javax.sql.DataSource

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

        val migratedPrivilegeFingerprint =
            DriverManager.getConnection(postgres.jdbcUrl, postgres.username, adminPassword).use(::privilegeFingerprint)
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, adminPassword).use { connection ->
            connection.createStatement().use { statement ->
                // bootstrap은 우연히 남은 권한에 기대지 않고 V13~V15 exact matrix를 다시 만들어야 한다.
                statement.execute("grant select on table order_fill_observations to decision_app")
                statement.execute("grant execute on function initialize_order_fill_projection() to decision_app")
                statement.execute("grant execute on function read_decision_owner_projection() to public")
                statement.execute("grant execute on function read_decision_owner_projection() to decision_fill_writer")
            }
        }
        // 기존 volume에서 bootstrap을 재적용해도 migration의 calendar·Principle 최소권한을 되돌리면 안 된다.
        val bootstrapResult = postgres.execInContainer("bash", "-ec", "bash /tmp/02-application-roles.sh")
        assertEquals(
            0,
            bootstrapResult.exitCode,
            "role bootstrap failed after migration: stdout=${bootstrapResult.stdout} " +
                "stderr=${bootstrapResult.stderr}",
        )

        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, adminPassword).use { connection ->
            val bootstrappedPrivilegeFingerprint = privilegeFingerprint(connection)
            connection.createStatement().use { statement ->
                val thresholdDefinition =
                    queryScalar(
                        statement,
                        "select pg_get_functiondef('public.evaluate_rag_v2_immutable_public_voyage_component_v45_unlinked(text,jsonb)'::regprocedure)",
                    )
                val ledgerDefinition =
                    queryScalar(
                        statement,
                        "select pg_get_functiondef('public.evaluate_rag_v2_immutable_public_voyage_component(text,jsonb)'::regprocedure)",
                    )
                assertTrue(thresholdDefinition.contains("WHEN 'EXACT30' THEN 1"))
                assertTrue(thresholdDefinition.contains("WHEN 'OA112' THEN 1"))
                assertFalse(thresholdDefinition.contains("WHEN 'EXACT30' THEN 10"))
                assertFalse(thresholdDefinition.contains("WHEN 'OA112' THEN 112"))
                assertTrue(
                    ledgerDefinition.contains(
                        "CASE generation_scope WHEN 'EXACT30' THEN 1 WHEN 'OA112' THEN 1 ELSE -1 END",
                    ),
                )
            }
            assertEquals(
                emptySet<String>(),
                migratedPrivilegeFingerprint.toSet() - bootstrappedPrivilegeFingerprint.toSet(),
                "bootstrap removed expected runtime privileges",
            )
            assertEquals(
                emptySet<String>(),
                bootstrappedPrivilegeFingerprint.toSet() - migratedPrivilegeFingerprint.toSet(),
                "bootstrap retained or added unexpected runtime privileges",
            )
            assertNoPublicCustomObjectPrivileges(connection)
            assertTrue(hasTablePrivilege(connection, "decision_collector", "opendart_quota_usage", "UPDATE"))
            assertTrue(hasTablePrivilege(connection, "decision_collector", "calendar_observations", "INSERT"))
            assertFalse(hasTablePrivilege(connection, "decision_collector", "calendar_observations", "UPDATE"))
            assertFalse(hasTablePrivilege(connection, "decision_collector", "flyway_schema_history", "SELECT"))
            assertTrue(hasTablePrivilege(connection, "decision_app", "trading_sessions", "SELECT"))
            listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(hasTablePrivilege(connection, "decision_app", "users", privilege))
            }
            assertFalse(hasTablePrivilege(connection, "decision_app", "calendar_observations", "SELECT"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "opendart_quota_usage", "SELECT"))

            listOf(
                "issue_rag_rpc_scope(text,text,jsonb)",
                "recheck_rag_rpc_citations(text,text,text,text,bigint,text,text,jsonb)",
                "read_rag_v2_corpus_status(text)",
                "read_rag_v2_history_metadata(text,timestamp with time zone,text,integer)",
                "read_rag_v2_history_detail(text,text)",
                "delete_owned_rag_v2_history(text,text)",
                "record_rag_v2_immutable_consent(text,text,text,text)",
                "record_rag_v2_immutable_consent_v2(text,text,text,text,text,text,text)",
                "read_rag_v2_immutable_effective_consent(text)",
                "issue_rag_v2_immutable_import_ticket(text,text,text,text)",
                "issue_rag_v2_immutable_import_ticket_v2(text,text,text,text,text)",
                "issue_rag_v2_immutable_owner_delete_ticket(text,text,text)",
                "issue_rag_v2_retrieval_scope_v2(text,text,text[])",
                "issue_rag_v2_retrieval_scope_v3(text,text,text[])",
                "read_rag_v2_vertex_prepared_scope_v2(text,text,text,text[])",
                "authorize_s4_9_runtime_voyage_query(text,text,text)",
            ).forEach { function ->
                assertTrue(
                    hasFunctionPrivilege(connection, "decision_app", function),
                    "bootstrap removed the RAG owner-scoped function grant for $function",
                )
            }
            assertFalse(
                hasFunctionPrivilege(
                    connection,
                    "decision_app",
                    "delete_owner_rag_v2_document(text,text,text,text)",
                ),
                "V25 must not leave the V24 delete-before-replacement capability callable",
            )
            assertTrue(
                hasFunctionPrivilege(
                    connection,
                    "decision_rag_writer",
                    "consume_rag_v2_immutable_import_ticket(text,text,text,text,text)",
                ),
            )
            listOf(
                "stage_rag_v2_immutable_public_bge_document(jsonb)",
                "evaluate_rag_v2_immutable_public_bge_component(text,jsonb)",
                "stage_rag_v2_immutable_external_exact30_voyage_document(jsonb)",
                "reserve_rag_v2_immutable_voyage_query_usage(text,text,text,text,text,text,text,timestamp with time zone,integer,integer,bigint,bigint)",
                "claim_rag_v2_immutable_voyage_query_usage_attempt(text)",
                "commit_rag_v2_immutable_voyage_query_usage(text,integer,bigint)",
                "mark_rag_v2_immutable_voyage_query_usage_unknown_billing(text)",
                "reserve_s4_9_runtime_voyage_query_usage(text,text,text)",
                "reserve_rag_v2_immutable_voyage_usage_with_tokenizer(text,text,text,text,text,text,timestamp with time zone,integer,integer,bigint,bigint)",
                "commit_rag_v2_immutable_voyage_usage_with_tokenizer(text,integer,integer,bigint)",
                "reserve_rag_v2_immutable_voyage_query_usage_with_tokenizer(text,text,text,text,text,text,text,text,timestamp with time zone,integer,integer,bigint,bigint)",
                "commit_rag_v2_immutable_voyage_query_usage_with_tokenizer(text,integer,integer,bigint)",
                "reserve_rag_v2_immutable_voyage_document_batch_usage(text,text,text,text,text,text,timestamp with time zone,integer,integer,bigint,bigint)",
                "claim_rag_v2_immutable_voyage_document_batch_attempt(text,text,text,text)",
                "mark_rag_v2_immutable_voyage_document_batch_unknown_billing(text,text,text)",
                "commit_and_stage_rag_v2_immutable_voyage_document_batch(jsonb)",
                "load_rag_v2_immutable_voyage_document_batch_vectors(text)",
                "reserve_rag_v2_immutable_voyage_evaluation_batch_usage(text,text,text,text,text,text,text,text,timestamp with time zone,integer,integer,bigint,bigint)",
                "claim_rag_v2_immutable_voyage_evaluation_batch_attempt(text,text,text,text,text)",
                "mark_rag_v2_immutable_voyage_evaluation_batch_unknown_billing(text,text,text)",
                "commit_and_stage_rag_v2_immutable_voyage_evaluation_batch(jsonb)",
                "load_rag_v2_immutable_voyage_evaluation_batch_vectors(text,text,text)",
                "record_rag_v2_bge_public_execution_supersession(text,text)",
                "stage_rag_v2_immutable_owner_document_v3(text,text,jsonb)",
                "reserve_rag_v2_owner_voyage_import(text,text,text,text,text,text,text[],integer,integer,integer)",
                "complete_rag_v2_owner_voyage_import(text,text,text,jsonb,integer,integer,bigint)",
                "fail_rag_v2_owner_voyage_import_unknown_billing(text,text,text,text)",
            ).forEach { function ->
                assertTrue(
                    hasFunctionPrivilege(connection, "decision_rag_writer", function),
                    "bootstrap removed the public RAG writer capability for $function",
                )
                assertFalse(
                    hasFunctionPrivilege(connection, "decision_app", function),
                    "public RAG writer capability leaked to the app role for $function",
                )
            }
            listOf(
                "read_rag_v2_retrieval_scope_v2(text,text,text)",
                "read_rag_v2_retrieval_scope_by_claim_v2(text,text)",
                "search_authorized_rag_v2_dense_v2(text,text,text,text[],vector,vector)",
            ).forEach { function ->
                assertTrue(
                    hasFunctionPrivilege(connection, "decision_rag_query", function),
                    "bootstrap removed the owner dual-profile query capability for $function",
                )
                assertFalse(
                    hasFunctionPrivilege(connection, "decision_app", function),
                    "owner dual-profile query capability leaked to the app role for $function",
                )
            }
            listOf(
                "activate_rag_v2_immutable_public_base(text,text,bigint,text)",
                "activate_rag_v2_immutable_owner_bundle(text,text,text,bigint,text,text)",
                "delete_rag_v2_immutable_owner_document_with_ticket(text,text,text,text,text,text)",
            ).forEach { function ->
                assertTrue(hasFunctionPrivilege(connection, "decision_rag_admin", function))
                assertFalse(hasFunctionPrivilege(connection, "decision_app", function))
            }
            listOf(
                "delete_rag_v2_immutable_owner_document(text,text,text,text,bigint,text,text,text)",
                "replace_and_delete_rag_v2_immutable_owner_document(text,text,text,text,text)",
            ).forEach { function ->
                assertFalse(hasFunctionPrivilege(connection, "decision_rag_admin", function))
                assertFalse(hasFunctionPrivilege(connection, "decision_app", function))
            }
            listOf(
                "rag_v2_immutable_oa_track_catalog",
                "rag_v2_immutable_oa_source_cards",
                "rag_v2_immutable_public_component_evaluations",
                "rag_v2_immutable_public_component_manifests",
                "rag_v2_immutable_exact30_source_allowlist",
                "rag_v2_immutable_external_exact30_source_allowlist",
                "rag_v2_immutable_external_exact30_voyage_component_manifests",
                "rag_v2_immutable_voyage_query_usage_reservations",
                "rag_v2_immutable_voyage_query_usage_attempts",
                "rag_v2_immutable_voyage_query_usage_outcomes",
                "rag_v2_immutable_source_revisions",
                "rag_v2_immutable_chunks",
                "rag_v2_immutable_component_generations",
                "rag_v2_immutable_generation_memberships",
                "rag_v2_immutable_generation_embeddings",
                "rag_v2_immutable_embedding_cache",
                "rag_v2_immutable_materialization_runs",
                "rag_v2_immutable_source_receipts",
                "rag_v2_immutable_chunk_receipts",
                "rag_v2_immutable_embedding_receipts",
                "rag_v2_immutable_public_bundle_pointers",
                "rag_v2_immutable_bundles",
                "rag_v2_immutable_owner_bundle_pointers",
                "rag_v2_immutable_consent_events",
                "rag_v2_immutable_import_tickets",
                "rag_v2_immutable_owner_delete_tickets",
                "rag_v2_immutable_activation_receipts",
                "rag_v2_immutable_deletion_receipts",
                "rag_v2_immutable_owner_document_deletion_tombstones",
            ).forEach { table ->
                listOf("decision_app", "decision_rag_writer", "decision_rag_admin", "decision_rag_query").forEach { role ->
                    listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                        assertFalse(
                            hasTablePrivilege(connection, role, table, privilege),
                            "unexpected V25 immutable RAG $privilege on $table for $role",
                        )
                    }
                }
            }
            listOf(
                "latest_cross_market_observations",
                "latest_analyst_revision_evidence",
                "latest_market_cause_evidence",
                "latest_cross_market_risk_snapshots",
            ).forEach { view ->
                assertTrue(
                    hasTablePrivilege(connection, "decision_app", view, "SELECT"),
                    "bootstrap removed the S4.8 bounded read grant for $view",
                )
            }
            listOf(
                "append_market_source_entitlement(jsonb)",
                "append_cross_market_exposure_catalog_entry(jsonb)",
                "append_cross_market_observation(jsonb)",
                "append_analyst_revision_evidence(jsonb)",
                "append_market_cause_evidence(jsonb)",
            ).forEach { function ->
                assertTrue(
                    hasFunctionPrivilege(connection, "decision_market_writer", function),
                    "bootstrap removed the S4.8 append-only writer grant for $function",
                )
            }

            assertTrue(hasTablePrivilege(connection, "decision_app", "principle_presets", "SELECT"))
            listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(hasTablePrivilege(connection, "decision_app", "principles", privilege))
                assertFalse(hasTablePrivilege(connection, "decision_app", "principle_versions", privilege))
            }
            listOf(
                "insert_principle_authorized(text,text,text,text,text,text,text,integer,timestamp with time zone,timestamp with time zone)",
                "insert_principle_version_authorized_v2(text,text,text,text,integer,text,text,text,text,text,text,timestamp with time zone)",
                "insert_principle_audit_authorized_v2(text,text,text,text,text,integer,text,timestamp with time zone)",
                "read_owned_principle_authorized(text,text,text)",
                "list_owned_principles_authorized(text,text,integer,text,timestamp with time zone,text)",
                "update_owned_principle_authorized(text,text,text,integer,text,text,text,timestamp with time zone)",
                "list_owned_principle_versions_authorized(text,text,text,integer,text,integer)",
                "read_active_owned_principle_snapshot_authorized(text,text,text)",
            ).forEach { function ->
                assertTrue(
                    hasFunctionPrivilege(connection, "decision_app", function),
                    "decision_app must retain the owner-scoped Principle capability $function",
                )
            }
            listOf(
                "insert_principle_version_authorized(text,text,text,text,integer,text,text,text,text,jsonb,text[],timestamp with time zone)",
                "insert_principle_audit_authorized(text,text,text,text,text,integer,text[],timestamp with time zone)",
                "lock_active_owned_principle_authorized(text,text,text,integer,text,text)",
            ).forEach { function ->
                assertFalse(hasFunctionPrivilege(connection, "decision_app", function))
            }
            assertFalse(hasTablePrivilege(connection, "decision_app", "audit_logs", "INSERT"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "audit_logs", "SELECT"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "audit_logs", "UPDATE"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "audit_logs", "DELETE"))
            listOf("INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(hasTablePrivilege(connection, "decision_app", "principle_presets", privilege))
            }
            assertFalse(hasTablePrivilege(connection, "decision_app", "audit_logs", "TRUNCATE"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "decisions", "INSERT"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "decisions", "SELECT"))
            listOf("UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(hasTablePrivilege(connection, "decision_app", "decisions", privilege))
            }
            assertTrue(hasTablePrivilege(connection, "decision_app", "decision_owner_projection", "SELECT"))
            assertTrue(hasTablePrivilege(connection, "decision_app", "decision_audit_projection", "SELECT"))
            assertTrue(hasTablePrivilege(connection, "decision_app", "risk_kill_switch", "SELECT"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "risk_kill_switch_transitions", "INSERT"))
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
                assertFalse(hasColumnPrivilege(connection, "decision_app", "risk_kill_switch", column, "UPDATE"))
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
                "read_kill_switch_audit_projection()",
                "read_decision_usability()",
                "transition_kill_switch_authorized(text,text,bigint,boolean,bigint,text)",
                "persist_decision_bundle_authorized_v2(text,text)",
                "read_mock_order_decision(text,text,text)",
                "find_mock_order_idempotency_result(text,text,timestamp with time zone,text)",
                "read_mock_order_owner_projection(text,text,text)",
                "create_mock_order(jsonb,text)",
                "request_mock_order_cancel(jsonb,text)",
            ).forEach { function ->
                assertTrue(hasFunctionPrivilege(connection, "decision_app", function))
            }
            listOf(
                "revalidate_kill_switch_admin(text,bigint)",
                "invalidate_unused_decisions_for_kill_switch(bigint,timestamp with time zone,text)",
                "append_decision_created_outbox(text,text,jsonb,timestamp with time zone)",
                "append_kill_switch_outbox(text,boolean,timestamp with time zone)",
                "read_demo_credentials()",
                "read_user_actor(text)",
                "persist_decision_bundle_authorized(text,jsonb)",
            ).forEach { function ->
                assertFalse(hasFunctionPrivilege(connection, "decision_app", function))
            }
            assertTrue(hasFunctionPrivilege(connection, "decision_auth", "read_demo_credentials()"))
            assertTrue(hasFunctionPrivilege(connection, "decision_auth", "read_user_actor(text)"))
            listOf("users", "decisions", "audit_logs", "flyway_schema_history").forEach { table ->
                listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                    assertFalse(hasTablePrivilege(connection, "decision_auth", table, privilege))
                }
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
            assertTrue(hasTablePrivilege(connection, "decision_app", "paper_margin_owner_projection", "SELECT"))
            assertFalse(hasTablePrivilege(connection, "decision_app", "order_fill_observations", "SELECT"))
            assertFalse(
                hasFunctionPrivilege(connection, "decision_app", "initialize_order_fill_projection()"),
            )
            assertFalse(
                hasFunctionPrivilege(connection, "decision_fill_writer", "read_decision_owner_projection()"),
            )
        }

        DriverManager
            .getConnection(
                postgres.jdbcUrl,
                Properties().apply {
                    setProperty("user", "decision_auth")
                    setProperty("password", authPassword)
                },
            ).use { connection ->
                connection.createStatement().use { statement ->
                    assertEquals(
                        "2",
                        queryScalar(statement, "select count(*)::text from read_demo_credentials()"),
                    )
                    val denied = assertThrows<SQLException> { statement.executeQuery("select * from users") }
                    assertEquals("42501", denied.sqlState)
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
                }
            }

        DriverManager.getConnection(postgres.jdbcUrl, runtimeProperties()).use { connection ->
            connection.createStatement().use { statement ->
                assertEquals("2s", queryScalar(statement, "show statement_timeout"))
                assertEquals("500ms", queryScalar(statement, "show lock_timeout"))
                assertEquals("5s", queryScalar(statement, "show idle_in_transaction_session_timeout"))
                val timeoutFailure = assertThrows<SQLException> { statement.execute("select pg_sleep(3)") }
                assertEquals("57014", timeoutFailure.sqlState)
                assertEquals("1", queryScalar(statement, "select 1"))
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
                    "insert into decisions default values",
                    "update decisions set outcome = outcome where false",
                    "delete from decisions where false",
                    "truncate table decisions",
                    "insert into risk_kill_switch (" +
                        "kill_switch_id,active,reason_class,generation,changed_by,changed_by_role,changed_at" +
                        ") values ('OTHER',true,'USER_MANUAL_STOP',2,'usr_demo_user','USER',now())",
                    "update risk_kill_switch set active = active where kill_switch_id = 'GLOBAL'",
                    "delete from risk_kill_switch where false",
                    "insert into risk_kill_switch_transitions default values",
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
                    "select * from read_demo_credentials()",
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
        assertRuntimeLockTimeout()
        assertActorScopedPoolStateIsReset()
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

    private fun privilegeFingerprint(connection: Connection): List<String> =
        connection
            .createStatement()
            .use { statement ->
                statement
                    .executeQuery(
                        """
                        WITH runtime_roles AS (
                          SELECT oid, rolname
                          FROM pg_roles
                          WHERE rolname = ANY (ARRAY[
                            'decision_app',
                            'decision_auth',
                            'decision_worker',
                            'decision_outbox_publisher',
                            'decision_poison_recorder',
                            'decision_replay',
                            'decision_identity',
                            'decision_replay_authorizer',
                            'decision_demo',
                            'decision_collector',
                            'decision_disclosure_reader',
                            'decision_market_writer',
                            'decision_market_operational_reader',
                            'decision_market_research_reader',
                            'decision_market_retention_admin',
                            'decision_portfolio_writer',
                            'decision_risk_writer',
                            'decision_fill_writer',
                            'decision_rag_writer',
                            'decision_rag_admin',
                            'decision_rag_query',
                            'decision_signal_writer',
                            'decision_signal_scheduler',
                            'decision_signal_admin'
                          ])
                        )
                        SELECT fingerprint
                        FROM (
                          SELECT
                            'RELATION|' || role.rolname || '|' || relation.relname || '|' ||
                              grant_row.privilege_type || '|' || grant_row.is_grantable AS fingerprint
                          FROM pg_class relation
                          JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                          CROSS JOIN LATERAL aclexplode(relation.relacl) grant_row
                          JOIN runtime_roles role ON role.oid = grant_row.grantee
                          WHERE namespace.nspname = 'public'
                          UNION ALL
                          SELECT
                            'COLUMN|' || role.rolname || '|' || relation.relname || '|' ||
                              attribute.attname || '|' || grant_row.privilege_type || '|' ||
                              grant_row.is_grantable
                          FROM pg_attribute attribute
                          JOIN pg_class relation ON relation.oid = attribute.attrelid
                          JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                          CROSS JOIN LATERAL aclexplode(attribute.attacl) grant_row
                          JOIN runtime_roles role ON role.oid = grant_row.grantee
                          WHERE namespace.nspname = 'public'
                            AND attribute.attnum > 0
                            AND NOT attribute.attisdropped
                          UNION ALL
                          SELECT
                            'ROUTINE|' || role.rolname || '|' || routine.proname || '(' ||
                              pg_get_function_identity_arguments(routine.oid) || ')|' ||
                              grant_row.privilege_type || '|' || grant_row.is_grantable
                          FROM pg_proc routine
                          JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
                          CROSS JOIN LATERAL aclexplode(routine.proacl) grant_row
                          JOIN runtime_roles role ON role.oid = grant_row.grantee
                          WHERE namespace.nspname = 'public'
                          UNION ALL
                          SELECT
                            'SCHEMA|' || role.rolname || '|' || namespace.nspname || '|' ||
                              grant_row.privilege_type || '|' || grant_row.is_grantable
                          FROM pg_namespace namespace
                          CROSS JOIN LATERAL aclexplode(namespace.nspacl) grant_row
                          JOIN runtime_roles role ON role.oid = grant_row.grantee
                          WHERE namespace.nspname = 'public'
                        ) grants
                        ORDER BY fingerprint
                        """.trimIndent(),
                    ).use { result ->
                        buildList {
                            while (result.next()) {
                                add(result.getString(1))
                            }
                        }
                    }
            }

    private fun assertNoPublicCustomObjectPrivileges(connection: Connection) {
        connection.createStatement().use { statement ->
            val publicRelationGrants =
                statement
                    .executeQuery(
                        """
                        SELECT count(*)
                        FROM pg_class relation
                        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                        CROSS JOIN LATERAL aclexplode(
                          coalesce(
                            relation.relacl,
                            acldefault(
                              CASE WHEN relation.relkind = 'S' THEN 'S'::"char" ELSE 'r'::"char" END,
                              relation.relowner
                            )
                          )
                        ) grant_row
                        WHERE namespace.nspname = 'public'
                          AND relation.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
                          AND grant_row.grantee = 0
                        """.trimIndent(),
                    ).use { result ->
                        assertTrue(result.next())
                        result.getInt(1)
                    }
            assertEquals(0, publicRelationGrants)

            val publicFunctionGrants =
                statement
                    .executeQuery(
                        """
                        SELECT count(*)
                        FROM pg_proc routine
                        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
                        CROSS JOIN LATERAL aclexplode(
                          coalesce(routine.proacl, acldefault('f', routine.proowner))
                        ) grant_row
                        WHERE namespace.nspname = 'public'
                          AND grant_row.grantee = 0
                          AND NOT EXISTS (
                            SELECT 1
                            FROM pg_depend dependency
                            WHERE dependency.classid = 'pg_proc'::regclass
                              AND dependency.objid = routine.oid
                              AND dependency.deptype = 'e'
                          )
                        """.trimIndent(),
                    ).use { result ->
                        assertTrue(result.next())
                        result.getInt(1)
                    }
            assertEquals(0, publicFunctionGrants)
        }
    }

    private fun queryScalar(
        statement: java.sql.Statement,
        sql: String,
    ): String =
        statement.executeQuery(sql).use { result ->
            assertTrue(result.next())
            result.getString(1)
        }

    private fun assertRuntimeLockTimeout() {
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, adminPassword).use { blocker ->
            blocker.autoCommit = false
            blocker.createStatement().use { statement ->
                statement.execute("lock table market_calendar in access exclusive mode")
            }
            try {
                DriverManager.getConnection(postgres.jdbcUrl, runtimeProperties()).use { runtime ->
                    runtime.createStatement().use { statement ->
                        val failure =
                            assertThrows<SQLException> {
                                statement.execute("select * from market_calendar limit 1")
                            }
                        assertEquals("55P03", failure.sqlState)
                    }
                }
            } finally {
                blocker.rollback()
            }
        }
    }

    private fun assertActorScopedPoolStateIsReset() {
        val config =
            HikariConfig().apply {
                jdbcUrl = postgres.jdbcUrl
                username = RUNTIME_USER
                password = runtimePassword
                maximumPoolSize = 1
                minimumIdle = 1
                poolName = "actor-scope-reset-test"
            }
        HikariDataSource(config).use { dataSource ->
            val beanFactory =
                StaticListableBeanFactory().apply {
                    addBean("actorScopedDataSource", dataSource)
                }
            val reader =
                ActorScopedReadQuery(
                    beanFactory.getBeanProvider(DataSource::class.java),
                )
            val scopedValues =
                reader.query(
                    actorUserId = "usr_pool_scope_probe",
                    sql =
                        """
                        select current_setting('app.actor_user_id', true) actor_user_id,
                               current_setting('statement_timeout') statement_timeout
                        """.trimIndent(),
                ) { result ->
                    result.getString("actor_user_id") to result.getString("statement_timeout")
                }
            assertEquals(listOf("usr_pool_scope_probe" to "500ms"), scopedValues)

            assertThrows<SQLException> {
                reader.query(
                    actorUserId = "usr_pool_scope_rollback",
                    sql = "select 1 / 0",
                ) { result -> result.getInt(1) }
            }

            dataSource.connection.use { connection ->
                connection.createStatement().use { statement ->
                    assertTrue(
                        queryScalar(
                            statement,
                            "select coalesce(current_setting('app.actor_user_id', true), '')",
                        ).isBlank(),
                    )
                    assertEquals("2s", queryScalar(statement, "show statement_timeout"))
                    assertEquals("500ms", queryScalar(statement, "show lock_timeout"))
                    assertEquals("5s", queryScalar(statement, "show idle_in_transaction_session_timeout"))
                }
            }
        }
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
        private val workerPassword: String = "w" + "a".repeat(24)
        private val outboxPublisherPassword: String = "o" + "p".repeat(24)
        private val poisonRecorderPassword: String = "p" + "r".repeat(24)
        private val replayPassword: String = "r" + "y".repeat(24)
        private val identityPassword: String = "i" + "d".repeat(24)
        private val authPassword: String = "a" + "u".repeat(24)
        private val replayAuthorizerPassword: String = "z" + "a".repeat(24)
        private val demoPassword: String = "d" + "m".repeat(24)
        private val migrationPassword: String = "m" + "p".repeat(24)
        private val collectorPassword: String = "c" + "p".repeat(24)
        private val disclosureReaderPassword: String = "d" + "r".repeat(24)
        private val marketWriterPassword: String = "w" + "m".repeat(24)
        private val portfolioWriterPassword: String = "w" + "p".repeat(24)
        private val riskWriterPassword: String = "w" + "r".repeat(24)
        private val fillWriterPassword: String = "w" + "f".repeat(24)
        private val ragWriterPassword: String = "w" + "g".repeat(24)
        private val ragAdminPassword: String = "a" + "g".repeat(24)
        private val ragQueryPassword: String = "q" + "g".repeat(24)
        private val signalWriterPassword: String = "w" + "s".repeat(24)
        private val signalSchedulerPassword: String = "s" + "s".repeat(24)
        private val signalAdminPassword: String = "a" + "s".repeat(24)
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            stablePostgresContainer(postgresImage)
                .withDatabaseName("trading")
                .withUsername("postgres")
                .withPassword(adminPassword)
                .withEnv("POSTGRES_APP_PASSWORD", runtimePassword)
                .withEnv("POSTGRES_WORKER_PASSWORD", workerPassword)
                .withEnv("POSTGRES_OUTBOX_PUBLISHER_PASSWORD", outboxPublisherPassword)
                .withEnv("POSTGRES_POISON_RECORDER_PASSWORD", poisonRecorderPassword)
                .withEnv("POSTGRES_REPLAY_PASSWORD", replayPassword)
                .withEnv("POSTGRES_IDENTITY_PASSWORD", identityPassword)
                .withEnv("POSTGRES_AUTH_PASSWORD", authPassword)
                .withEnv("POSTGRES_REPLAY_AUTHORIZER_PASSWORD", replayAuthorizerPassword)
                .withEnv("POSTGRES_DEMO_PASSWORD", demoPassword)
                .withEnv("POSTGRES_MIGRATION_PASSWORD", migrationPassword)
                .withEnv("POSTGRES_COLLECTOR_PASSWORD", collectorPassword)
                .withEnv("POSTGRES_DISCLOSURE_READER_PASSWORD", disclosureReaderPassword)
                .withEnv("POSTGRES_MARKET_WRITER_PASSWORD", marketWriterPassword)
                .withEnv("POSTGRES_PORTFOLIO_WRITER_PASSWORD", portfolioWriterPassword)
                .withEnv("POSTGRES_RISK_WRITER_PASSWORD", riskWriterPassword)
                .withEnv("POSTGRES_FILL_WRITER_PASSWORD", fillWriterPassword)
                .withEnv("POSTGRES_RAG_WRITER_PASSWORD", ragWriterPassword)
                .withEnv("POSTGRES_RAG_ADMIN_PASSWORD", ragAdminPassword)
                .withEnv("POSTGRES_RAG_QUERY_PASSWORD", ragQueryPassword)
                .withEnv("POSTGRES_SIGNAL_WRITER_PASSWORD", signalWriterPassword)
                .withEnv("POSTGRES_SIGNAL_SCHEDULER_PASSWORD", signalSchedulerPassword)
                .withEnv("POSTGRES_SIGNAL_ADMIN_PASSWORD", signalAdminPassword)
    }
}
