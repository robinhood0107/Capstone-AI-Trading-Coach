package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.flywaydb.core.Flyway
import org.flywaydb.core.api.FlywayException
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import java.sql.Connection
import java.sql.DriverManager
import java.sql.SQLException
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

// V16은 disposable database에서만 legacy tombstone과 최소권한 graph를 검증한다.
@Testcontainers
class RagSourceRegistryMigrationIntegrationTest {
    @Test
    fun `V59 to V60 owner dual profile upgrade is forward only and preserves existing immutable tables`() {
        withPreparedDatabase("owner_dual_profile_upgrade") { jdbcUrl ->
            flyway(jdbcUrl, target = "59").migrate()
            adminConnection(jdbcUrl).use { connection ->
                assertThat(queryString(connection, "select max(version::integer)::text from flyway_schema_history where success"))
                    .isEqualTo("59")
                assertThat(
                    queryString(
                        connection,
                        "select count(*)::text from information_schema.columns where table_schema = 'public' and table_name = 'rag_v2_immutable_bundles' and column_name = 'owner_embedding_profile_id'",
                    ),
                ).isEqualTo("0")
            }

            flyway(jdbcUrl, target = "60").migrate()

            adminConnection(jdbcUrl).use { connection ->
                assertThat(queryString(connection, "select max(version::integer)::text from flyway_schema_history where success"))
                    .isEqualTo("60")
                assertThat(
                    queryString(
                        connection,
                        "select count(*)::text from information_schema.columns where table_schema = 'public' and table_name = 'rag_v2_immutable_bundles' and column_name = 'owner_embedding_profile_id'",
                    ),
                ).isEqualTo("1")
                assertThat(
                    queryString(
                        connection,
                        "select count(*)::text from pg_proc where oid = 'public.issue_rag_v2_immutable_import_ticket_v2(text,text,text,text,text)'::regprocedure",
                    ),
                ).isEqualTo("1")
                assertThat(queryString(connection, "select count(*)::text from rag_v2_immutable_import_tickets"))
                    .isEqualTo("0")
            }
        }
    }

    @Test
    fun `V60 to V61 owner overlay reuse repair is forward only and preserves ACL`() {
        withPreparedDatabase("owner_overlay_reuse_upgrade") { jdbcUrl ->
            flyway(jdbcUrl, target = "60").migrate()
            adminConnection(jdbcUrl).use { connection ->
                assertThat(queryString(connection, "select max(version::integer)::text from flyway_schema_history where success"))
                    .isEqualTo("60")
            }

            flyway(jdbcUrl, target = "61").migrate()

            adminConnection(jdbcUrl).use { connection ->
                assertThat(queryString(connection, "select max(version::integer)::text from flyway_schema_history where success"))
                    .isEqualTo("61")
                assertThat(
                    queryString(
                        connection,
                        "select pg_get_functiondef('public.prepare_rag_v2_immutable_owner_overlay(text,text)'::regprocedure)",
                    ),
                ).contains("existing_generation.state NOT IN ('EVALUATED', 'ACTIVE', 'SUPERSEDED')")
                assertThat(
                    hasFunctionPrivilege(
                        connection,
                        "decision_rag_admin",
                        "prepare_rag_v2_immutable_owner_overlay(text,text)",
                    ),
                ).isTrue()
                assertThat(
                    hasPublicFunctionExecute(
                        connection,
                        "prepare_rag_v2_immutable_owner_overlay(text,text)",
                    ),
                ).isFalse()
            }
        }
    }

    @Test
    fun `V61 to V62 base-only owner scope repair is forward only and preserves ACL`() {
        withPreparedDatabase("base_only_owner_scope_upgrade") { jdbcUrl ->
            flyway(jdbcUrl, target = "61").migrate()
            flyway(jdbcUrl, target = "62").migrate()

            adminConnection(jdbcUrl).use { connection ->
                assertThat(queryString(connection, "select max(version::integer)::text from flyway_schema_history where success"))
                    .isEqualTo("62")
                assertThat(
                    queryString(
                        connection,
                        "select pg_get_functiondef('public.canonicalize_rag_v2_immutable_retrieval_citations(text,text,text,jsonb)'::regprocedure)",
                    ),
                ).contains("rag_v2_immutable_empty_owner_scope_is_current")
                assertThat(
                    hasPublicFunctionExecute(
                        connection,
                        "rag_v2_immutable_empty_owner_scope_is_current(text,bigint,text,text,text)",
                    ),
                ).isFalse()
            }
        }
    }

    @Test
    fun `V62 through V90 forward repairs preserve empty owner scope and ACL`() {
        withPreparedDatabase("empty_owner_generation_scope_upgrade") { jdbcUrl ->
            flyway(jdbcUrl, target = "62").migrate()
            flyway(jdbcUrl).migrate()

            adminConnection(jdbcUrl).use { connection ->
                assertThat(queryString(connection, "select max(version::integer)::text from flyway_schema_history where success"))
                    .isEqualTo("115")
                assertThat(
                    queryString(
                        connection,
                        "select pg_get_functiondef('public.rag_v2_immutable_empty_owner_scope_is_current(text,bigint,text,text,text)'::regprocedure)",
                    ),
                ).contains("generation.expected_source_count = 0")
                assertThat(
                    hasPublicFunctionExecute(
                        connection,
                        "rag_v2_immutable_empty_owner_scope_is_current(text,bigint,text,text,text)",
                    ),
                ).isFalse()
                assertThat(queryString(connection, "select count(*)::text from public.s4_9_mcp_oauth_clients"))
                    .isEqualTo("0")
                assertThat(queryString(connection, "select count(*)::text from public.s4_9_strong_llm_usage_ledger"))
                    .isEqualTo("0")
                assertThat(
                    hasPublicFunctionExecute(
                        connection,
                        "record_s4_9_strong_llm_usage(text,text,text,text,text,text,text,integer,integer,integer,integer,integer,text)",
                    ),
                ).isFalse()
            }
        }
    }

    @Test
    fun `V65 to V86 preserves S4 9 boundaries and existing rows`() {
        withPreparedDatabase("s49_forward_upgrade") { jdbcUrl ->
            flyway(jdbcUrl, target = "65").migrate()
            adminConnection(jdbcUrl).use { connection ->
                connection.createStatement().use { statement ->
                    statement.executeUpdate(
                        "insert into users(user_id, username, password_hash, role, status, security_version) " +
                            "values ('usr_s49_preserved', 's49_preserved', '\$2b\$12\$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'USER', 'ACTIVE', 1)",
                    )
                }
            }

            flyway(jdbcUrl).migrate()

            adminConnection(jdbcUrl).use { connection ->
                assertThat(queryString(connection, "select max(version::integer)::text from flyway_schema_history where success"))
                    .isEqualTo("115")
                assertThat(queryString(connection, "select count(*)::text from users where user_id = 'usr_s49_preserved'"))
                    .isEqualTo("1")
                assertThat(queryString(connection, "select count(*)::text from public.s4_9_saved_answer_history"))
                    .isEqualTo("0")
            }
        }
    }

    @Test
    fun `V69 to V70 preserves Strong LLM usage and adds v2 defaults`() {
        withPreparedDatabase("s49_v69_v70_upgrade") { jdbcUrl ->
            flyway(jdbcUrl, target = "69").migrate()
            adminConnection(jdbcUrl).use { connection ->
                connection.createStatement().use { statement ->
                    statement.executeUpdate(
                        """
                        insert into public.s4_9_strong_llm_usage_ledger(
                          usage_event_id,request_id,provider,model_id,answer_basis,outcome,
                          tool_round_count,search_call_count,read_call_count,prompt_token_count,
                          output_token_count,evidence_set_sha256
                        ) values (
                          's49_llu_${"a".repeat(32)}','req_s49_upgrade_0001','VERTEX','gemini-3.5-flash',
                          'MODEL_KNOWLEDGE','COMMITTED',0,0,0,11,7,repeat('b',64)
                        )
                        """.trimIndent(),
                    )
                }
            }

            flyway(jdbcUrl).migrate()

            adminConnection(jdbcUrl).use { connection ->
                assertThat(
                    queryString(
                        connection,
                        """
                        select concat_ws(':', usage_schema_version, vertex_generate_call_count,
                          google_grounding_query_count, search_backend, evidence_validation_mode)
                        from public.s4_9_strong_llm_usage_ledger
                        where request_id = 'req_s49_upgrade_0001'
                        """.trimIndent(),
                    ),
                ).isEqualTo("1:1:0:NONE:CANONICAL_EXACT")
            }
        }
    }

    @Test
    fun `V70 Google budget settles actual over reserve and conservatively retains unknown usage`() {
        withPreparedDatabase("s49_google_budget") { jdbcUrl ->
            flyway(jdbcUrl).migrate()
            adminConnection(jdbcUrl).use { connection ->
                connection.createStatement().use { statement ->
                    statement.executeUpdate(
                        "insert into users(user_id,username,password_hash,role,status,security_version) " +
                            "values ('usr_s49_budget','s49_budget','\$2b\$12\$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','USER','ACTIVE',1)",
                    )
                }
            }
            appConnection(jdbcUrl).use { connection ->
                connection.autoCommit = false

                fun reserve(
                    id: String,
                    request: String,
                    fingerprint: String,
                    count: Int,
                    cap: Int,
                ): Boolean {
                    TestActorRlsScope.open(
                        jdbcUrl,
                        connection,
                        "usr_s49_budget",
                        "RESERVE_GROUNDING_BUDGET",
                        "RAG_REQUEST",
                        request,
                    )
                    val accepted =
                        connection
                            .prepareStatement(
                                "select public.reserve_s4_9_google_grounding_budget(?,?,?,?,date '2026-08-01',?,?)",
                            ).use { statement ->
                                statement.setString(1, id)
                                statement.setString(2, "usr_s49_budget")
                                statement.setString(3, request)
                                statement.setString(4, fingerprint)
                                statement.setInt(5, count)
                                statement.setInt(6, cap)
                                statement.executeQuery().use { result ->
                                    assertTrue(result.next())
                                    result.getBoolean(1)
                                }
                            }
                    connection.commit()
                    return accepted
                }

                fun settle(
                    id: String,
                    state: String,
                    actual: Int?,
                ) {
                    TestActorRlsScope.open(
                        jdbcUrl,
                        connection,
                        "usr_s49_budget",
                        "SETTLE_GROUNDING_BUDGET",
                        "BUDGET_RESERVATION",
                        id,
                    )
                    connection.prepareStatement("select public.settle_s4_9_google_grounding_budget(?,?,?,?)").use { statement ->
                        statement.setString(1, "usr_s49_budget")
                        statement.setString(2, id)
                        statement.setString(3, state)
                        if (actual == null) statement.setNull(4, java.sql.Types.INTEGER) else statement.setInt(4, actual)
                        statement.executeQuery().close()
                    }
                    connection.commit()
                }

                val fingerprint = "7".repeat(64)
                val first = "s49_gbr_${"1".repeat(32)}"
                val second = "s49_gbr_${"2".repeat(32)}"
                val third = "s49_gbr_${"3".repeat(32)}"
                assertTrue(reserve(first, "req_s49_budget_0001", fingerprint, 8, 16))
                assertTrue(reserve(second, "req_s49_budget_0002", fingerprint, 8, 16))
                assertFalse(reserve(third, "req_s49_budget_0003", fingerprint, 1, 16))
                settle(first, "COMMITTED", 12)
                settle(second, "RELEASED", null)
                assertTrue(reserve(third, "req_s49_budget_0003", fingerprint, 4, 16))
                settle(third, "RELEASED", null)

                val unknownFingerprint = "8".repeat(64)
                val unknown = "s49_gbr_${"4".repeat(32)}"
                assertTrue(reserve(unknown, "req_s49_budget_unknown", unknownFingerprint, 8, 8))
                settle(unknown, "UNKNOWN_BILLING", null)
                assertFalse(
                    reserve(
                        "s49_gbr_${"5".repeat(32)}",
                        "req_s49_budget_blocked",
                        unknownFingerprint,
                        1,
                        8,
                    ),
                )
            }
        }
    }

    @Test
    fun `V70 persists bounded grounding provenance and usage without granting direct table reads`() {
        withPreparedDatabase("s49_grounding_provenance") { jdbcUrl ->
            flyway(jdbcUrl).migrate()
            adminConnection(jdbcUrl).use { connection ->
                connection.createStatement().use { statement ->
                    statement.executeUpdate(
                        "insert into users(user_id,username,password_hash,role,status,security_version) " +
                            "values ('usr_s49_ground','s49_ground','\$2b\$12\$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','USER','ACTIVE',1)",
                    )
                }
            }
            appConnection(jdbcUrl).use { connection ->
                connection.autoCommit = false
                val sourcesJson =
                    """[{"sourceNodeId":"s49_src_${"1".repeat(
                        32,
                    )}","resultId":"google_1","citationId":"cit_2","title":"Investor.gov","canonicalUrl":"https://www.investor.gov/diversification","domain":"investor.gov","chunkIndex":0}]"""
                val supportsJson =
                    """[{"supportId":"s49_sup_${"2".repeat(
                        32,
                    )}","segmentSha256":"${"3".repeat(64)}","startIndex":0,"endIndex":24,"chunkIndices":[0]}]"""
                TestActorRlsScope.open(
                    jdbcUrl,
                    connection,
                    "usr_s49_ground",
                    "RECORD_GROUNDING_PROVENANCE",
                    "RAG_REQUEST",
                    "req_s49_grounding_0001",
                    payloadValues =
                        listOf(
                            "usr_s49_ground",
                            "req_s49_grounding_0001",
                            sourcesJson,
                            supportsJson,
                        ),
                )
                connection.prepareStatement("select public.record_s4_9_grounding_provenance(?,?,?,?)").use { statement ->
                    statement.setString(1, "usr_s49_ground")
                    statement.setString(2, "req_s49_grounding_0001")
                    statement.setString(3, sourcesJson)
                    statement.setString(4, supportsJson)
                    statement.execute()
                }
                connection.commit()
                TestActorRlsScope.open(
                    jdbcUrl,
                    connection,
                    "usr_s49_ground",
                    "RECORD_SEARCH_ATTEMPT",
                    "RAG_REQUEST",
                    "req_s49_grounding_0001",
                    payloadValues =
                        listOf("usr_s49_ground", "req_s49_grounding_0001", "1", "VERTEX_GOOGLE", "1", "COMMITTED"),
                )
                connection.createStatement().use { statement ->
                    statement.execute(
                        "select public.record_s4_9_search_attempt(" +
                            "'s49_sra_${"4".repeat(32)}','usr_s49_ground','req_s49_grounding_0001'," +
                            "1,'VERTEX_GOOGLE','COMMITTED',1)",
                    )
                }
                connection.commit()
                TestActorRlsScope.open(
                    jdbcUrl,
                    connection,
                    "usr_s49_ground",
                    "RECORD_STRONG_LLM_USAGE",
                    "RAG_REQUEST",
                    "req_s49_grounding_0001",
                    payloadValues =
                        listOf(
                            "usr_s49_ground",
                            "req_s49_grounding_0001",
                            "gemini-3.5-flash",
                            "EVIDENCE",
                            "COMMITTED",
                            "6".repeat(64),
                            null,
                        ),
                )
                connection.createStatement().use { statement ->
                    statement.execute(
                        """
                        select public.record_s4_9_strong_llm_usage_v2(
                          's49_llu_${"5".repeat(32)}','usr_s49_ground','req_s49_grounding_0001',
                          'gemini-3.5-flash','EVIDENCE','COMMITTED',0,1,0,120,48,
                          '${"6".repeat(64)}',1,1,'VERTEX_GOOGLE','GOOGLE_GROUNDING',null
                        )
                        """.trimIndent(),
                    )
                }
                connection.commit()
            }

            appConnection(jdbcUrl).use { connection ->
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { it.executeQuery("select * from public.s4_9_grounding_source_nodes") }
                }
            }
            adminConnection(jdbcUrl).use { connection ->
                assertThat(
                    queryString(
                        connection,
                        """
                        select concat_ws(':',
                          (select count(*) from public.s4_9_grounding_source_nodes),
                          (select count(*) from public.s4_9_grounding_support_edges),
                          (select count(*) from public.s4_9_search_attempts where not raw_query_stored),
                          (select count(*) from public.s4_9_strong_llm_usage_ledger where usage_schema_version = 2)
                        )
                        """.trimIndent(),
                    ),
                ).isEqualTo("1:1:1:1")
            }
        }
    }

    @Test
    fun `V71 canonicalizes host registered SearXNG read provenance`() {
        withPreparedDatabase("s49_searxng_history_provenance") { jdbcUrl ->
            flyway(jdbcUrl).migrate()
            adminConnection(jdbcUrl).use { connection ->
                connection.createStatement().use { statement ->
                    statement.executeUpdate(
                        "insert into users(user_id,username,password_hash,role,status,security_version) " +
                            "values ('usr_s49_searxng','s49_searxng','\$2b\$12\$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','USER','ACTIVE',1)",
                    )
                }
            }
            appConnection(jdbcUrl).use { connection ->
                connection.autoCommit = false
                TestActorRlsScope.open(
                    jdbcUrl,
                    connection,
                    "usr_s49_searxng",
                    "RECORD_READ_PROVENANCE",
                    "RAG_REQUEST",
                    "req_s49_searxng_0001",
                    payloadValues =
                        listOf(
                            "usr_s49_searxng",
                            "req_s49_searxng_0001",
                            "s49_src_${"1".repeat(32)}",
                            "searxng_${"2".repeat(24)}",
                            "cit_1",
                            "3".repeat(64),
                        ),
                )
                connection.createStatement().use { statement ->
                    statement.execute(
                        "select public.record_s4_9_read_provenance(" +
                            "'usr_s49_searxng','req_s49_searxng_0001','s49_src_${"1".repeat(32)}'," +
                            "'searxng_${"2".repeat(24)}','cit_1','SEARXNG_RESULT','Investor.gov'," +
                            "'https://www.investor.gov/diversification','www.investor.gov','${"3".repeat(64)}')",
                    )
                }
                val canonical =
                    queryString(
                        connection,
                        """
                        select public.canonicalize_s4_9_strong_llm_citations_v2(
                          'usr_s49_searxng','req_s49_searxng_0001','req_s49_searxng_0001','scope_unused',
                          '[{"ordinal":1,"citationId":"cit_1","sourceId":"src_web_investor_gov","sourceRevisionId":"srv_web_${"4".repeat(
                            24,
                        )}","chunkRevisionId":"rag_v2_chk_${"5".repeat(
                            32,
                        )}","generationId":"rgr_${"6".repeat(
                            32,
                        )}","citationKind":"PUBLIC_WEB","provenanceResultId":"searxng_${"2".repeat(
                            24,
                        )}","title":"Investor.gov","canonicalUrl":"https://www.investor.gov/diversification","locator":{"section":"www.investor.gov"}}]'::jsonb
                        )::text
                        """.trimIndent(),
                    )
                assertThat(canonical).contains("Investor.gov", "src_web_investor_gov", "cit_1")
                connection.rollback()
            }
        }
    }

    @Test
    fun `V66 OAuth hashes rotate revoke and remain hidden from application tables`() {
        withPreparedDatabase("s49_oauth_hashes") { jdbcUrl ->
            flyway(jdbcUrl).migrate()
            adminConnection(jdbcUrl).use { connection ->
                connection.createStatement().use { statement ->
                    statement.executeUpdate(
                        "insert into users(user_id, username, password_hash, role, status, security_version) " +
                            "values ('usr_s49_oauth', 's49_oauth', '\$2b\$12\$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'USER', 'ACTIVE', 1)",
                    )
                }
            }
            appConnection(jdbcUrl).use { connection ->
                connection.autoCommit = false
                connection.createStatement().use { statement ->
                    statement
                        .executeQuery(
                            "with sync as materialized (select public.sync_s4_9_mcp_oauth_client(" +
                                "'mcp_s49_test','S4.9 test',repeat('1',64)," +
                                "array['http://127.0.0.1/callback'],array['mcp:rag.public'],'STATIC_ALLOWLIST') as ignored) " +
                                "select count(*) = 1 from sync",
                        ).use { result ->
                            assertTrue(result.next())
                            assertTrue(result.getBoolean(1))
                        }
                    connection.commit()
                    TestActorRlsScope.open(
                        jdbcUrl,
                        connection,
                        "usr_s49_oauth",
                        "ISSUE_MCP_OAUTH_CODE",
                        "OAUTH_CODE",
                        "a".repeat(64),
                        payloadValues =
                            listOf(
                                "usr_s49_oauth",
                                "mcp_s49_test",
                                "1",
                                "http://127.0.0.1/callback",
                                "http://127.0.0.1:8080/mcp",
                                "mcp:rag.public",
                                "A".repeat(43),
                            ),
                    )
                    statement.execute(
                        "select public.upsert_s4_9_mcp_oauth_code_hash(" +
                            "repeat('a',64),'mcp_s49_test','usr_s49_oauth',1,'http://127.0.0.1/callback'," +
                            "'http://127.0.0.1:8080/mcp',array['mcp:rag.public'],repeat('A',43)," +
                            "transaction_timestamp() + interval '3 minutes')",
                    )
                    connection.commit()
                    statement.execute("select public.consume_s4_9_mcp_oauth_code_hash(repeat('a',64))")
                    connection.commit()
                    statement.execute(
                        "select public.rotate_s4_9_mcp_refresh_token_hash(" +
                            "repeat('b',64),repeat('a',64),'http://127.0.0.1:8080/mcp'," +
                            "array['mcp:rag.public'],transaction_timestamp() + interval '6 days')",
                    )
                    connection.commit()
                    statement.execute("select * from public.consume_s4_9_mcp_refresh_token(repeat('b',64))")
                    connection.commit()
                    statement.execute(
                        "select public.rotate_s4_9_mcp_refresh_token_hash(" +
                            "repeat('c',64),repeat('b',64),'http://127.0.0.1:8080/mcp'," +
                            "array['mcp:rag.public'],transaction_timestamp() + interval '6 days')",
                    )
                    connection.commit()
                    statement.execute("select public.revoke_s4_9_mcp_refresh_token_family(repeat('c',64))")
                    connection.commit()
                }
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { it.executeQuery("select * from public.s4_9_mcp_oauth_refresh_tokens") }
                }
            }
            adminConnection(jdbcUrl).use { connection ->
                assertThat(
                    queryString(
                        connection,
                        "select (consumed_at is not null)::text from public.s4_9_mcp_oauth_authorization_codes " +
                            "where code_sha256 = repeat('a',64)",
                    ),
                ).isEqualTo("true")
                assertThat(
                    queryString(
                        connection,
                        "select count(*)::text from public.s4_9_mcp_oauth_refresh_tokens where rotated_at is not null",
                    ),
                ).isEqualTo("1")
                assertThat(
                    queryString(
                        connection,
                        "select count(*)::text from public.s4_9_mcp_oauth_refresh_tokens where revoked_at is not null",
                    ),
                ).isEqualTo("2")
            }
        }
    }

    @Test
    fun `V87 refresh token claim is one shot and bound to current security version`() {
        withPreparedDatabase("s49_refresh_claim") { jdbcUrl ->
            flyway(jdbcUrl).migrate()
            adminConnection(jdbcUrl).use { connection ->
                connection.createStatement().use { statement ->
                    val passwordHash = "\$2b\$12\$" + "a".repeat(53)
                    statement.executeUpdate(
                        "insert into users(user_id, username, password_hash, role, status, security_version) " +
                            "values ('usr_s49_refresh', 's49_refresh', '$passwordHash', " +
                            "'USER', 'ACTIVE', 1)",
                    )
                }
            }
            appConnection(jdbcUrl).use { connection ->
                connection.autoCommit = false
                connection.createStatement().use { statement ->
                    statement.execute(
                        "select public.sync_s4_9_mcp_oauth_client(" +
                            "'mcp_s49_refresh','S4.9 refresh',repeat('1',64)," +
                            "array['http://127.0.0.1/callback'],array['mcp:rag.public'],'STATIC_ALLOWLIST')",
                    )
                    connection.commit()
                    TestActorRlsScope.open(
                        jdbcUrl,
                        connection,
                        "usr_s49_refresh",
                        "ISSUE_MCP_OAUTH_CODE",
                        "OAUTH_CODE",
                        "c".repeat(64),
                        payloadValues =
                            listOf(
                                "usr_s49_refresh",
                                "mcp_s49_refresh",
                                "1",
                                "http://127.0.0.1/callback",
                                "http://127.0.0.1:8080/mcp",
                                "mcp:rag.public",
                                "A".repeat(43),
                            ),
                    )
                    statement.execute(
                        "select public.upsert_s4_9_mcp_oauth_code_hash(" +
                            "repeat('c',64),'mcp_s49_refresh','usr_s49_refresh',1,'http://127.0.0.1/callback'," +
                            "'http://127.0.0.1:8080/mcp',array['mcp:rag.public'],repeat('A',43)," +
                            "transaction_timestamp() + interval '3 minutes')",
                    )
                    connection.commit()
                    statement.execute("select public.consume_s4_9_mcp_oauth_code_hash(repeat('c',64))")
                    connection.commit()
                    statement.execute(
                        "select public.rotate_s4_9_mcp_refresh_token_hash(" +
                            "repeat('d',64),repeat('c',64),'http://127.0.0.1:8080/mcp',array['mcp:rag.public']," +
                            "transaction_timestamp() + interval '6 days')",
                    )
                    connection.commit()
                    statement
                        .executeQuery(
                            "select owner_user_id,security_version from " +
                                "public.consume_s4_9_mcp_refresh_token(repeat('d',64))",
                        ).use { result ->
                            assertTrue(result.next())
                            assertThat(result.getString("owner_user_id")).isEqualTo("usr_s49_refresh")
                            assertThat(result.getLong("security_version")).isEqualTo(1)
                            assertThat(result.next()).isFalse()
                        }
                    connection.commit()
                    statement
                        .executeQuery(
                            "select count(*) from public.consume_s4_9_mcp_refresh_token(repeat('d',64))",
                        ).use { result ->
                            assertTrue(result.next())
                            assertThat(result.getInt(1)).isZero()
                        }
                    connection.commit()
                }
            }
            adminConnection(jdbcUrl).use { connection ->
                connection.createStatement().use {
                    it.executeUpdate(
                        "update users set security_version=2 where user_id='usr_s49_refresh'",
                    )
                }
            }
            appConnection(jdbcUrl).use { connection ->
                connection.createStatement().use { statement ->
                    assertThrows(SQLException::class.java) {
                        statement.execute(
                            "select public.rotate_s4_9_mcp_refresh_token_hash(" +
                                "repeat('e',64),repeat('d',64),'http://127.0.0.1:8080/mcp',array['mcp:rag.public']," +
                                "transaction_timestamp() + interval '6 days')",
                        )
                    }
                }
            }
        }
    }

    @Test
    fun `V66 validation receipt saves encrypted history once and content free ledgers pass RLS`() {
        withPreparedDatabase("s49_receipt_save") { jdbcUrl ->
            flyway(jdbcUrl).migrate()
            adminConnection(jdbcUrl).use { connection ->
                connection.createStatement().use { statement ->
                    statement.executeUpdate(
                        "insert into users(user_id, username, password_hash, role, status, security_version) " +
                            "values ('usr_s49_save', 's49_save', '\$2b\$12\$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'USER', 'ACTIVE', 1)",
                    )
                }
            }
            appConnection(jdbcUrl).use { connection ->
                connection.autoCommit = false
                val contextId = "s49_ctx_${"1".repeat(32)}"
                val sourceSetHash = "2".repeat(64)
                val draftHash = "3".repeat(64)
                val evidenceHash = "5".repeat(64)
                val usageEvidenceHash = "7".repeat(64)
                connection.createStatement().use { statement ->
                    statement.execute(
                        "select public.sync_s4_9_mcp_oauth_client(" +
                            "'mcp_s49_save','S4.9 save',repeat('1',64)," +
                            "array['http://127.0.0.1/callback'],array['mcp:answer.validate','mcp:history.write'],'STATIC_ALLOWLIST')",
                    )
                }
                connection.commit()

                TestActorRlsScope.open(
                    jdbcUrl,
                    connection,
                    "usr_s49_save",
                    "ISSUE_ANSWER_VALIDATION",
                    "RESEARCH_CONTEXT",
                    contextId,
                    payloadValues =
                        listOf("usr_s49_save", "mcp_s49_save", contextId, sourceSetHash, draftHash, "VALID"),
                )
                connection.createStatement().use { statement ->
                    statement.execute(
                        "select public.issue_s4_9_answer_validation_receipt(" +
                            "repeat('d',64),'usr_s49_save','mcp_s49_save','s49_ctx_' || repeat('1',32)," +
                            "repeat('2',64),repeat('3',64),'VALID',transaction_timestamp() + interval '4 minutes')",
                    )
                }
                connection.commit()

                TestActorRlsScope.open(
                    jdbcUrl,
                    connection,
                    "usr_s49_save",
                    "CONSUME_ANSWER_VALIDATION",
                    "RAG_ANSWER",
                    "rag_s49_answer_0001",
                    payloadValues = listOf("usr_s49_save", "mcp_s49_save", "rag_s49_answer_0001", draftHash),
                )
                connection.createStatement().use { statement ->
                    statement.execute(
                        "select public.consume_s4_9_validation_and_save_history(" +
                            "repeat('d',64),'usr_s49_save','mcp_s49_save','rag_s49_answer_0001',repeat('3',64),'kek-v1'," +
                            "decode(repeat('01',12),'hex'),decode(repeat('02',32),'hex'),decode(repeat('03',16),'hex')," +
                            "decode(repeat('04',12),'hex'),decode('05','hex'),decode(repeat('06',16),'hex')," +
                            "decode(repeat('07',12),'hex'),decode('08','hex'),decode(repeat('09',16),'hex'),transaction_timestamp())",
                    )
                }
                connection.commit()

                TestActorRlsScope.open(
                    jdbcUrl,
                    connection,
                    "usr_s49_save",
                    "RECORD_WEB_EVIDENCE",
                    "RESEARCH_CONTEXT",
                    contextId,
                    payloadValues =
                        listOf(
                            "usr_s49_save",
                            "mcp_s49_save",
                            contextId,
                            "https://example.com/evidence",
                            "Evidence",
                            evidenceHash,
                        ),
                )
                connection.createStatement().use { statement ->
                    statement.execute(
                        "select public.record_s4_9_web_evidence_metadata(" +
                            "'s49_web_' || repeat('4',32),'usr_s49_save','mcp_s49_save','s49_ctx_' || repeat('1',32)," +
                            "'https://example.com/evidence','Evidence',null,transaction_timestamp(),repeat('5',64)," +
                            "transaction_timestamp() + interval '1 hour')",
                    )
                }
                connection.commit()

                TestActorRlsScope.open(
                    jdbcUrl,
                    connection,
                    "usr_s49_save",
                    "RECORD_STRONG_LLM_USAGE",
                    "RAG_REQUEST",
                    "req_s49_usage_0001",
                    payloadValues =
                        listOf(
                            "usr_s49_save",
                            "req_s49_usage_0001",
                            "gemini-3.5-flash",
                            "MODEL_KNOWLEDGE",
                            "COMMITTED",
                            usageEvidenceHash,
                        ),
                )
                connection.createStatement().use { statement ->
                    statement.execute(
                        "select public.record_s4_9_strong_llm_usage(" +
                            "'s49_llu_' || repeat('6',32),'usr_s49_save','req_s49_usage_0001','VERTEX_AI','gemini-3.5-flash'," +
                            "'MODEL_KNOWLEDGE','COMMITTED',0,0,0,10,5,repeat('7',64))",
                    )
                }
                connection.commit()

                TestActorRlsScope.open(
                    jdbcUrl,
                    connection,
                    "usr_s49_save",
                    "CONSUME_ANSWER_VALIDATION",
                    "RAG_ANSWER",
                    "rag_s49_answer_0002",
                    payloadValues = listOf("usr_s49_save", "mcp_s49_save", "rag_s49_answer_0002", draftHash),
                )
                connection.createStatement().use { statement ->
                    assertThrows(SQLException::class.java) {
                        statement.execute(
                            "select public.consume_s4_9_validation_and_save_history(" +
                                "repeat('d',64),'usr_s49_save','mcp_s49_save','rag_s49_answer_0002',repeat('3',64),'kek-v1'," +
                                "decode(repeat('01',12),'hex'),decode(repeat('02',32),'hex'),decode(repeat('03',16),'hex')," +
                                "decode(repeat('04',12),'hex'),decode('05','hex'),decode(repeat('06',16),'hex')," +
                                "decode(repeat('07',12),'hex'),decode('08','hex'),decode(repeat('09',16),'hex'),transaction_timestamp())",
                        )
                    }
                }
                connection.rollback()
            }
            adminConnection(jdbcUrl).use { connection ->
                assertThat(queryString(connection, "select count(*)::text from public.s4_9_saved_answer_history"))
                    .isEqualTo("1")
                assertThat(queryString(connection, "select count(*)::text from public.s4_9_web_evidence_metadata"))
                    .isEqualTo("1")
                assertThat(queryString(connection, "select count(*)::text from public.s4_9_strong_llm_usage_ledger"))
                    .isEqualTo("1")
            }
        }
    }

    @Test
    fun `clean migration creates normalized graph tombstones and exact ACL`() {
        withPreparedDatabase("clean") { jdbcUrl ->
            flyway(jdbcUrl).migrate()

            adminConnection(jdbcUrl).use { connection ->
                val expectedTables =
                    setOf(
                        "rag_sources_v2_legacy",
                        "rag_chunks_v2_legacy",
                        "rag_answers_v2_legacy",
                        "rag_citations_v2_legacy",
                        "rag_answer_feedback_v2_legacy",
                        "rag_sources",
                        "rag_source_revisions",
                        "rag_source_checks",
                        "rag_ingest_runs",
                        "rag_chunk_revisions",
                        "rag_corpus_generations",
                        "rag_generation_chunks",
                        "rag_chunk_embeddings",
                        "rag_embedding_staging",
                        "rag_generation_attestations",
                        "rag_source_card_verifications",
                        "rag_source_public_topics",
                        "rag_source_exact_identifiers",
                        "rag_retrieval_scope_claims",
                        "rag_embedding_policy_state",
                        "rag_embedding_policy_transitions",
                        "rag_consent_events",
                        "rag_answer_claims",
                        "rag_answer_claim_transitions",
                        "rag_answer_history",
                        "rag_answer_citations",
                        "rag_answer_feedback",
                        "rag_provider_usage_ledger",
                        "rag_v2_public_corpus_state",
                        "rag_v2_owner_private_generation_pointers",
                        "rag_v2_owner_documents",
                        "rag_v2_owner_document_chunks",
                        "rag_v2_owner_document_embeddings",
                        "rag_v2_document_deletion_receipts",
                        "rag_v2_answer_history",
                        "rag_v2_answer_citations",
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
                        "rag_v2_immutable_import_tickets",
                        "rag_v2_immutable_activation_receipts",
                        "rag_v2_immutable_deletion_receipts",
                        "rag_v2_immutable_owner_document_deletion_tombstones",
                    )
                assertThat(queryStrings(connection, normalizedTableQuery))
                    .containsAll(expectedTables)
                assertThat(queryString(connection, "select max(version::integer) from flyway_schema_history where success"))
                    .isEqualTo("115")

                expectedTables.forEach { table ->
                    assertThat(
                        queryString(
                            connection,
                            "select tableowner from pg_tables where schemaname = 'public' and tablename = '$table'",
                        ),
                    ).isEqualTo("flyway")
                }
                assertThat(roleSettings(connection, "decision_rag_writer"))
                    .contains(
                        "statement_timeout=2s",
                        "lock_timeout=500ms",
                        "idle_in_transaction_session_timeout=5s",
                    )
                assertThat(roleSettings(connection, "decision_rag_admin"))
                    .contains(
                        "statement_timeout=5s",
                        "lock_timeout=500ms",
                        "idle_in_transaction_session_timeout=5s",
                    )
                assertThat(roleSettings(connection, "decision_rag_query"))
                    .contains(
                        "statement_timeout=1500ms",
                        "lock_timeout=250ms",
                        "idle_in_transaction_session_timeout=5s",
                    )

                val writerTables =
                    listOf(
                        "rag_sources",
                        "rag_source_revisions",
                        "rag_source_checks",
                        "rag_ingest_runs",
                        "rag_chunk_revisions",
                        "rag_corpus_generations",
                        "rag_generation_chunks",
                    )
                writerTables.forEach { table ->
                    assertTrue(hasTablePrivilege(connection, "decision_rag_writer", table, "SELECT"))
                    assertTrue(hasTablePrivilege(connection, "decision_rag_writer", table, "INSERT"))
                    assertFalse(hasTablePrivilege(connection, "decision_rag_writer", table, "DELETE"))
                    assertFalse(hasTablePrivilege(connection, "decision_rag_writer", table, "TRUNCATE"))
                }
                assertTrue(
                    hasTablePrivilege(
                        connection,
                        "decision_rag_writer",
                        "rag_embedding_staging",
                        "SELECT",
                    ),
                )
                assertTrue(
                    hasTablePrivilege(
                        connection,
                        "decision_rag_writer",
                        "rag_embedding_staging",
                        "INSERT",
                    ),
                )
                listOf("UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                    assertFalse(
                        hasTablePrivilege(
                            connection,
                            "decision_rag_writer",
                            "rag_embedding_staging",
                            privilege,
                        ),
                    )
                }
                listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                    assertFalse(
                        hasTablePrivilege(
                            connection,
                            "decision_rag_writer",
                            "rag_chunk_embeddings",
                            privilege,
                        ),
                        "unexpected direct final embedding $privilege privilege",
                    )
                }
                assertFalse(hasTablePrivilege(connection, "decision_rag_writer", "rag_sources", "UPDATE"))
                assertTrue(
                    hasColumnPrivilege(
                        connection,
                        "decision_rag_writer",
                        "rag_ingest_runs",
                        "status",
                        "UPDATE",
                    ),
                )
                assertFalse(
                    hasColumnPrivilege(
                        connection,
                        "decision_rag_writer",
                        "rag_ingest_runs",
                        "ingest_run_id",
                        "UPDATE",
                    ),
                )
                listOf(
                    "status",
                    "actual_chunk_count",
                    "evaluation_status",
                    "evaluated_at",
                    "failed_at",
                    "disabled_at",
                    "failure_class",
                ).forEach { column ->
                    assertTrue(
                        hasColumnPrivilege(
                            connection,
                            "decision_rag_writer",
                            "rag_corpus_generations",
                            column,
                            "UPDATE",
                        ),
                        "missing RAG generation writer UPDATE on $column",
                    )
                }
                listOf("corpus_generation_id", "corpus_hash", "embedding_profile_id").forEach { column ->
                    assertFalse(
                        hasColumnPrivilege(
                            connection,
                            "decision_rag_writer",
                            "rag_corpus_generations",
                            column,
                            "UPDATE",
                        ),
                        "unexpected RAG generation writer UPDATE on $column",
                    )
                }
                assertFalse(
                    hasColumnPrivilege(
                        connection,
                        "decision_rag_writer",
                        "rag_corpus_generations",
                        "activated_at",
                        "UPDATE",
                    ),
                    "writer must not gain generation activation authority",
                )
                listOf("users", "principles", "orders", "flyway_schema_history").forEach { unrelated ->
                    listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                        assertFalse(
                            hasTablePrivilege(connection, "decision_rag_writer", unrelated, privilege),
                            "unexpected RAG writer $privilege on $unrelated",
                        )
                    }
                }
                expectedTables.forEach { table ->
                    listOf("decision_app", "decision_rag_query").forEach { role ->
                        listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                            assertFalse(
                                hasTablePrivilege(connection, role, table, privilege),
                                "unexpected $role $privilege on $table",
                            )
                        }
                    }
                    assertFalse(hasPublicTablePrivilege(connection, table))
                }

                assertTrue(
                    hasFunctionPrivilege(
                        connection,
                        "decision_app",
                        "public.read_rag_source_registry(text)",
                    ),
                )
                assertTrue(
                    hasFunctionPrivilege(
                        connection,
                        "decision_rag_query",
                        "public.read_active_rag_chunks(text,integer)",
                    ),
                )
                val customFunctions =
                    listOf(
                        "public.guard_rag_source_revision_locator()",
                        "public.guard_rag_ingest_run_transition()",
                        "public.guard_rag_chunk_scope()",
                        "public.guard_rag_generation_materialization()",
                        "public.guard_rag_generation_transition()",
                        "public.guard_rag_generation_activation()",
                        "public.read_rag_source_registry(text)",
                        "public.read_active_rag_chunks(text,integer)",
                    )
                customFunctions.forEach { signature ->
                    assertFalse(hasPublicFunctionExecute(connection, signature))
                    assertFalse(
                        hasFunctionPrivilege(connection, "decision_rag_writer", signature),
                        "unexpected RAG writer EXECUTE on $signature",
                    )
                    assertThat(
                        queryString(
                            connection,
                            "select pg_get_userbyid(proowner) from pg_proc where oid = '$signature'::regprocedure",
                        ),
                    ).isEqualTo("flyway")
                }
                val retirementFunction = "public.retire_rag_source_for_relocation(text,text)"
                assertFalse(hasPublicFunctionExecute(connection, retirementFunction))
                assertTrue(
                    hasFunctionPrivilege(
                        connection,
                        "decision_rag_writer",
                        retirementFunction,
                    ),
                )
                listOf("decision_app", "decision_rag_query").forEach { role ->
                    assertFalse(
                        hasFunctionPrivilege(connection, role, retirementFunction),
                        "unexpected $role EXECUTE on $retirementFunction",
                    )
                }
                assertThat(
                    queryString(
                        connection,
                        "select pg_get_userbyid(proowner) from pg_proc where oid = '$retirementFunction'::regprocedure",
                    ),
                ).isEqualTo("flyway")
                listOf(
                    "public.finalize_rag_embedding_staging(text,text,text,integer,text)",
                    "public.purge_rag_embedding_staging(text,text)",
                ).forEach { signature ->
                    assertFalse(hasPublicFunctionExecute(connection, signature))
                    assertTrue(
                        hasFunctionPrivilege(connection, "decision_rag_writer", signature),
                        "missing bounded staging function privilege on $signature",
                    )
                    listOf("decision_app", "decision_rag_query").forEach { role ->
                        assertFalse(
                            hasFunctionPrivilege(connection, role, signature),
                            "unexpected $role EXECUTE on $signature",
                        )
                    }
                    assertThat(
                        queryString(
                            connection,
                            "select pg_get_userbyid(proowner) from pg_proc where oid = '$signature'::regprocedure",
                        ),
                    ).isEqualTo("flyway")
                }
                val guardedFunctionSearchPaths =
                    queryStrings(
                        connection,
                        """
                        select unnest(proconfig)
                        from pg_proc
                        where oid in (
                          'public.guard_rag_source_revision_locator()'::regprocedure,
                          'public.guard_rag_ingest_run_transition()'::regprocedure,
                          'public.guard_rag_chunk_scope()'::regprocedure,
                          'public.guard_rag_generation_materialization()'::regprocedure,
                          'public.guard_rag_generation_transition()'::regprocedure,
                          'public.guard_rag_generation_activation()'::regprocedure,
                          'public.retire_rag_source_for_relocation(text,text)'::regprocedure,
                          'public.read_rag_source_registry(text)'::regprocedure,
                          'public.read_active_rag_chunks(text,integer)'::regprocedure,
                          'public.finalize_rag_embedding_staging(text,text,text,integer,text)'::regprocedure,
                          'public.purge_rag_embedding_staging(text,text)'::regprocedure
                        )
                        """.trimIndent(),
                    )
                assertThat(guardedFunctionSearchPaths).allMatch {
                    it == "search_path=pg_catalog, public, pg_temp" ||
                        it == "search_path=public, pg_catalog, pg_temp"
                }
                assertThat(guardedFunctionSearchPaths.count { it == "search_path=public, pg_catalog, pg_temp" })
                    .isEqualTo(1)
            }
        }
    }

    @Test
    fun `V16 rejects nonempty legacy rag_sources without partial tombstone`() {
        assertLegacyPrecondition("rag_sources")
    }

    @Test
    fun `V16 rejects nonempty legacy rag_chunks without partial tombstone`() {
        assertLegacyPrecondition("rag_chunks")
    }

    @Test
    fun `V16 rejects nonempty legacy rag_answers without partial tombstone`() {
        assertLegacyPrecondition("rag_answers")
    }

    @Test
    fun `V16 rejects nonempty legacy rag_citations without partial tombstone`() {
        assertLegacyPrecondition("rag_citations")
    }

    @Test
    fun `V16 rejects nonempty legacy rag_answer_feedback without partial tombstone`() {
        assertLegacyPrecondition("rag_answer_feedback")
    }

    @Test
    fun `V16 locks legacy tables before checking emptiness`() {
        withPreparedDatabase("concurrent_legacy_write") { jdbcUrl ->
            flyway(jdbcUrl, target = "15").migrate()
            adminConnection(jdbcUrl).use { writer ->
                writer.autoCommit = false
                seedLegacyRow(writer, "rag_sources")

                val migration =
                    CompletableFuture.supplyAsync {
                        runCatching { flyway(jdbcUrl).migrate() }
                    }
                assertThat(migration.isDone).isFalse()
                writer.commit()

                val failure = migration.get(10, TimeUnit.SECONDS).exceptionOrNull()
                assertThat(failure).isInstanceOf(FlywayException::class.java)
                assertThat(
                    generateSequence(failure) { it.cause }
                        .mapNotNull { it.message }
                        .joinToString("\n"),
                ).contains("S4 normalized RAG precondition failed")
            }

            adminConnection(jdbcUrl).use { connection ->
                assertThat(queryString(connection, "select to_regclass('public.rag_sources') is not null"))
                    .isEqualTo("t")
                assertThat(queryString(connection, "select to_regclass('public.rag_sources_v2_legacy') is null"))
                    .isEqualTo("t")
            }
        }
    }

    @Test
    fun `writer cannot skip source revisions or reopen terminal ingest and generation state`() {
        withPreparedDatabase("append_only_state") { jdbcUrl ->
            flyway(jdbcUrl).migrate()
            writerConnection(jdbcUrl).use { connection ->
                connection.createStatement().use { statement ->
                    statement.execute(
                        """
                        insert into rag_sources (
                          source_id, source_type, institution, topic, owner_identity
                        )
                        values (
                          'src_project_state_machine_001', 'PROJECT_SOURCE_CARD',
                          'project', 'state_machine', 'python-rag-corpus-privacy'
                        )
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        insert into rag_source_revisions (
                          source_revision_id, source_id, revision_seq, registry_version,
                          title, tier, access_level, license_decision, license_note, attribution,
                          retention_mode, retention_days, retention_owner, external_processing_allowed,
                          initial_processing, canonical_url, allowed_origin, allowed_path,
                          locator_sha256, metadata_hash
                        )
                        values (
                          'src_rev_11111111111111111111111111111111',
                          'src_project_state_machine_001', 1, 'test-v1',
                          'state machine card', 'PROJECT', 'PUBLIC', 'PROJECT_AUTHORED_PUBLIC',
                          'project-authored test card', 'project test attribution',
                          'PROJECT_CARD', 365, 'python-rag-corpus-privacy', false,
                          'PROJECT_AUTHORED_CARD', 'https://example.com/state-machine',
                          'https://example.com', '/state-machine', repeat('1', 64), repeat('2', 64)
                        )
                        """.trimIndent(),
                    )
                }

                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            insert into rag_source_revisions (
                              source_revision_id, source_id, revision_seq, registry_version,
                              title, tier, access_level, license_decision, license_note, attribution,
                              retention_mode, retention_days, retention_owner, external_processing_allowed,
                              initial_processing, canonical_url, allowed_origin, allowed_path,
                              locator_sha256, metadata_hash
                            )
                            values (
                              'src_rev_33333333333333333333333333333333',
                              'src_project_state_machine_001', 3, 'test-v3',
                              'state machine card v3', 'PROJECT', 'PUBLIC', 'PROJECT_AUTHORED_PUBLIC',
                              'project-authored test card', 'project test attribution',
                              'PROJECT_CARD', 365, 'python-rag-corpus-privacy', false,
                              'PROJECT_AUTHORED_CARD', 'https://example.com/state-machine',
                              'https://example.com', '/state-machine', repeat('1', 64), repeat('3', 64)
                            )
                            """.trimIndent(),
                        )
                    }
                }

                connection.createStatement().use { statement ->
                    statement.execute(
                        """
                        insert into rag_ingest_runs (
                          ingest_run_id, source_revision_id, parser_version, canonicalizer_version,
                          card_schema_version, input_content_hash, status, expected_chunk_count
                        )
                        values (
                          'rag_ing_11111111111111111111111111111111',
                          'src_rev_11111111111111111111111111111111',
                          'parser-v1', 'canonicalizer-v1', 'card-v1', repeat('4', 64), 'PLANNED', 1
                        )
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        update rag_ingest_runs
                        set status = 'RUNNING', started_at = transaction_timestamp()
                        where ingest_run_id = 'rag_ing_11111111111111111111111111111111'
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        insert into rag_sources (
                          source_id, source_type, institution, topic, owner_identity
                        )
                        values (
                          'src_project_other_lineage_001', 'PROJECT_SOURCE_CARD',
                          'project', 'other_lineage', 'python-rag-corpus-privacy'
                        )
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        insert into rag_source_revisions (
                          source_revision_id, source_id, revision_seq, registry_version,
                          title, tier, access_level, license_decision, license_note, attribution,
                          retention_mode, retention_days, retention_owner, external_processing_allowed,
                          initial_processing, canonical_url, allowed_origin, allowed_path,
                          locator_sha256, metadata_hash
                        )
                        values (
                          'src_rev_22222222222222222222222222222222',
                          'src_project_other_lineage_001', 1, 'test-v1',
                          'other lineage card', 'PROJECT', 'PUBLIC', 'PROJECT_AUTHORED_PUBLIC',
                          'project-authored test card', 'project test attribution',
                          'PROJECT_CARD', 365, 'python-rag-corpus-privacy', false,
                          'PROJECT_AUTHORED_CARD', 'https://example.com/other-lineage',
                          'https://example.com', '/other-lineage', repeat('8', 64), repeat('9', 64)
                        )
                        """.trimIndent(),
                    )
                }
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            insert into rag_source_revisions (
                              source_revision_id, source_id, revision_seq, registry_version,
                              title, tier, access_level, license_decision, license_note, attribution,
                              retention_mode, retention_days, retention_owner, external_processing_allowed,
                              initial_processing, canonical_url, allowed_origin, allowed_path,
                              locator_sha256, metadata_hash
                            )
                            values (
                              'src_rev_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                              'src_project_other_lineage_001', 2, 'test-v2',
                              'invalid upstream-shaped project card', 'OFFICIAL', 'PUBLIC',
                              'REFERENCE_ONLY_NO_EXTERNAL_PROCESSING',
                              'invalid boundary', 'project test attribution',
                              'REFERENCE_METADATA_ONLY', 365, 'python-rag-corpus-privacy', false,
                              'REFERENCE_ONLY', 'https://example.com/other-lineage',
                              'https://example.com', '/other-lineage',
                              repeat('8', 64), repeat('a', 64)
                            )
                            """.trimIndent(),
                        )
                    }
                }
                connection.createStatement().use { statement ->
                    statement.execute(
                        """
                        insert into rag_ingest_runs (
                          ingest_run_id, source_revision_id, parser_version, canonicalizer_version,
                          card_schema_version, input_content_hash, status, expected_chunk_count
                        )
                        values (
                          'rag_ing_22222222222222222222222222222222',
                          'src_rev_11111111111111111111111111111111',
                          'parser-v2', 'canonicalizer-v1', 'card-v1',
                          repeat('a', 64), 'PLANNED', 2
                        )
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        update rag_ingest_runs
                        set status = 'RUNNING', started_at = transaction_timestamp()
                        where ingest_run_id = 'rag_ing_22222222222222222222222222222222'
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        insert into rag_chunk_revisions (
                          chunk_revision_id, ingest_run_id, source_revision_id, chunk_seq,
                          heading_path, canonical_content, canonical_content_hash, token_count,
                          topic, access_level, tier
                        )
                        values (
                          'rag_chk_55555555555555555555555555555555',
                          'rag_ing_22222222222222222222222222222222',
                          'src_rev_11111111111111111111111111111111', 1,
                          array['핵심 claim'], 'failed ingest partial content',
                          repeat('e', 64), 10, 'state_machine', 'PUBLIC', 'PROJECT'
                        )
                        """.trimIndent(),
                    )
                }
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            update rag_ingest_runs
                            set status = 'FAILED', actual_chunk_count = 0,
                                completed_at = transaction_timestamp(), failure_class = 'PARSER_ERROR'
                            where ingest_run_id = 'rag_ing_22222222222222222222222222222222'
                            """.trimIndent(),
                        )
                    }
                }
                connection.createStatement().use { statement ->
                    statement.execute(
                        """
                        update rag_ingest_runs
                        set status = 'FAILED', actual_chunk_count = 1,
                            completed_at = transaction_timestamp(), failure_class = 'PARSER_ERROR'
                        where ingest_run_id = 'rag_ing_22222222222222222222222222222222'
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        insert into rag_corpus_generations (
                          corpus_generation_id, corpus_hash, embedding_profile_id, vector_space,
                          status, expected_chunk_count
                        )
                        values (
                          'rag_gen_33333333333333333333333333333333', repeat('c', 64),
                          'bge_m3_local_1024_v1', 'bge_m3_local_1024_v1', 'REGISTERED', 1
                        )
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        update rag_corpus_generations
                        set status = 'PLANNED'
                        where corpus_generation_id = 'rag_gen_33333333333333333333333333333333'
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        update rag_corpus_generations
                        set status = 'MATERIALIZING'
                        where corpus_generation_id = 'rag_gen_33333333333333333333333333333333'
                        """.trimIndent(),
                    )
                }
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            insert into rag_generation_chunks (
                              corpus_generation_id, chunk_revision_id, embedding_profile_id,
                              embedding_input_hash, context_set_hash, ordinal
                            )
                            values (
                              'rag_gen_33333333333333333333333333333333',
                              'rag_chk_55555555555555555555555555555555',
                              'bge_m3_local_1024_v1', repeat('e', 64), null, 1
                            )
                            """.trimIndent(),
                        )
                    }
                }
                connection.createStatement().use { statement ->
                    statement.execute(
                        """
                        insert into rag_sources (
                          source_id, source_type, institution, topic, owner_identity
                        )
                        values (
                          'src_kis_reference_only_001', 'UPSTREAM_REFERENCE',
                          'kis', 'reference_only', 'python-rag-corpus-privacy'
                        )
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        insert into rag_source_revisions (
                          source_revision_id, source_id, revision_seq, registry_version,
                          title, tier, access_level, license_decision, license_note, attribution,
                          retention_mode, retention_days, retention_owner, external_processing_allowed,
                          initial_processing, canonical_url, allowed_origin, allowed_path,
                          locator_sha256, metadata_hash
                        )
                        values (
                          'src_rev_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                          'src_kis_reference_only_001', 1, 'test-v1',
                          'reference-only upstream', 'OFFICIAL', 'PUBLIC',
                          'REFERENCE_ONLY_NO_EXTERNAL_PROCESSING',
                          'reference-only test metadata', 'KIS test attribution',
                          'REFERENCE_METADATA_ONLY', 365, 'python-rag-corpus-privacy', false,
                          'REFERENCE_ONLY', 'https://example.com/reference-only',
                          'https://example.com', '/reference-only',
                          repeat('b', 64), repeat('c', 64)
                        )
                        """.trimIndent(),
                    )
                }
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            insert into rag_ingest_runs (
                              ingest_run_id, source_revision_id, parser_version,
                              canonicalizer_version, card_schema_version,
                              input_content_hash, status, expected_chunk_count
                            )
                            values (
                              'rag_ing_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                              'src_rev_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                              'parser-v1', 'canonicalizer-v1', 'card-v1',
                              repeat('b', 64), 'PLANNED', 1
                            )
                            """.trimIndent(),
                        )
                    }
                }
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            insert into rag_chunk_revisions (
                              chunk_revision_id, ingest_run_id, source_revision_id, chunk_seq,
                              heading_path, canonical_content, canonical_content_hash, token_count,
                              topic, access_level, tier
                            )
                            values (
                              'rag_chk_22222222222222222222222222222222',
                              'rag_ing_11111111111111111111111111111111',
                              'src_rev_22222222222222222222222222222222', 1,
                              array['핵심 claim'], 'cross-source content', repeat('a', 64), 10,
                              'other_lineage', 'PUBLIC', 'PROJECT'
                            )
                            """.trimIndent(),
                        )
                    }
                }
                listOf(
                    Triple("wrong_topic", "PUBLIC", "PROJECT"),
                    Triple("state_machine", "INTERNAL", "PROJECT"),
                    Triple("state_machine", "PUBLIC", "OFFICIAL"),
                ).forEachIndexed { index, (topic, accessLevel, tier) ->
                    assertThrows(SQLException::class.java) {
                        connection.createStatement().use { statement ->
                            statement.execute(
                                """
                                insert into rag_chunk_revisions (
                                  chunk_revision_id, ingest_run_id, source_revision_id, chunk_seq,
                                  heading_path, canonical_content, canonical_content_hash, token_count,
                                  topic, access_level, tier
                                )
                                values (
                                  'rag_chk_3333333333333333333333333333333$index',
                                  'rag_ing_11111111111111111111111111111111',
                                  'src_rev_11111111111111111111111111111111', 1,
                                  array['핵심 claim'], 'scope mismatch content',
                                  repeat('${('b'.code + index).toChar()}', 64), 10,
                                  '$topic', '$accessLevel', '$tier'
                                )
                                """.trimIndent(),
                            )
                        }
                    }
                }
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            update rag_ingest_runs
                            set status = 'SUCCEEDED', actual_chunk_count = 1,
                                completed_at = transaction_timestamp()
                            where ingest_run_id = 'rag_ing_11111111111111111111111111111111'
                            """.trimIndent(),
                        )
                    }
                }
                connection.createStatement().use { statement ->
                    statement.execute(
                        """
                        insert into rag_chunk_revisions (
                          chunk_revision_id, ingest_run_id, source_revision_id, chunk_seq,
                          heading_path, canonical_content, canonical_content_hash, token_count,
                          topic, access_level, tier
                        )
                        values (
                          'rag_chk_11111111111111111111111111111111',
                          'rag_ing_11111111111111111111111111111111',
                          'src_rev_11111111111111111111111111111111', 1,
                          array['핵심 claim'], 'state machine content', repeat('5', 64), 10,
                          'state_machine', 'PUBLIC', 'PROJECT'
                        )
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        update rag_ingest_runs
                        set status = 'SUCCEEDED', actual_chunk_count = 1,
                            completed_at = transaction_timestamp()
                        where ingest_run_id = 'rag_ing_11111111111111111111111111111111'
                        """.trimIndent(),
                    )
                }
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            insert into rag_chunk_revisions (
                              chunk_revision_id, ingest_run_id, source_revision_id, chunk_seq,
                              heading_path, canonical_content, canonical_content_hash, token_count,
                              topic, access_level, tier
                            )
                            values (
                              'rag_chk_44444444444444444444444444444444',
                              'rag_ing_11111111111111111111111111111111',
                              'src_rev_11111111111111111111111111111111', 2,
                              array['핵심 claim'], 'terminal append', repeat('d', 64), 10,
                              'state_machine', 'PUBLIC', 'PROJECT'
                            )
                            """.trimIndent(),
                        )
                    }
                }
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            update rag_ingest_runs
                            set status = 'RUNNING', actual_chunk_count = null, completed_at = null
                            where ingest_run_id = 'rag_ing_11111111111111111111111111111111'
                            """.trimIndent(),
                        )
                    }
                }

                connection.createStatement().use { statement ->
                    statement.execute(
                        """
                        insert into rag_corpus_generations (
                          corpus_generation_id, corpus_hash, embedding_profile_id, vector_space,
                          status, expected_chunk_count
                        )
                        values (
                          'rag_gen_11111111111111111111111111111111', repeat('6', 64),
                          'bge_m3_local_1024_v1', 'bge_m3_local_1024_v1', 'REGISTERED', 1
                        )
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        update rag_corpus_generations
                        set status = 'PLANNED'
                        where corpus_generation_id = 'rag_gen_11111111111111111111111111111111'
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        update rag_corpus_generations
                        set status = 'MATERIALIZING'
                        where corpus_generation_id = 'rag_gen_11111111111111111111111111111111'
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        insert into rag_generation_chunks (
                          corpus_generation_id, chunk_revision_id, embedding_profile_id,
                          embedding_input_hash, context_set_hash, ordinal
                        )
                        values (
                          'rag_gen_11111111111111111111111111111111',
                          'rag_chk_11111111111111111111111111111111',
                          'bge_m3_local_1024_v1', repeat('7', 64), null, 1
                        )
                        """.trimIndent(),
                    )
                }
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            insert into rag_embedding_staging (
                              generation_id, materialization_run_id, chunk_revision_id,
                              embedding_profile_id, embedding_input_hash, context_set_hash,
                              embedding, staging_row_hash
                            )
                            values (
                              'rag_gen_11111111111111111111111111111111',
                              'rag_mat_11111111111111111111111111111111',
                              'rag_chk_11111111111111111111111111111111',
                              'bge_m3_local_1024_v1', repeat('7', 64), null,
                              array_fill(0.0::real, array[1024])::vector, repeat('0', 64)
                            )
                            """.trimIndent(),
                        )
                    }
                }
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            insert into rag_chunk_embeddings (
                              chunk_embedding_id, corpus_generation_id, chunk_revision_id,
                              embedding_profile_id, vector_space, embedding_input_hash,
                              context_set_hash, embedding
                            )
                            values (
                              'rag_emb_11111111111111111111111111111111',
                              'rag_gen_11111111111111111111111111111111',
                              'rag_chk_11111111111111111111111111111111',
                              'bge_m3_local_1024_v1', 'bge_m3_local_1024_v1',
                              repeat('7', 64), null,
                              ('[' || '1' || repeat(',0', 1023) || ']')::vector
                            )
                            """.trimIndent(),
                        )
                    }
                }
                connection.createStatement().use { statement ->
                    statement.execute(
                        """
                        insert into rag_embedding_staging (
                          generation_id, materialization_run_id, chunk_revision_id,
                          embedding_profile_id, embedding_input_hash, context_set_hash,
                          embedding, staging_row_hash
                        )
                        values (
                          'rag_gen_11111111111111111111111111111111',
                          'rag_mat_11111111111111111111111111111111',
                          'rag_chk_11111111111111111111111111111111',
                          'bge_m3_local_1024_v1', repeat('7', 64), null,
                          ('[' || '1' || repeat(',0', 1023) || ']')::vector, repeat('8', 64)
                        )
                        """.trimIndent(),
                    )
                    assertThat(
                        queryString(
                            connection,
                            """
                            select finalize_rag_embedding_staging(
                              'rag_gen_11111111111111111111111111111111',
                              'rag_mat_11111111111111111111111111111111',
                              'decision_rag_writer',
                              1,
                              encode(digest(repeat('8', 64), 'sha256'), 'hex')
                            )
                            """.trimIndent(),
                        ),
                    ).isEqualTo("1")
                    statement.execute(
                        """
                        update rag_corpus_generations
                        set status = 'MATERIALIZED', actual_chunk_count = 1
                        where corpus_generation_id = 'rag_gen_11111111111111111111111111111111'
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        update rag_corpus_generations
                        set status = 'EVAL_PASSED', evaluation_status = 'PASSED',
                            evaluated_at = transaction_timestamp()
                        where corpus_generation_id = 'rag_gen_11111111111111111111111111111111'
                        """.trimIndent(),
                    )
                }
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            update rag_corpus_generations
                            set status = 'ACTIVE', activated_at = transaction_timestamp()
                            where corpus_generation_id = 'rag_gen_11111111111111111111111111111111'
                            """.trimIndent(),
                        )
                    }
                }
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            update rag_corpus_generations
                            set status = 'MATERIALIZING', actual_chunk_count = 0,
                                evaluation_status = 'PENDING', evaluated_at = null, activated_at = null
                            where corpus_generation_id = 'rag_gen_11111111111111111111111111111111'
                            """.trimIndent(),
                        )
                    }
                }
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            update rag_corpus_generations
                            set status = 'DISABLED', actual_chunk_count = 0,
                                evaluation_status = 'PENDING', evaluated_at = null,
                                activated_at = null, disabled_at = transaction_timestamp()
                            where corpus_generation_id = 'rag_gen_11111111111111111111111111111111'
                            """.trimIndent(),
                        )
                    }
                }
                connection.createStatement().use { statement ->
                    statement.execute(
                        """
                        update rag_corpus_generations
                        set status = 'DISABLED', disabled_at = transaction_timestamp()
                        where corpus_generation_id = 'rag_gen_11111111111111111111111111111111'
                        """.trimIndent(),
                    )
                }
                assertThat(
                    queryString(
                        connection,
                        """
                        select (
                          status = 'DISABLED'
                          and actual_chunk_count = expected_chunk_count
                          and evaluation_status = 'PASSED'
                          and evaluated_at is not null
                          and activated_at is null
                          and disabled_at is not null
                        )
                        from rag_corpus_generations
                        where corpus_generation_id = 'rag_gen_11111111111111111111111111111111'
                        """.trimIndent(),
                    ),
                ).isEqualTo("t")
                connection.createStatement().use { statement ->
                    statement.execute(
                        """
                        insert into rag_corpus_generations (
                          corpus_generation_id, corpus_hash, embedding_profile_id, vector_space,
                          status, expected_chunk_count
                        )
                        values (
                          'rag_gen_22222222222222222222222222222222', repeat('b', 64),
                          'bge_m3_local_1024_v1', 'bge_m3_local_1024_v1', 'REGISTERED', 1
                        )
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        update rag_corpus_generations
                        set status = 'PLANNED'
                        where corpus_generation_id = 'rag_gen_22222222222222222222222222222222'
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        update rag_corpus_generations
                        set status = 'MATERIALIZING'
                        where corpus_generation_id = 'rag_gen_22222222222222222222222222222222'
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        insert into rag_generation_chunks (
                          corpus_generation_id, chunk_revision_id, embedding_profile_id,
                          embedding_input_hash, context_set_hash, ordinal
                        )
                        values (
                          'rag_gen_22222222222222222222222222222222',
                          'rag_chk_11111111111111111111111111111111',
                          'bge_m3_local_1024_v1', repeat('7', 64), null, 1
                        )
                        """.trimIndent(),
                    )
                }
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            update rag_corpus_generations
                            set status = 'MATERIALIZED', actual_chunk_count = 1
                            where corpus_generation_id = 'rag_gen_22222222222222222222222222222222'
                            """.trimIndent(),
                        )
                    }
                }
            }
        }
    }

    @Test
    fun `generation supports terminal failure and pre-active disable without reopening either receipt`() {
        withPreparedDatabase("terminal_generation_receipts") { jdbcUrl ->
            flyway(jdbcUrl).migrate()
            writerConnection(jdbcUrl).use { connection ->
                connection.createStatement().use { statement ->
                    statement.execute(
                        """
                        insert into rag_corpus_generations (
                          corpus_generation_id, corpus_hash, embedding_profile_id, vector_space,
                          status, expected_chunk_count
                        )
                        values (
                          'rag_gen_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', repeat('a', 64),
                          'bge_m3_local_1024_v1', 'bge_m3_local_1024_v1', 'REGISTERED', 1
                        )
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        update rag_corpus_generations
                        set status = 'PLANNED'
                        where corpus_generation_id = 'rag_gen_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        update rag_corpus_generations
                        set status = 'FAILED_FINAL', evaluation_status = 'FAILED',
                            failed_at = transaction_timestamp(), failure_class = 'EVALUATION_FAILED'
                        where corpus_generation_id = 'rag_gen_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
                        """.trimIndent(),
                    )
                }
                assertThat(
                    queryString(
                        connection,
                        """
                        select (
                          status = 'FAILED_FINAL'
                          and evaluation_status = 'FAILED'
                          and failed_at is not null
                          and failure_class = 'EVALUATION_FAILED'
                          and activated_at is null
                          and disabled_at is null
                        )
                        from rag_corpus_generations
                        where corpus_generation_id = 'rag_gen_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
                        """.trimIndent(),
                    ),
                ).isEqualTo("t")
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            update rag_corpus_generations
                            set status = 'DISABLED', evaluation_status = 'PENDING',
                                failed_at = null, failure_class = null,
                                disabled_at = transaction_timestamp()
                            where corpus_generation_id = 'rag_gen_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
                            """.trimIndent(),
                        )
                    }
                }

                connection.createStatement().use { statement ->
                    statement.execute(
                        """
                        insert into rag_corpus_generations (
                          corpus_generation_id, corpus_hash, embedding_profile_id, vector_space,
                          status, expected_chunk_count
                        )
                        values (
                          'rag_gen_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', repeat('b', 64),
                          'bge_m3_local_1024_v1', 'bge_m3_local_1024_v1', 'REGISTERED', 1
                        )
                        """.trimIndent(),
                    )
                    statement.execute(
                        """
                        update rag_corpus_generations
                        set status = 'DISABLED', disabled_at = transaction_timestamp()
                        where corpus_generation_id = 'rag_gen_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
                        """.trimIndent(),
                    )
                }
                assertThat(
                    queryString(
                        connection,
                        """
                        select (
                          status = 'DISABLED'
                          and actual_chunk_count = 0
                          and evaluation_status = 'PENDING'
                          and evaluated_at is null
                          and activated_at is null
                          and failed_at is null
                          and failure_class is null
                          and disabled_at is not null
                        )
                        from rag_corpus_generations
                        where corpus_generation_id = 'rag_gen_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
                        """.trimIndent(),
                    ),
                ).isEqualTo("t")
                assertThrows(SQLException::class.java) {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            update rag_corpus_generations
                            set status = 'PLANNED', disabled_at = null
                            where corpus_generation_id = 'rag_gen_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
                            """.trimIndent(),
                        )
                    }
                }
            }
        }
    }

    private fun assertLegacyPrecondition(legacyTable: String) {
        withPreparedDatabase("nonempty_${legacyTable.removePrefix("rag_")}") { jdbcUrl ->
            flyway(jdbcUrl, target = "15").migrate()
            adminConnection(jdbcUrl).use { connection ->
                seedLegacyRow(connection, legacyTable)
            }

            val failure = assertThrows(FlywayException::class.java) { flyway(jdbcUrl).migrate() }
            assertThat(generateSequence<Throwable>(failure) { it.cause }.mapNotNull { it.message }.joinToString("\n"))
                .contains("S4 normalized RAG precondition failed")
                .contains("V2 legacy RAG tables must all be empty")

            adminConnection(jdbcUrl).use { connection ->
                assertThat(
                    queryString(
                        connection,
                        "select to_regclass('public.$legacyTable') is not null",
                    ),
                ).isEqualTo("t")
                assertThat(
                    queryString(
                        connection,
                        "select to_regclass('public.${legacyTable}_v2_legacy') is null",
                    ),
                ).isEqualTo("t")
                assertThat(
                    queryString(
                        connection,
                        "select count(*) from flyway_schema_history where version = '16'",
                    ),
                ).isEqualTo("0")
            }
        }
    }

    private fun seedLegacyRow(
        connection: Connection,
        legacyTable: String,
    ) {
        connection.createStatement().use { statement ->
            if (legacyTable in setOf("rag_sources", "rag_chunks", "rag_citations")) {
                statement.execute(
                    """
                    insert into rag_sources (
                      source_id, title, source_type, tier, access_level, ingest_status
                    )
                    values ('legacy_source_001', 'legacy source', 'OFFICIAL', 'OFFICIAL', 'PUBLIC', 'REGISTERED')
                    """.trimIndent(),
                )
            }
            if (legacyTable in setOf("rag_chunks", "rag_citations")) {
                statement.execute(
                    """
                    insert into rag_chunks (
                      chunk_id, source_id, seq, content, tier, access_level,
                      content_hash, embedding_model
                    )
                    values (
                      'legacy_chunk_001', 'legacy_source_001', 1, 'legacy content',
                      'OFFICIAL', 'PUBLIC', repeat('a', 64), 'legacy-model'
                    )
                    """.trimIndent(),
                )
            }
            if (legacyTable in setOf("rag_answers", "rag_citations", "rag_answer_feedback")) {
                statement.execute(
                    """
                    insert into rag_answers (
                      answer_id, user_id, question, question_hash
                    )
                    values ('legacy_answer_001', 'usr_demo_user', 'legacy question', repeat('b', 64))
                    """.trimIndent(),
                )
            }
            when (legacyTable) {
                "rag_citations" ->
                    statement.execute(
                        """
                        insert into rag_citations (answer_id, cit_no, chunk_id, used_in_answer)
                        values ('legacy_answer_001', 1, 'legacy_chunk_001', true)
                        """.trimIndent(),
                    )
                "rag_answer_feedback" ->
                    statement.execute(
                        """
                        insert into rag_answer_feedback (answer_id, user_id, helpful)
                        values ('legacy_answer_001', 'usr_demo_user', true)
                        """.trimIndent(),
                    )
            }
        }
    }

    private fun flyway(
        jdbcUrl: String,
        target: String? = null,
    ): Flyway {
        val configuration =
            Flyway
                .configure()
                .dataSource(jdbcUrl, "flyway", FLYWAY_PASSWORD)
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

    private fun <T> withPreparedDatabase(
        label: String,
        block: (String) -> T,
    ): T {
        val databaseName = "decision_s4_${label}_${databaseCounter.incrementAndGet()}"
        require(databaseName.matches(Regex("""[a-z0-9_]+""")))
        adminConnection(postgres.jdbcUrl).use { connection ->
            connection.autoCommit = true
            connection.createStatement().use { statement ->
                statement.execute("create database $databaseName owner decision")
            }
        }
        val jdbcUrl = postgres.jdbcUrl.substringBeforeLast('/') + "/$databaseName"
        try {
            adminConnection(jdbcUrl).use { connection ->
                connection.autoCommit = true
                connection.createStatement().use { statement ->
                    statement.execute("create extension if not exists vector")
                    statement.execute("create extension if not exists pg_trgm")
                    statement.execute("create extension if not exists pgcrypto")
                    statement.execute("revoke create on schema public from public")
                    statement.execute(
                        """
                        grant usage on schema public to
                          decision_app, decision_collector, decision_disclosure_reader,
                          decision_market_writer, decision_portfolio_writer, decision_risk_writer,
                          decision_fill_writer, decision_rag_writer, decision_rag_admin,
                          decision_rag_query, flyway
                        """.trimIndent(),
                    )
                    statement.execute("grant create on schema public to flyway")
                }
            }
            return block(jdbcUrl)
        } finally {
            adminConnection(postgres.jdbcUrl).use { connection ->
                connection.autoCommit = true
                connection.createStatement().use { statement ->
                    statement.execute("drop database $databaseName")
                }
            }
        }
    }

    private fun adminConnection(jdbcUrl: String): Connection = DriverManager.getConnection(jdbcUrl, postgres.username, postgres.password)

    private fun appConnection(jdbcUrl: String): Connection = DriverManager.getConnection(jdbcUrl, "decision_app", "app-test")

    private fun writerConnection(jdbcUrl: String): Connection =
        DriverManager.getConnection(jdbcUrl, "decision_rag_writer", "rag-writer-test")

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
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    result.getBoolean(1)
                }
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
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    result.getBoolean(1)
                }
            }

    private fun hasPublicTablePrivilege(
        connection: Connection,
        table: String,
    ): Boolean =
        connection
            .prepareStatement(
                """
                select exists (
                  select 1
                  from pg_class as relation
                  cross join lateral aclexplode(
                    coalesce(relation.relacl, acldefault('r', relation.relowner))
                  ) as acl
                  where relation.oid = ('public.' || ?)::regclass
                    and acl.grantee = 0
                )
                """.trimIndent(),
            ).use { statement ->
                statement.setString(1, table)
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    result.getBoolean(1)
                }
            }

    private fun hasFunctionPrivilege(
        connection: Connection,
        role: String,
        signature: String,
    ): Boolean =
        connection
            .prepareStatement("select has_function_privilege(?, ?, 'EXECUTE')")
            .use { statement ->
                statement.setString(1, role)
                statement.setString(2, signature)
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    result.getBoolean(1)
                }
            }

    private fun hasPublicFunctionExecute(
        connection: Connection,
        signature: String,
    ): Boolean =
        connection
            .prepareStatement(
                """
                select exists (
                  select 1
                  from pg_proc as function
                  cross join lateral aclexplode(
                    coalesce(function.proacl, acldefault('f', function.proowner))
                  ) as acl
                  where function.oid = ?::regprocedure
                    and acl.grantee = 0
                    and acl.privilege_type = 'EXECUTE'
                )
                """.trimIndent(),
            ).use { statement ->
                statement.setString(1, signature)
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    result.getBoolean(1)
                }
            }

    private fun queryString(
        connection: Connection,
        sql: String,
    ): String =
        connection.createStatement().use { statement ->
            statement.executeQuery(sql).use { result ->
                assertTrue(result.next())
                result.getString(1)
            }
        }

    private fun queryStrings(
        connection: Connection,
        sql: String,
    ): List<String> =
        connection.createStatement().use { statement ->
            statement.executeQuery(sql).use { result ->
                buildList {
                    while (result.next()) {
                        add(result.getString(1))
                    }
                }
            }
        }

    private fun roleSettings(
        connection: Connection,
        role: String,
    ): List<String> =
        connection
            .prepareStatement("select unnest(rolconfig) from pg_roles where rolname = ?")
            .use { statement ->
                statement.setString(1, role)
                statement.executeQuery().use { result ->
                    buildList {
                        while (result.next()) {
                            add(result.getString(1))
                        }
                    }
                }
            }

    companion object {
        private const val FLYWAY_PASSWORD = "flyway-test"
        private val databaseCounter = AtomicInteger()
        private val normalizedTableQuery =
            """
            select tablename
            from pg_tables
            where schemaname = 'public' and tablename like 'rag_%'
            order by tablename
            """.trimIndent()
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            stablePostgresContainer(postgresImage)
                .withDatabaseName("decision_s4_migration_admin")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")
    }
}
