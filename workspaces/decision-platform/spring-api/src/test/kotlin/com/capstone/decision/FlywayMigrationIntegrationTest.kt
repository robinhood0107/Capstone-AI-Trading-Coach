package com.capstone.decision

import com.capstone.decision.application.risk.port.EvaluationSourceRequest
import com.capstone.decision.application.risk.port.InstrumentCatalogPort
import com.capstone.decision.application.risk.port.OrderMetricPort
import com.capstone.decision.application.risk.port.PortfolioContextResolution
import com.capstone.decision.application.risk.port.RiskSnapshotPort
import com.capstone.decision.domain.risk.MetricCell
import com.capstone.decision.domain.risk.MetricSource
import com.capstone.decision.domain.risk.MetricValue
import com.capstone.decision.domain.risk.OrderIntentSnapshot
import com.capstone.decision.domain.risk.PortfolioSource
import com.capstone.decision.infrastructure.risk.JdbcInternalPaperBalanceAdapter
import com.capstone.decision.infrastructure.risk.JdbcKisMockBalanceAdapter
import com.capstone.decision.infrastructure.risk.JdbcMarketQuoteAdapter
import com.capstone.decision.infrastructure.risk.JdbcPortfolioContextAdapter
import com.capstone.decision.infrastructure.risk.JdbcStoredMarginAdapter
import org.flywaydb.core.Flyway
import org.flywaydb.core.api.FlywayException
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.Arguments
import org.junit.jupiter.params.provider.MethodSource
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.dao.DataIntegrityViolationException
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.springframework.transaction.support.TransactionSynchronizationManager
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import tools.jackson.databind.json.JsonMapper
import tools.jackson.databind.node.ObjectNode
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import java.sql.DriverManager
import java.sql.SQLException
import java.time.Instant
import java.util.Base64
import java.util.HexFormat
import java.util.concurrent.ExecutionException
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.stream.Stream

// pgvector/pg_trgm/Flyway 제약은 H2로 대체 검증할 수 없어 실제 PostgreSQL 컨테이너로 잠근다.
@Testcontainers
@SpringBootTest
class FlywayMigrationIntegrationTest(
    @Autowired private val jdbcTemplate: JdbcTemplate,
    @Autowired private val portfolioContextAdapter: JdbcPortfolioContextAdapter,
    @Autowired private val marketQuoteAdapter: JdbcMarketQuoteAdapter,
    @Autowired private val kisMockBalanceAdapter: JdbcKisMockBalanceAdapter,
    @Autowired private val internalPaperBalanceAdapter: JdbcInternalPaperBalanceAdapter,
    @Autowired private val storedMarginAdapter: JdbcStoredMarginAdapter,
    @Autowired private val instrumentCatalogPort: InstrumentCatalogPort,
    @Autowired private val orderMetricPort: OrderMetricPort,
    @Autowired private val riskSnapshotPort: RiskSnapshotPort,
) : SpringApiIntegrationTestBase() {
    @Test
    fun `clean database applies V1 through V73 migrations and creates required objects`() {
        val versions = queryStrings("select version from flyway_schema_history where success order by installed_rank")
        // V7 is a Java migration and must appear alongside the SQL migrations.
        assertEquals((1..73).map(Int::toString), versions)

        val requiredTables =
            listOf(
                "users",
                "principles",
                "principle_versions",
                "decisions",
                "orders",
                "order_events",
                "order_fill_observations",
                "order_fill_application_receipts",
                "processed_event",
                "artifact_ingest_state",
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
                "rag_sources_v2_legacy",
                "rag_chunks_v2_legacy",
                "rag_answers_v2_legacy",
                "rag_citations_v2_legacy",
                "rag_answer_feedback_v2_legacy",
                "market_calendar",
                "opendart_quota_usage",
                "calendar_source_health",
                "calendar_observations",
                "trading_sessions",
                "trading_session_revisions",
                "calendar_events",
                "calendar_event_sources",
                "calendar_conflicts",
                "calendar_collection_cursors",
                "disclosure_risk_state_transitions",
                "market_quote_observations",
                "instrument_catalog_observations",
                "portfolio_balance_observations",
                "portfolio_position_observations",
                "deterministic_risk_observations",
                "daily_order_count_observations",
                "corporation_registry_observations",
                "decision_artifacts",
                "decision_traces",
                "decision_idempotency_results",
                "decision_owner_projection",
                "decision_audit_projection",
                "risk_kill_switch",
                "risk_kill_switch_transitions",
                "decision_invalidations",
                "brokerage_db_capability_keys",
                "mock_order_owner_projection",
                "kill_switch_user_projection",
                "latest_market_quote_observations",
                "latest_instrument_catalog_observations",
                "latest_portfolio_balance_observations",
                "latest_deterministic_risk_observations",
                "latest_daily_order_count_observations",
                "current_corporation_registry_projection",
                "disclosure_event_observation_projection",
                "disclosure_collection_status_projection",
                "market_source_entitlements",
                "cross_market_exposure_catalog_entries",
                "cross_market_observations",
                "analyst_revision_evidence",
                "market_cause_evidence",
                "cross_market_risk_snapshots",
                "cross_market_snapshot_evidence_links",
                "latest_cross_market_observations",
                "latest_analyst_revision_evidence",
                "latest_market_cause_evidence",
                "latest_cross_market_risk_snapshots",
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
                "foreign_news_sentiment_aggregates",
                "s48_runtime_sanitized_projections",
                "s4_9_google_grounding_monthly_budget",
                "s4_9_google_grounding_reservations",
                "s4_9_grounding_source_nodes",
                "s4_9_grounding_support_segments",
                "s4_9_grounding_support_edges",
                "s4_9_search_attempts",
                "signal_v2_production_pointers",
                "signal_universe_releases",
                "signal_model_releases",
                "signal_model_release_transitions",
                "signal_batches",
                "signal_batch_members",
                "active_signal_model_release",
                "active_signal_batch",
                "signal_batch_publications",
            )
        requiredTables.forEach { tableName ->
            assertTrue(tableExists(tableName), "expected table $tableName to exist")
        }

        assertEquals(1, countMarketCalendarRows("KRX", "2026-06-23", true))
        assertEquals(1, countMarketCalendarRows("KRX", "2026-01-01", false))
        assertEquals("VIEW", tableType("market_calendar"))
        assertEquals(2, countRows("trading_sessions", "canonical_rule_version = 'V4_COMPAT_MIGRATION'"))
        assertEquals(2, countRows("trading_session_revisions", "canonical_rule_version = 'V4_COMPAT_MIGRATION'"))
        assertTrue(indexExists("rag_chunk_revisions_trgm_idx"), "expected pg_trgm index for Korean keyword search")
        assertFalse(
            indexDefinitionLike("rag_chunk_embeddings", "%ivfflat%"),
            "ivfflat must wait until real embeddings are loaded",
        )
    }

    @Test
    fun `V74 preserves audit objects and revokes every LightGBM production mutation capability`() {
        val url = createDatabase("s5_research_only_v74")
        flyway(url, target = "74").migrate()

        DriverManager.getConnection(url, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement
                    .executeQuery(
                        "select version from flyway_schema_history where success order by installed_rank",
                    ).use { result ->
                        val versions = mutableListOf<String>()
                        while (result.next()) versions += result.getString(1)
                        assertEquals((1..74).map(Int::toString), versions)
                    }
                val revoked =
                    listOf(
                        "decision_signal_writer" to
                            "public.stage_signal_model_release(text,text,text,text,text,text,text,text,text,text,text)",
                        "decision_signal_writer" to
                            "public.stage_signal_batch(text,text,text,text,text,text,date,timestamp with time zone,text)",
                        "decision_signal_scheduler" to "public.publish_active_signal_batch(text,text,text)",
                        "decision_signal_scheduler" to "public.suspend_signal_model_for_drift(text,text)",
                        "decision_signal_admin" to
                            "public.activate_signal_model_and_batch(text,text,text,text,text,text,text)",
                        "decision_signal_admin" to "public.suspend_signal_model_for_drift(text,text)",
                    )
                revoked.forEach { (role, function) ->
                    statement
                        .executeQuery(
                            "select has_function_privilege('$role', '$function', 'EXECUTE')",
                        ).use { result ->
                            assertTrue(result.next())
                            assertFalse(result.getBoolean(1), "$role retained EXECUTE on $function")
                        }
                }
                statement
                    .executeQuery(
                        "select count(*) from information_schema.tables " +
                            "where table_schema='public' and table_name in " +
                            "('signal_model_releases','signal_batches','signal_batch_members')",
                    ).use { result ->
                        assertTrue(result.next())
                        assertEquals(3, result.getInt(1))
                    }
            }
        }
    }

    @Test
    fun `V86 preserves neutral market data roles and locks the V76 daily chain`() {
        val url = createDatabase("s5_7b_market_data_v75")
        flyway(url).migrate()

        DriverManager.getConnection(url, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement
                    .executeQuery(
                        "select version from flyway_schema_history where success order by installed_rank",
                    ).use { result ->
                        val versions = mutableListOf<String>()
                        while (result.next()) versions += result.getString(1)
                        assertEquals((1..87).map(Int::toString), versions)
                    }
                val privileges =
                    mapOf(
                        "writer_insert" to
                            "has_table_privilege('decision_market_writer','market_data_bars','INSERT')",
                        "writer_update" to
                            "has_table_privilege('decision_market_writer','market_data_bars','UPDATE')",
                        "writer_delete" to
                            "has_table_privilege('decision_market_writer','market_data_bars','DELETE')",
                        "operational_view" to
                            "has_table_privilege('decision_market_operational_reader','market_data_operational_bars','SELECT')",
                        "operational_raw" to
                            "has_table_privilege('decision_market_operational_reader','market_data_bars','SELECT')",
                        "research_view" to
                            "has_table_privilege('decision_market_research_reader','market_data_research_bars','SELECT')",
                        "app_operational" to
                            "has_table_privilege('decision_app','market_data_operational_bars','SELECT')",
                        "app_research" to
                            "has_table_privilege('decision_app','market_data_research_bars','SELECT')",
                    )
                privileges.forEach { (name, expression) ->
                    statement.executeQuery("select $expression").use { result ->
                        assertTrue(result.next())
                        val expected = name in setOf("writer_insert", "operational_view", "research_view")
                        assertEquals(expected, result.getBoolean(1), name)
                    }
                }
                statement
                    .executeQuery(
                        "select has_function_privilege(" +
                            "'decision_market_retention_admin'," +
                            "'prune_market_data_macro(date,boolean)','EXECUTE')",
                    ).use { result ->
                        assertTrue(result.next())
                        assertTrue(result.getBoolean(1))
                    }
                statement
                    .executeQuery(
                        "select has_function_privilege(" +
                            "'decision_market_writer'," +
                            "'prune_market_data_macro(date,boolean)','EXECUTE')",
                    ).use { result ->
                        assertTrue(result.next())
                        assertFalse(result.getBoolean(1))
                    }
            }
        }

        val manifestA = "a".repeat(64)
        val manifestB = "b".repeat(64)
        val source = "c".repeat(64)
        val archive = "d".repeat(64)
        val calendar = "e".repeat(64)
        val insert =
            """
            insert into market_data_manifests (
              manifest_sha256, manifest_kind, contract_id, session_date, as_of,
              generation, source_manifest_sha256, archive_sha256,
              calendar_revision, calendar_sha256, temporal_quality
            ) values (?, 'SEED', 'market-data-seed.v1', date '2026-08-13',
              timestamptz '2026-08-19 13:08:04+00', 1, ?, ?,
              'XKRX-4.13.2+KIS_CTCA0903R', ?, 'RECONSTRUCTED_FIXED_LAG')
            """.trimIndent()
        DriverManager.getConnection(url, "decision_market_writer", MARKET_WRITER_PASSWORD).use { writer ->
            writer.prepareStatement(insert).use { statement ->
                statement.setString(1, manifestA)
                statement.setString(2, source)
                statement.setString(3, archive)
                statement.setString(4, calendar)
                assertEquals(1, statement.executeUpdate())
                assertEquals(0, statement.executeUpdate(), "same session and same SHA must be a no-op")
            }
            val conflict =
                assertThrows<SQLException> {
                    writer.prepareStatement(insert).use { statement ->
                        statement.setString(1, manifestB)
                        statement.setString(2, source)
                        statement.setString(3, archive)
                        statement.setString(4, calendar)
                        statement.executeUpdate()
                    }
                }
            assertTrue(conflict.message.orEmpty().contains("NEEDS_HUMAN"))
        }
    }

    @Test
    fun `V77 publishes snapshot and complete report atomically with PIT reader and immutable replay`() {
        val url = createDatabase("s6_5_financial_engineering_v77")
        flyway(url).migrate()
        val numeric = "{\"x\":1}"
        val numericHash = sha256Hex(numeric)
        val steps =
            """[{"name":"STORED_COLLECTION"},{"name":"FEATURE"},{"name":"INFERENCE"},""" +
                """{"name":"SNAPSHOT"},{"name":"REPORT"}]"""

        fun append(artifactHash: String): String =
            DriverManager.getConnection(url, "decision_market_writer", MARKET_WRITER_PASSWORD).use { writer ->
                writer
                    .prepareStatement(
                        """
                        select outcome from append_financial_engineering_result(
                          ?::uuid, 1, '005930', date '2026-08-20',
                          timestamptz '2026-08-20 06:30:00+00',
                          timestamptz '2026-08-20 23:10:00+00',
                          ?, ?, ?, ?, 'AVAILABLE', 'PASS', 'FRESH', ?, '{}', ?, 4096, ?
                        )
                        """.trimIndent(),
                    ).use { statement ->
                        statement.setString(1, "00000000-0000-4000-8000-000000000605")
                        statement.setString(2, "1".repeat(64))
                        statement.setString(3, "2".repeat(64))
                        statement.setString(4, numericHash)
                        statement.setString(5, artifactHash)
                        statement.setString(6, numeric)
                        statement.setString(7, "5".repeat(64))
                        statement.setString(8, steps)
                        statement.executeQuery().use { result ->
                            assertTrue(result.next())
                            result.getString(1)
                        }
                    }
            }

        assertEquals("INSERTED", append("4".repeat(64)))
        assertEquals("NO_OP", append("4".repeat(64)))
        val conflict = assertThrows<SQLException> { append("6".repeat(64)) }
        assertEquals("23505", conflict.sqlState)

        DriverManager.getConnection(url, "decision_app", APP_PASSWORD).use { reader ->
            reader
                .prepareStatement("select count(*) from read_financial_engineering_snapshot('005930', ?::timestamptz)")
                .use { statement ->
                    statement.setString(1, "2026-08-20T23:09:59Z")
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        assertEquals(0, result.getInt(1))
                    }
                    statement.setString(1, "2026-08-20T23:10:00Z")
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        assertEquals(1, result.getInt(1))
                    }
                }
        }
        DriverManager.getConnection(url, postgres.username, postgres.password).use { admin ->
            val immutable =
                assertThrows<SQLException> {
                    admin.createStatement().executeUpdate(
                        "update financial_engineering_snapshots set quality='WARN'",
                    )
                }
            assertEquals("55000", immutable.sqlState)
        }
        DriverManager.getConnection(url, postgres.username, postgres.password).use { admin ->
            admin.createStatement().use { statement ->
                for (privilege in listOf("SELECT", "INSERT", "UPDATE", "DELETE")) {
                    statement
                        .executeQuery(
                            "select has_table_privilege('decision_app'," +
                                "'financial_engineering_snapshots','$privilege')",
                        ).use { result ->
                            assertTrue(result.next())
                            assertFalse(result.getBoolean(1), privilege)
                        }
                }
            }
        }
    }

    @Test
    fun `V72 Signal v2 exact ingest replays rejects conflicts rolls back and blocks fake pointer`() {
        fun payloadDigest(payload: String): String =
            HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(payload.toByteArray()))

        fun call(
            connection: java.sql.Connection,
            evaluationId: String,
            payload: String,
        ): Pair<String, String> {
            connection
                .prepareStatement(
                    """
                    SELECT outcome, signal_id FROM ingest_signal_v2_exact(
                      ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                    )
                    """.trimIndent(),
                ).use { statement ->
                    statement.setString(1, "signal-v2-runtime-v1")
                    statement.setString(2, "LIGHTGBM")
                    statement.setString(3, "decision-platform")
                    statement.setString(4, "005930")
                    statement.setObject(5, java.time.LocalDate.of(2026, 8, 14))
                    statement.setObject(6, null)
                    statement.setString(7, "1d")
                    statement.setString(8, "ABSTAIN")
                    statement.setString(9, "MISSING_EVIDENCE")
                    statement.setObject(10, null)
                    statement.setObject(11, null)
                    statement.setObject(12, null)
                    statement.setString(13, evaluationId)
                    statement.setString(14, "lgbm-v1-fixture")
                    statement.setString(15, "mrp-fixture")
                    statement.setString(16, "a".repeat(64))
                    statement.setString(17, payloadDigest(payload))
                    statement.setString(18, "b".repeat(64))
                    statement.setBoolean(19, true)
                    statement.setString(20, "FAKE_CONTRACT")
                    statement.setString(21, payload)
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        return result.getString("outcome") to result.getString("signal_id")
                    }
                }
        }

        val payload = """{"reason":"MISSING_EVIDENCE","status":"ABSTAIN"}"""
        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { connection ->
            connection.autoCommit = false
            val inserted = call(connection, "eval-v72-replay", payload)
            val replayed = call(connection, "eval-v72-replay", payload)
            assertEquals("INSERTED", inserted.first)
            assertEquals("REPLAYED", replayed.first)
            assertEquals(inserted.second, replayed.second)
            connection.commit()

            val identity =
                jdbcTemplate.queryForObject(
                    "SELECT logical_identity_sha256 FROM ingested_signals WHERE signal_id = ?",
                    String::class.java,
                    inserted.second,
                )
            val pointerError =
                assertThrows<SQLException> {
                    connection.prepareStatement("SELECT activate_signal_v2_production_pointer(?)").use { statement ->
                        statement.setString(1, identity)
                        statement.execute()
                    }
                }
            assertEquals("22023", pointerError.sqlState)
            connection.rollback()

            connection.autoCommit = false
            call(connection, "eval-v72-rollback", payload)
            val conflict =
                assertThrows<SQLException> {
                    call(connection, "eval-v72-rollback", """{"reason":"PRODUCER_FAILED","status":"ABSTAIN"}""")
                }
            assertEquals("23505", conflict.sqlState)
            connection.rollback()

            val directRead =
                assertThrows<SQLException> {
                    connection.createStatement().use { it.executeQuery("SELECT * FROM ingested_signals") }
                }
            assertEquals("42501", directRead.sqlState)
            connection.rollback()
        }
        assertEquals(
            0,
            jdbcTemplate.queryForObject(
                "SELECT count(*) FROM ingested_signals WHERE evaluation_id = 'eval-v72-rollback'",
                Int::class.java,
            ),
        )
        assertEquals(
            0,
            jdbcTemplate.queryForObject(
                "SELECT count(*) FROM signal_v2_production_pointers",
                Int::class.java,
            ),
        )
        assertFalse(hasTablePrivilege("decision_app", "ingested_signals", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "ingested_signals", "INSERT"))
        assertTrue(
            hasFunctionPrivilege(
                "decision_app",
                "ingest_signal_v2_exact(text,text,text,text,date,timestamp with time zone,text,text,text,text,numeric,numeric,text,text,text,text,text,text,boolean,text,text)",
            ),
        )
    }

    @Test
    fun `V73 stages exact release batch activates atomically and suspends LightGBM on drift`() {
        clearS5ProductionState()
        val releaseId = "lgr-${"1".repeat(12)}"
        val modelVersion = "lgbm-v1-${"2".repeat(12)}"
        val reportId = "mrp-${"3".repeat(12)}"
        val releaseManifestText =
            """{"calendarName":"XKRX","calendarVersion":"4.13.2","codeHead":"${"e".repeat(
                40,
            )}","codeTree":"${"f".repeat(
                40,
            )}","featureManifestSha256":"${"b".repeat(
                64,
            )}","files":{},"fixture":false,"modelReleaseId":"$releaseId","modelReportId":"$reportId","modelVersion":"$modelVersion","provenanceClass":"PRODUCTION","releaseVersion":"s5-model-release-v1","semanticSha256":"${"7".repeat(
                64,
            )}","sourceBundleSetSha256":"${"c".repeat(
                64,
            )}","sourcePolicySetSha256":"${"8".repeat(
                64,
            )}","status":"QUALIFIED","temporalQuality":"RECONSTRUCTED_FIXED_LAG","trainingDatasetSha256":"${"d".repeat(
                64,
            )}","uvLockSha256":"${"1".repeat(64)}"}"""
        val releaseManifest = sha256Hex(releaseManifestText)
        val kst = java.time.ZoneId.of("Asia/Seoul")
        // calendar-date 산술 대신 실제 연속 XKRX session fixture를 사용한다.
        val nextSession = java.time.LocalDate.of(2026, 8, 14)
        val postHolidaySession = java.time.LocalDate.of(2026, 8, 18)
        // activation은 현재 clock과 같은 batch만 허용하므로 대상 pair를 DB clock에서 읽는다.
        val (latestSession, asOf) = prepareS5CurrentClock()
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { flyway ->
            flyway.prepareStatement("SELECT session_date, as_of FROM s5_signal_batch_clock_at(?)").use { statement ->
                statement.setObject(
                    1,
                    postHolidaySession.atTime(8, 10).atZone(kst).toOffsetDateTime(),
                )
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    assertEquals(nextSession, result.getObject("session_date", java.time.LocalDate::class.java))
                    assertEquals(
                        postHolidaySession.atTime(8, 10).atZone(kst).toInstant(),
                        result.getTimestamp("as_of").toInstant(),
                    )
                }
            }
        }
        DriverManager.getConnection(postgres.jdbcUrl, "decision_signal_writer", "signal-writer-test").use { writer ->
            writer.prepareStatement("SELECT stage_signal_model_release(?,?,?,?,?,?,?,?,?,?,?)").use { statement ->
                listOf(
                    releaseId,
                    modelVersion,
                    reportId,
                    "b".repeat(64),
                    "c".repeat(64),
                    "d".repeat(64),
                    "e".repeat(40),
                    "f".repeat(40),
                    "1".repeat(64),
                ).forEachIndexed { index, value -> statement.setString(index + 3, value) }
                statement.setString(1, releaseManifest)
                statement.setString(2, releaseManifestText)
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    assertEquals("INSERTED", result.getString(1))
                }
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    assertEquals("REPLAYED", result.getString(1))
                }
            }
            val poisonedRelease =
                assertThrows<SQLException> {
                    writer.prepareStatement("SELECT stage_signal_model_release(?,?,?,?,?,?,?,?,?,?,?)").use { statement ->
                        val values =
                            listOf(
                                releaseManifest,
                                releaseManifestText,
                                releaseId,
                                "lgbm-v1-${"9".repeat(12)}",
                                reportId,
                                "b".repeat(64),
                                "c".repeat(64),
                                "d".repeat(64),
                                "e".repeat(40),
                                "f".repeat(40),
                                "1".repeat(64),
                            )
                        values.forEachIndexed { index, value -> statement.setString(index + 1, value) }
                        statement.executeQuery()
                    }
                }
            assertEquals("22023", poisonedRelease.sqlState)
            val oversizedRelease =
                assertThrows<SQLException> {
                    writer.prepareStatement("SELECT stage_signal_model_release(?,?,?,?,?,?,?,?,?,?,?)").use { statement ->
                        listOf(
                            releaseManifest,
                            "{".repeat(1_048_577),
                            releaseId,
                            modelVersion,
                            reportId,
                            "b".repeat(64),
                            "c".repeat(64),
                            "d".repeat(64),
                            "e".repeat(40),
                            "f".repeat(40),
                            "1".repeat(64),
                        ).forEachIndexed { index, value -> statement.setString(index + 1, value) }
                        statement.executeQuery()
                    }
                }
            assertEquals("22023", oversizedRelease.sqlState)

            val symbols = ((1..29).map { it.toString().padStart(6, '0') } + "005930" + "132030").sorted()
            val membershipJson = symbols.joinToString(separator = ",", prefix = "[", postfix = "]") { "\"$it\"" }
            val membershipDigest =
                HexFormat.of().formatHex(
                    MessageDigest
                        .getInstance("SHA-256")
                        .digest(
                            "s5-inference-universe-v1\u0000$membershipJson".toByteArray(),
                        ),
                )
            val universeId = "sur-${membershipDigest.take(12)}"
            val asOfText = asOf.toInstant().toString()
            val members =
                symbols.joinToString(prefix = "[", postfix = "]") { symbol ->
                    """{"asOf":"$asOfText","confidence":0.5,"modelReportId":"$reportId","modelVersion":"$modelVersion","signal":"HOLD","status":"AVAILABLE","symbol":"$symbol"}"""
                }
            val membersDigest = sha256Hex(members)
            val batchId = "sgb-${"4".repeat(12)}"
            val batchManifestText =
                """{"asOf":"$asOfText","batchPurpose":"DAILY","batchVersion":"s5-signal-batch-v1","fixture":false,"membershipSha256":"$membershipDigest","membersSha256":"$membersDigest","modelReleaseId":"$releaseId","parquetFile":"signals.parquet","parquetSha256":"${"6".repeat(
                    64,
                )}","provenanceClass":"PRODUCTION","rowCount":31,"semanticSha256":"${"5".repeat(
                    64,
                )}","sessionDate":"$latestSession","signalBatchId":"$batchId","timeframe":"1d","universeReleaseId":"$universeId"}"""
            val batchManifest = sha256Hex(batchManifestText)
            writer.prepareStatement("SELECT stage_signal_batch(?,?,?,?,?,?,?,?,?)").use { statement ->
                statement.setString(1, batchManifest)
                statement.setString(2, batchManifestText)
                statement.setString(3, batchId)
                statement.setString(4, releaseId)
                statement.setString(5, universeId)
                statement.setString(6, membershipDigest)
                statement.setObject(7, latestSession)
                statement.setObject(8, asOf)
                statement.setString(9, members)
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    assertEquals("INSERTED", result.getString(1))
                }
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    assertEquals("REPLAYED", result.getString(1))
                }
            }
            val poisonedMembers = members.replaceFirst("\"signal\":\"HOLD\"", "\"signal\":\"BUY\"")
            val poisonedBatch =
                assertThrows<SQLException> {
                    writer.prepareStatement("SELECT stage_signal_batch(?,?,?,?,?,?,?,?,?)").use { statement ->
                        statement.setString(1, batchManifest)
                        statement.setString(2, batchManifestText)
                        statement.setString(3, batchId)
                        statement.setString(4, releaseId)
                        statement.setString(5, universeId)
                        statement.setString(6, membershipDigest)
                        statement.setObject(7, latestSession)
                        statement.setObject(8, asOf)
                        statement.setString(9, poisonedMembers)
                        statement.executeQuery()
                    }
                }
            assertEquals("22023", poisonedBatch.sqlState)
            val oversizedMembers =
                assertThrows<SQLException> {
                    writer.prepareStatement("SELECT stage_signal_batch(?,?,?,?,?,?,?,?,?)").use { statement ->
                        statement.setString(1, batchManifest)
                        statement.setString(2, batchManifestText)
                        statement.setString(3, batchId)
                        statement.setString(4, releaseId)
                        statement.setString(5, universeId)
                        statement.setString(6, membershipDigest)
                        statement.setObject(7, latestSession)
                        statement.setObject(8, asOf)
                        statement.setString(9, "[".repeat(262_145))
                        statement.executeQuery()
                    }
                }
            assertEquals("22023", oversizedMembers.sqlState)
            val staleBatchId = "sgb-${"9".repeat(12)}"
            val staleAsOf = latestSession.atTime(8, 10).atZone(kst).toOffsetDateTime()
            val staleAsOfText = staleAsOf.toInstant().toString()
            val staleMembers =
                symbols.joinToString(prefix = "[", postfix = "]") { symbol ->
                    """{"asOf":"$staleAsOfText","confidence":0.5,"modelReportId":"$reportId","modelVersion":"$modelVersion","signal":"HOLD","status":"AVAILABLE","symbol":"$symbol"}"""
                }
            val staleManifestText =
                """{"asOf":"$staleAsOfText","batchPurpose":"DAILY","batchVersion":"s5-signal-batch-v1","fixture":false,"membershipSha256":"$membershipDigest","membersSha256":"${sha256Hex(
                    staleMembers,
                )}","modelReleaseId":"$releaseId","parquetFile":"signals.parquet","parquetSha256":"${"9".repeat(
                    64,
                )}","provenanceClass":"PRODUCTION","rowCount":31,"semanticSha256":"${"9".repeat(
                    64,
                )}","sessionDate":"${latestSession.minusDays(
                    1,
                )}","signalBatchId":"$staleBatchId","timeframe":"1d","universeReleaseId":"$universeId"}"""
            val staleManifest = sha256Hex(staleManifestText)
            writer.prepareStatement("SELECT stage_signal_batch(?,?,?,?,?,?,?,?,?)").use { statement ->
                statement.setString(1, staleManifest)
                statement.setString(2, staleManifestText)
                statement.setString(3, staleBatchId)
                statement.setString(4, releaseId)
                statement.setString(5, universeId)
                statement.setString(6, membershipDigest)
                statement.setObject(7, latestSession.minusDays(1))
                statement.setObject(8, staleAsOf)
                statement.setString(9, staleMembers)
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    assertEquals("INSERTED", result.getString(1))
                }
            }

            DriverManager.getConnection(postgres.jdbcUrl, "decision_signal_admin", "signal-admin-test").use { admin ->
                admin.prepareStatement("SELECT activate_signal_model_and_batch(?,?,?,?,?,?,?)").use { statement ->
                    statement.setString(1, releaseId)
                    statement.setString(2, batchId)
                    statement.setString(3, "")
                    statement.setString(4, "")
                    statement.setString(5, releaseManifest)
                    statement.setString(6, batchManifest)
                    statement.setString(7, "MANUAL_ACTIVATION")
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        assertEquals(1L, result.getLong(1))
                    }
                }
            }

            DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { app ->
                app.prepareStatement("SELECT status, signal, confidence FROM read_production_signal_v2(?)").use { statement ->
                    statement.setString(1, "005930")
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        assertEquals("AVAILABLE", result.getString("status"))
                        assertEquals("HOLD", result.getString("signal"))
                    }
                }
                val direct =
                    assertThrows<SQLException> {
                        app.createStatement().use { it.executeQuery("SELECT * FROM signal_model_releases") }
                    }
                assertEquals("42501", direct.sqlState)
            }

            DriverManager
                .getConnection(
                    postgres.jdbcUrl,
                    "decision_signal_scheduler",
                    "signal-scheduler-test",
                ).use { scheduler ->
                    val stalePublish =
                        assertThrows<SQLException> {
                            scheduler.prepareStatement("SELECT publish_active_signal_batch(?,?,?)").use { statement ->
                                statement.setString(1, staleBatchId)
                                statement.setString(2, batchId)
                                statement.setString(3, staleManifest)
                                statement.executeQuery()
                            }
                        }
                    assertEquals("22023", stalePublish.sqlState)
                    scheduler.prepareStatement("SELECT publish_active_signal_batch(?,?,?)").use { statement ->
                        statement.setString(1, batchId)
                        statement.setString(2, "sgb-${"8".repeat(12)}")
                        statement.setString(3, batchManifest)
                        statement.executeQuery().use { result ->
                            assertTrue(result.next())
                            assertEquals(1L, result.getLong(1))
                        }
                    }
                    assertEquals(
                        1,
                        jdbcTemplate.queryForObject(
                            "SELECT count(*) FROM signal_batch_publications WHERE signal_batch_id = ?",
                            Int::class.java,
                            batchId,
                        ),
                    )
                    scheduler.prepareStatement("SELECT suspend_signal_model_for_drift(?,?)").use { statement ->
                        statement.setString(1, releaseId)
                        statement.setString(2, "6".repeat(64))
                        statement.execute()
                    }
                }
            DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { app ->
                app.prepareStatement("SELECT status, reason, signal FROM read_production_signal_v2(?)").use { statement ->
                    statement.setString(1, "005930")
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        assertEquals("ABSTAIN", result.getString("status"))
                        assertEquals("ARTIFACT_DRIFT", result.getString("reason"))
                        assertEquals(null, result.getString("signal"))
                    }
                }
            }
        }
        listOf(
            "signal_model_releases",
            "signal_batches",
            "signal_batch_members",
            "active_signal_model_release",
            "active_signal_batch",
            "signal_batch_publications",
        ).forEach { table ->
            listOf("SELECT", "INSERT", "UPDATE", "DELETE").forEach { privilege ->
                assertFalse(hasTablePrivilege("decision_signal_writer", table, privilege))
                assertFalse(hasTablePrivilege("decision_signal_scheduler", table, privilege))
                assertFalse(hasTablePrivilege("decision_signal_admin", table, privilege))
            }
        }
        assertTrue(
            hasFunctionPrivilege(
                "decision_signal_writer",
                "stage_signal_model_release(text,text,text,text,text,text,text,text,text,text,text)",
            ),
        )
        assertFalse(
            hasFunctionPrivilege("decision_signal_writer", "publish_active_signal_batch(text,text,text)"),
        )
        assertTrue(
            hasFunctionPrivilege("decision_signal_scheduler", "publish_active_signal_batch(text,text,text)"),
        )
        assertFalse(
            hasFunctionPrivilege(
                "decision_signal_scheduler",
                "activate_signal_model_and_batch(text,text,text,text,text,text,text)",
            ),
        )
        assertTrue(
            hasFunctionPrivilege(
                "decision_signal_admin",
                "activate_signal_model_and_batch(text,text,text,text,text,text,text)",
            ),
        )
        assertFalse(
            hasFunctionPrivilege(
                "decision_signal_admin",
                "stage_signal_model_release(text,text,text,text,text,text,text,text,text,text,text)",
            ),
        )
    }

    @Test
    fun `V73 manual rollback publishes a fresh batch and never re-exposes the old signal`() {
        val (session, asOf) = prepareS5CurrentClock()
        clearS5ProductionState()
        val prior = stageS5Candidate("a", session, asOf, "BUY")
        val replacement = stageS5Candidate("b", session, asOf, "HOLD")
        DriverManager.getConnection(postgres.jdbcUrl, "decision_signal_admin", "signal-admin-test").use { admin ->
            assertEquals(
                1L,
                activateS5(
                    admin,
                    prior,
                    expectedReleaseId = "",
                    expectedBatchId = "",
                    reason = "MANUAL_ACTIVATION",
                ),
            )
            assertEquals(
                2L,
                activateS5(
                    admin,
                    replacement,
                    expectedReleaseId = prior.releaseId,
                    expectedBatchId = prior.batchId,
                    reason = "MANUAL_ACTIVATION",
                ),
            )
            val rollback =
                stageS5Candidate(
                    "a",
                    session,
                    asOf,
                    "BUY",
                    batchPurpose = "ROLLBACK",
                    batchSeed = "e",
                )
            assertEquals(
                3L,
                activateS5(
                    admin,
                    rollback,
                    expectedReleaseId = replacement.releaseId,
                    expectedBatchId = replacement.batchId,
                    reason = "MANUAL_ROLLBACK",
                ),
            )
        }
        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { app ->
            app.prepareStatement("SELECT signal, as_of FROM read_production_signal_v2(?)").use { statement ->
                statement.setString(1, "005930")
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    assertEquals("BUY", result.getString("signal"))
                    assertEquals(asOf.toInstant(), result.getTimestamp("as_of").toInstant())
                }
            }
        }
        assertEquals(
            1,
            jdbcTemplate.queryForObject(
                "SELECT count(*) FROM signal_batch_publications WHERE signal_batch_id = ? AND reason = 'MANUAL_ROLLBACK'",
                Int::class.java,
                "sgb-${"e".repeat(12)}",
            ),
        )
    }

    @Test
    fun `V73 empty pointer activation CAS serializes concurrent admins`() {
        val (session, asOf) = prepareS5CurrentClock()
        clearS5ProductionState()
        val first = stageS5Candidate("c", session, asOf, "HOLD")
        val second = stageS5Candidate("d", session, asOf, "SELL")
        val executor = Executors.newSingleThreadExecutor()
        DriverManager.getConnection(postgres.jdbcUrl, "decision_signal_admin", "signal-admin-test").use { firstAdmin ->
            DriverManager.getConnection(postgres.jdbcUrl, "decision_signal_admin", "signal-admin-test").use { secondAdmin ->
                firstAdmin.autoCommit = false
                secondAdmin.autoCommit = false
                assertEquals(1L, activateS5(firstAdmin, first, "", "", "MANUAL_ACTIVATION"))
                val loser =
                    executor.submit<Long> {
                        activateS5(secondAdmin, second, "", "", "MANUAL_ACTIVATION")
                    }
                assertFalse(loser.isDone, "second activation must wait on the permanent advisory lock")
                firstAdmin.commit()
                val failure = assertThrows<ExecutionException> { loser.get(5, TimeUnit.SECONDS) }
                assertEquals("40001", (failure.cause as SQLException).sqlState)
                secondAdmin.rollback()
            }
        }
        executor.shutdownNow()
        assertEquals(
            1,
            jdbcTemplate.queryForObject(
                "SELECT count(*) FROM signal_batch_publications WHERE signal_batch_id IN (?, ?)",
                Int::class.java,
                first.batchId,
                second.batchId,
            ),
        )
    }

    @Test
    fun `V23 permits hash-stable fixture replay but blocks mutation snapshot writing and cross-owner reads`() {
        val storageTables =
            listOf(
                "market_source_entitlements",
                "cross_market_exposure_catalog_entries",
                "cross_market_observations",
                "analyst_revision_evidence",
                "market_cause_evidence",
                "cross_market_risk_snapshots",
                "cross_market_snapshot_evidence_links",
            )
        storageTables.forEach { table ->
            listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(hasTablePrivilege("decision_app", table, privilege), "unexpected app $privilege on $table")
                assertFalse(
                    hasTablePrivilege("decision_market_writer", table, privilege),
                    "unexpected writer $privilege on $table",
                )
            }
        }
        listOf(
            "append_market_source_entitlement(jsonb)",
            "append_cross_market_exposure_catalog_entry(jsonb)",
            "append_cross_market_observation(jsonb)",
            "append_analyst_revision_evidence(jsonb)",
            "append_market_cause_evidence(jsonb)",
        ).forEach { function ->
            assertTrue(hasFunctionPrivilege("decision_market_writer", function), "missing writer EXECUTE on $function")
            assertFalse(hasFunctionPrivilege("decision_app", function), "unexpected app EXECUTE on $function")
        }
        assertFalse(
            functionExists("append_cross_market_risk_snapshot(jsonb)"),
            "S4.8B must not create snapshot materialization authority",
        )
        listOf(
            "latest_cross_market_observations",
            "latest_analyst_revision_evidence",
            "latest_market_cause_evidence",
            "latest_cross_market_risk_snapshots",
        ).forEach { view ->
            assertTrue(hasTablePrivilege("decision_app", view, "SELECT"), "missing bounded read on $view")
        }

        val entitlement =
            """
            {
              "activationStatus":"CANDIDATE_DISABLED",
              "category":"OVERSEAS_LEAD",
              "contractExpiry":"2027-07-31T00:00:00Z",
              "decisionAuthority":"NONE",
              "derivedDataAllowed":false,
              "embeddingAllowed":false,
              "externalLlmAllowed":false,
              "logicalIdentityHash":"${"a".repeat(64)}",
              "machineFetchAllowed":false,
              "providerCallsAllowed":false,
              "rawStoreAllowed":false,
              "sourceFamily":"KIS",
              "sourceId":"KIS_DISABLED_04"
            }
            """.trimIndent()
        assertEquals("INSERTED", callMarketWriterAppend("append_market_source_entitlement", entitlement))
        assertEquals("REPLAY", callMarketWriterAppend("append_market_source_entitlement", entitlement))
        val conflicting = entitlement.replace("OVERSEAS_LEAD", "DOMESTIC_AMPLIFICATION")
        val conflict =
            assertThrows<SQLException> {
                callMarketWriterAppend("append_market_source_entitlement", conflicting)
            }
        assertEquals("23505", conflict.sqlState)
        assertRolePermissionDenied(
            "decision_market_writer",
            MARKET_WRITER_PASSWORD,
            "update market_source_entitlements set source_id = source_id",
        )
        assertRolePermissionDenied(
            "decision_market_writer",
            MARKET_WRITER_PASSWORD,
            "delete from market_source_entitlements",
        )
        assertDecisionAppPermissionDenied("select * from market_source_entitlements")

        jdbcTemplate.update(
            """
            insert into cross_market_risk_snapshots (
              logical_identity_hash, owner_user_id, owner_scope_hash, config_version,
              availability, evidence_mode, snapshot_available_at, decision_authority,
              order_authority, validation_status, payload_hash, artifact_hash, payload_json
            ) values (
              repeat('1', 64), 'usr_demo_user', repeat('2', 64), 'cross-market-risk-config.v1',
              'AVAILABLE', 'SYNTHETIC_FIXTURE', '2026-07-31T00:15:00Z',
              'NEW_BUY_ALLOW_TO_WARN_ONLY', 'NONE', 'UNVALIDATED',
              repeat('3', 64), repeat('4', 64), '{"sanitized":true}'::jsonb
            )
            """.trimIndent(),
        )
        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { connection ->
            connection.autoCommit = false
            connection.createStatement().use { statement ->
                statement.execute("select set_config('app.actor_user_id', 'usr_demo_user', true)")
                statement.executeQuery("select count(*) from latest_cross_market_risk_snapshots").use { result ->
                    assertTrue(result.next())
                    assertEquals(1L, result.getLong(1))
                }
                statement.execute("select set_config('app.actor_user_id', 'usr_demo_admin', true)")
                statement.executeQuery("select count(*) from latest_cross_market_risk_snapshots").use { result ->
                    assertTrue(result.next())
                    assertEquals(0L, result.getLong(1))
                }
            }
            connection.rollback()
        }
    }

    @Test
    fun `V23 accepts contract-shaped payloads and blocks unknown keys at every append boundary`() {
        val mapper = JsonMapper.builder().build()
        val entitlementDocument = crossMarketFixture(mapper, "market_source_entitlement.v1.valid.json")
        val entitlement =
            requireNotNull(entitlementDocument.path("entitlements").get(0)) {
                "market source entitlement fixture must include one candidate-disabled entry"
            }.deepCopy() as ObjectNode
        assertEquals(
            "INSERTED",
            callMarketWriterAppend("append_market_source_entitlement", mapper.writeValueAsString(entitlement)),
        )

        // 관측 fixture의 sourceRef는 foreign key이므로 동일한 계약 형태의 disabled source를 먼저 둔다.
        val observationSourceEntitlement = entitlement.deepCopy() as ObjectNode
        observationSourceEntitlement.put("logicalIdentityHash", "4".repeat(64))
        observationSourceEntitlement.put("sourceId", "KIS_DISABLED_02")
        assertEquals(
            "INSERTED",
            callMarketWriterAppend(
                "append_market_source_entitlement",
                mapper.writeValueAsString(observationSourceEntitlement),
            ),
        )

        val cause = crossMarketFixture(mapper, "market_cause_evidence.v1.valid.json")
        // GDELT aggregate는 원인 확정 권한이 없으므로 V23이 허용하는 비인과 관계만 regression control로 쓴다.
        cause.put("relation", "CO_MOVES_WITH")
        val canonicalPayloads =
            listOf(
                MarketWriterAppendPayload("append_market_source_entitlement", entitlement, "REPLAY"),
                MarketWriterAppendPayload(
                    "append_cross_market_exposure_catalog_entry",
                    crossMarketFixture(mapper, "cross_market_exposure_catalog.v1.valid.json"),
                    "INSERTED",
                ),
                MarketWriterAppendPayload(
                    "append_cross_market_observation",
                    crossMarketFixture(mapper, "cross_market_observation.v1.valid.json"),
                    "INSERTED",
                ),
                MarketWriterAppendPayload(
                    "append_analyst_revision_evidence",
                    crossMarketFixture(mapper, "analyst_revision_evidence.v1.valid.json"),
                    "INSERTED",
                ),
                MarketWriterAppendPayload("append_market_cause_evidence", cause, "INSERTED"),
            )

        canonicalPayloads.forEach { candidate ->
            assertEquals(
                candidate.expectedResult,
                callMarketWriterAppend(candidate.functionName, mapper.writeValueAsString(candidate.payload)),
            )
            // 기존 denylist에 없는 key도 전체 p_record가 payload_json으로 흐르기 전에 거부해야 한다.
            assertUnknownCrossMarketPayloadIsRejected(mapper, candidate.functionName, candidate.payload)
        }

        assertUnknownNestedCrossMarketPayloadIsRejected(
            mapper,
            "append_market_source_entitlement",
            entitlement,
            "materializationDeclaration",
        )
        assertUnknownNestedCrossMarketPayloadIsRejected(
            mapper,
            "append_analyst_revision_evidence",
            canonicalPayloads.single { it.functionName == "append_analyst_revision_evidence" }.payload,
            "current",
        )
        assertObjectAtAllowedScalarIsRejected(
            mapper,
            "append_market_cause_evidence",
            cause,
            "sanitizedSummary",
        )
        assertObjectInsideAllowedArrayIsRejected(
            mapper,
            "append_cross_market_exposure_catalog_entry",
            canonicalPayloads.single { it.functionName == "append_cross_market_exposure_catalog_entry" }.payload,
            "sourceLineage",
        )
    }

    @Test
    fun `V10 seeds one safe singleton and exposes only the sanitized user projection`() {
        val state =
            jdbcTemplate.queryForMap(
                """
                select active, reason_class, generation, changed_by, changed_by_role, request_id
                from risk_kill_switch
                """.trimIndent(),
            )
        assertEquals(false, state["active"])
        assertEquals("INITIAL_STATE", state["reason_class"])
        assertEquals(1L, state["generation"])
        assertEquals(null, state["changed_by"])
        assertEquals("SYSTEM", state["changed_by_role"])
        assertEquals(null, state["request_id"])
        assertEquals(
            listOf("active", "reason_class", "changed_at"),
            queryStrings(
                """
                select column_name
                from information_schema.columns
                where table_schema = 'public' and table_name = 'kill_switch_user_projection'
                order by ordinal_position
                """.trimIndent(),
            ),
        )
    }

    @Test
    fun `V10 indexes the global unused decision invalidation scan`() {
        assertTrue(indexExists("decisions_valid_until_invalidation_idx"))
        assertTrue(
            indexDefinitionLike(
                "decisions",
                "%(valid_until, decision_id)%",
            ),
        )
    }

    @Test
    fun `decision application role receives only V15 brokerage capabilities`() {
        assertTrue(hasTablePrivilege("decision_app", "risk_kill_switch", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "risk_kill_switch", "INSERT"))
        assertFalse(hasTablePrivilege("decision_app", "risk_kill_switch", "DELETE"))
        assertFalse(hasTablePrivilege("decision_app", "risk_kill_switch", "TRUNCATE"))
        assertTrue(hasTablePrivilege("decision_app", "risk_kill_switch_transitions", "INSERT"))
        assertTrue(hasTablePrivilege("decision_app", "kill_switch_user_projection", "SELECT"))
        listOf("orders", "order_events", "mock_order_owner_projection", "brokerage_db_capability_keys").forEach { table ->
            listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(hasTablePrivilege("decision_app", table, privilege), "unexpected $privilege on $table")
            }
        }
        listOf("paper_accounts", "paper_positions", "paper_order_events").forEach { table ->
            listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(hasTablePrivilege("decision_app", table, privilege), "unexpected $privilege on $table")
            }
        }
        assertTrue(hasTablePrivilege("decision_app", "paper_margin_owner_projection", "SELECT"))
        listOf("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
            assertFalse(
                hasTablePrivilege("decision_app", "decision_invalidations", privilege),
                "unexpected $privilege on decision_invalidations",
            )
        }
        listOf("UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
            assertFalse(hasTablePrivilege("decision_app", "orders", privilege), "unexpected $privilege on orders")
            assertFalse(hasTablePrivilege("decision_app", "order_events", privilege), "unexpected $privilege on order_events")
        }
        listOf("SELECT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
            assertFalse(
                hasTablePrivilege("decision_app", "risk_kill_switch_transitions", privilege),
                "unexpected $privilege on risk_kill_switch_transitions",
            )
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
            "record_mock_order_provider_outcome(jsonb,text)",
            "read_paper_order_context(text,text,text)",
            "find_paper_order_idempotency_result(text,text,timestamp with time zone,text)",
            "read_paper_balance_projection(text,text,text)",
            "create_paper_order(jsonb,text)",
        ).forEach { function ->
            assertTrue(hasFunctionPrivilege("decision_app", function), "missing EXECUTE on $function")
        }
        assertFalse(
            hasFunctionPrivilege("decision_app", "assert_brokerage_database_capability(text)"),
            "runtime role must not call the private capability verifier directly",
        )
        assertFalse(
            hasFunctionPrivilege("decision_app", "rebuild_paper_state(text,text)"),
            "runtime role must not call the paper ledger rebuild verifier",
        )
        assertDecisionAppPermissionDenied("select * from orders")
        assertDecisionAppPermissionDenied("insert into orders default values")
        assertDecisionAppPermissionDenied("select * from order_events")
        assertDecisionAppPermissionDenied("insert into order_events default values")
        assertDecisionAppPermissionDenied("select * from mock_order_owner_projection")
        assertDecisionAppPermissionDenied("select * from paper_accounts")
        assertDecisionAppPermissionDenied("insert into paper_order_events default values")
        assertDecisionAppPermissionDenied("update paper_order_events set event_seq = event_seq")
        assertDecisionAppPermissionDenied("delete from paper_order_events")
        assertDecisionAppPermissionDenied("truncate table paper_order_events")
        assertDecisionAppPermissionDenied(
            "select * from read_paper_order_context('usr_demo_user', 'dec_${"0".repeat(32)}', '${"0".repeat(64)}')",
        )
    }

    @Test
    fun `V13 precondition rejects a preexisting paper ledger row and rolls back`() {
        val migrationUrl = createDatabase("v13_precondition_paper_row")
        flyway(migrationUrl, target = "12").migrate()
        DriverManager.getConnection(migrationUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.execute(
                    """
                    insert into paper_accounts (
                      account_id, user_id, name, cash_balance, currency, status
                    ) values (
                      'acct_00000000000000000000000000000013',
                      'usr_demo_user', 'V13 precondition fixture', 1, 'KRW', 'ACTIVE'
                    )
                    """.trimIndent(),
                )
            }
        }

        val failure = assertThrows<FlywayException> { flyway(migrationUrl).migrate() }

        assertTrue(failure.stackTraceToString().contains("S3.2 V13 precondition failed"))
        DriverManager.getConnection(migrationUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement
                    .executeQuery("select count(*) from flyway_schema_history where version = '13'")
                    .use { result ->
                        assertTrue(result.next())
                        assertEquals(0, result.getInt(1))
                    }
                statement
                    .executeQuery("select count(*) from paper_accounts")
                    .use { result ->
                        assertTrue(result.next())
                        assertEquals(1, result.getInt(1))
                    }
            }
        }
    }

    @Test
    fun `V13 mode prefix와 paper ledger checks reject cross wired and mutable rows`() {
        insertOrderFixture()
        jdbcTemplate.update(
            """
            insert into orders (
              order_id, user_id, account_id, account_scope_hash, decision_id,
              decision_evaluation_id, brokerage_mode, idempotency_scope_hash,
              idempotency_owner_scope_hash, request_hash, symbol, side, order_type,
              quantity, submitted_price_krw, status, order_intent_json,
              result_canonical_json, acknowledged_by, acknowledged_at, submitted_at
            ) values (
              'ord_mock_00000000000000000000000000000013', 'usr-flyway',
              'acct_00000000000000000000000000000013', repeat('1', 64),
              'dec-flyway', 'eval-flyway', 'KIS_MOCK', repeat('2', 64),
              repeat('3', 64), repeat('4', 64), '005930', 'BUY', 'MARKET',
              1, null, 'SUBMITTED',
              '{"symbol":"005930","side":"BUY","orderType":"MARKET","quantity":"1","estimatedPrice":"10000","estimatedAmount":"10000","timeframe":"1d","strategyId":"v13-check"}'::jsonb,
              '{"orderId":"ord_mock_00000000000000000000000000000013","accountId":"acct_00000000000000000000000000000013","brokerageMode":"KIS_MOCK","status":"SUBMITTED","submittedAt":"2030-01-02T03:04:05Z"}',
              'usr-flyway', now(), now()
            )
            """.trimIndent(),
        )
        assertCheckViolation {
            jdbcTemplate.update(
                "update orders set brokerage_mode = 'INTERNAL_PAPER' where decision_id = 'dec-flyway'",
            )
        }
        assertCheckViolation {
            jdbcTemplate.update(
                "update orders set order_id = 'ord_paper_00000000000000000000000000000013' where decision_id = 'dec-flyway'",
            )
        }
        assertCheckViolation {
            jdbcTemplate.update("update orders set brokerage_mode = 'KIS_LIVE' where decision_id = 'dec-flyway'")
        }
        assertCheckViolation {
            jdbcTemplate.update(
                "update orders set order_id = 'ord_mock_A0000000000000000000000000000013' where decision_id = 'dec-flyway'",
            )
        }

        jdbcTemplate.update(
            """
            insert into paper_accounts (
              account_id, user_id, name, cash_balance, currency, status,
              owner_scope_hash, margin_requirement_krw
            ) values (
              'acct_00000000000000000000000000000013', 'usr-flyway',
              'V13 ledger fixture', 100000, 'KRW', 'ACTIVE', repeat('5', 64), 0
            )
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into paper_positions (
              position_id, account_id, symbol, quantity, average_price, market_value
            ) values (
              'ppos_00000000000000000000000000000013',
              'acct_00000000000000000000000000000013',
              '005930', 1, 10000, 10000
            )
            """.trimIndent(),
        )
        assertCheckViolation {
            jdbcTemplate.update(
                "update paper_accounts set cash_balance = -1 where account_id = 'acct_00000000000000000000000000000013'",
            )
        }
        assertCheckViolation {
            jdbcTemplate.update(
                "update paper_positions set quantity = -1 where position_id = 'ppos_00000000000000000000000000000013'",
            )
        }

        jdbcTemplate.update(
            """
            update orders
            set order_id = 'ord_paper_00000000000000000000000000000013',
                brokerage_mode = 'INTERNAL_PAPER',
                status = 'FILLED',
                filled_quantity = quantity,
                leaves_quantity = 0,
                unfilled_terminated_quantity = 0,
                average_fill_price_krw = 10000,
                result_canonical_json =
                  '{"orderId":"ord_paper_00000000000000000000000000000013","accountId":"acct_00000000000000000000000000000013","brokerageMode":"INTERNAL_PAPER","status":"FILLED","submittedAt":"2030-01-02T03:04:05Z","fill":null}'
            where decision_id = 'dec-flyway'
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into paper_order_events (
              paper_order_event_id, account_id, order_id, event_type,
              payload_json, event_seq
            ) values (
              'pev_00000000000000000000000000000013',
              'acct_00000000000000000000000000000013',
              'ord_paper_00000000000000000000000000000013',
              'PAPER_ORDER_FILLED',
              jsonb_build_object(
                'orderId', 'ord_paper_00000000000000000000000000000013',
                'symbol', '005930', 'side', 'BUY',
                'fillQuantity', 1, 'fillPriceKrw', 10000, 'fillAmountKrw', 10000,
                'priceBasis', 'LAST_QUOTE', 'slippageBps', 5, 'feeModel', 'NONE_V1',
                'observedAt', '2030-01-02T03:04:05Z',
                'beforeCashKrw', 110000, 'afterCashKrw', 100000,
                'beforeQuantity', 0, 'afterQuantity', 1,
                'beforeAveragePriceKrw', 0, 'afterAveragePriceKrw', 10000,
                'beforeMarketValueKrw', 0, 'afterMarketValueKrw', 10000
              ),
              1
            )
            """.trimIndent(),
        )
        assertCheckViolation {
            jdbcTemplate.update(
                "update paper_order_events set event_seq = 0 where paper_order_event_id = 'pev_00000000000000000000000000000013'",
            )
        }
        assertCheckViolation {
            jdbcTemplate.update(
                """
                update paper_order_events
                set payload_json = payload_json - 'feeModel'
                where paper_order_event_id = 'pev_00000000000000000000000000000013'
                """.trimIndent(),
            )
        }
        assertUniqueViolation {
            jdbcTemplate.update(
                """
                insert into paper_order_events (
                  paper_order_event_id, account_id, order_id, event_type,
                  payload_json, event_seq
                )
                select
                  'pev_00000000000000000000000000000014',
                  account_id, order_id, event_type, payload_json, 2
                from paper_order_events
                where paper_order_event_id = 'pev_00000000000000000000000000000013'
                """.trimIndent(),
            )
        }
    }

    @Test
    fun `V10 precondition rejects a conflicting Kill Switch object without changing V9 state`() {
        val migrationUrl = createDatabase("v10_precondition_conflict")
        flyway(migrationUrl, target = "9").migrate()
        DriverManager.getConnection(migrationUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.execute("create table risk_kill_switch (fixture_id integer primary key)")
            }
        }

        val failure = assertThrows<FlywayException> { flyway(migrationUrl).migrate() }

        assertTrue(requireNotNull(failure.message).contains("V10 precondition failed"))
        DriverManager.getConnection(migrationUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.executeQuery("select count(*) from flyway_schema_history where success").use { result ->
                    assertTrue(result.next())
                    assertEquals(9, result.getInt(1))
                }
                assertTrue(
                    statement
                        .executeQuery("select to_regclass('public.decisions') is not null")
                        .use { result -> result.next() && result.getBoolean(1) },
                )
            }
        }
    }

    @Test
    fun `V12 order events use monotonic sequence and coupled type status constraints`() {
        assertEquals(
            listOf("event_seq"),
            queryStrings(
                """
                select column_name
                from information_schema.columns
                where table_schema = 'public'
                  and table_name = 'order_events'
                  and column_name = 'event_seq'
                  and is_nullable = 'NO'
                """.trimIndent(),
            ),
        )
        assertTrue(indexExists("order_events_order_sequence_unique"))
        val pairConstraint =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    """
                    select pg_get_constraintdef(oid)
                    from pg_constraint
                    where conname = 'order_events_type_status_pair_check'
                    """.trimIndent(),
                    String::class.java,
                ),
            )
        assertTrue(pairConstraint.contains("MOCK_ORDER_SUBMITTED"))
        assertTrue(pairConstraint.contains("SUBMITTED"))
        assertTrue(pairConstraint.contains("MOCK_ORDER_CANCEL_REQUESTED"))
        assertTrue(pairConstraint.contains("CANCEL_REQUESTED"))
        val projectionDefinition =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    "select pg_get_functiondef('read_mock_order_owner_projection(text,text,text)'::regprocedure)",
                    String::class.java,
                ),
            )
        assertTrue(projectionDefinition.contains("event.event_seq DESC"))
        assertFalse(projectionDefinition.contains("current_setting('app.actor_user_id'"))
    }

    @Test
    fun `V12 brokerage evidence contracts pin writer identity status values and live expiry clock`() {
        val auditConstraint =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    """
                    select pg_get_constraintdef(oid)
                    from pg_constraint
                    where conname = 'audit_logs_brokerage_order_contract_check'
                    """.trimIndent(),
                    String::class.java,
                ),
            )
        assertTrue(auditConstraint.contains("brokerageMode"))
        assertTrue(auditConstraint.contains("KIS_MOCK"))
        assertTrue(auditConstraint.contains("SUBMITTED"))
        assertTrue(auditConstraint.contains("CANCEL_REQUESTED"))

        val outboxConstraint =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    """
                    select pg_get_constraintdef(oid)
                    from pg_constraint
                    where conname = 'event_outbox_brokerage_order_contract_check'
                    """.trimIndent(),
                    String::class.java,
                ),
            )
        assertTrue(outboxConstraint.contains("brokerageMode"))
        assertTrue(outboxConstraint.contains("KIS_MOCK"))
        assertTrue(outboxConstraint.contains("SUBMITTED"))
        assertTrue(outboxConstraint.contains("CANCEL_REQUESTED"))

        val guardDefinition =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    "select pg_get_functiondef('enforce_brokerage_evidence_writer()'::regprocedure)",
                    String::class.java,
                ),
            ).lowercase()
        assertTrue(guardDefinition.contains("pg_has_role"))
        assertTrue(guardDefinition.contains("current_user"))
        assertTrue(guardDefinition.contains("flyway"))
        assertTrue(guardDefinition.contains("42501"))

        val triggerDefinitions =
            queryStrings(
                """
                select pg_get_triggerdef(oid)
                from pg_trigger
                where not tgisinternal
                  and tgname in (
                    'audit_logs_brokerage_writer_guard',
                    'event_outbox_brokerage_writer_guard'
                  )
                order by tgname
                """.trimIndent(),
            )
        assertEquals(2, triggerDefinitions.size)
        val triggerText = triggerDefinitions.joinToString("\n").lowercase()
        assertTrue(triggerText.contains("before insert"))
        assertTrue(triggerText.contains("audit_logs"))
        assertTrue(triggerText.contains("target_type = 'order'"))
        assertTrue(triggerText.contains("event_outbox"))
        assertTrue(triggerText.contains("brokerage.mock-order-submitted.v1"))
        assertTrue(triggerText.contains("brokerage.mock-order-cancel-requested.v1"))

        val createOrderDefinition =
            requireNotNull(
                jdbcTemplate.queryForObject(
                    "select pg_get_functiondef('create_mock_order(jsonb,text)'::regprocedure)",
                    String::class.java,
                ),
            )
        assertTrue(createOrderDefinition.contains("clock_timestamp()"))
        assertFalse(createOrderDefinition.contains("valid_until > requested_created_at"))
    }

    @Test
    fun `V10 singleton actor resume and audit constraints reject unsafe rows`() {
        assertCheckViolation {
            jdbcTemplate.update(
                """
                insert into risk_kill_switch (
                  kill_switch_id, active, reason_class, generation,
                  changed_by, changed_by_role, changed_at
                ) values (
                  'OTHER', true, 'USER_MANUAL_STOP', 2,
                  'usr_demo_user', 'USER', now()
                )
                """.trimIndent(),
            )
        }
        assertCheckViolation {
            jdbcTemplate.update(
                """
                update risk_kill_switch
                set active = false,
                    reason_class = 'ADMIN_RESUME',
                    changed_by = 'usr_demo_user',
                    changed_by_role = 'USER'
                where kill_switch_id = 'GLOBAL'
                """.trimIndent(),
            )
        }
        assertCheckViolation {
            jdbcTemplate.update(
                """
                update risk_kill_switch
                set active = true,
                    reason_class = 'OPERATOR_MANUAL_STOP',
                    changed_by = null,
                    changed_by_role = 'ADMIN'
                where kill_switch_id = 'GLOBAL'
                """.trimIndent(),
            )
        }

        listOf(
            "'KILL_SWITCH_CHANGED', jsonb_build_object(" +
                "'generation', 2, 'previousActive', false, 'nextActive', true, " +
                "'reasonClass', 'OPERATOR_MANUAL_STOP', 'changedBy', 'usr_demo_admin', " +
                "'changedByRole', 'ADMIN', 'correlationId', 'req-audit-missing')",
            "'KILL_SWITCH_CHANGED', jsonb_build_object(" +
                "'generation', 2, 'previousActive', false, 'nextActive', true, " +
                "'reasonClass', 'OPERATOR_MANUAL_STOP', 'changedBy', 'usr_demo_admin', " +
                "'changedByRole', 'ADMIN', 'correlationId', 'req-audit-extra', " +
                "'invalidatedDecisionCount', 0, 'rawReason', 'forbidden')",
            "'UNSAFE_ACTION', jsonb_build_object(" +
                "'generation', 2, 'previousActive', false, 'nextActive', true, " +
                "'reasonClass', 'OPERATOR_MANUAL_STOP', 'changedBy', 'usr_demo_admin', " +
                "'changedByRole', 'ADMIN', 'correlationId', 'req-audit-action', " +
                "'invalidatedDecisionCount', 0)",
        ).forEachIndexed { index, actionAndPayload ->
            assertCheckViolation {
                jdbcTemplate.update(
                    """
                    insert into audit_logs (
                      audit_log_id, user_id, actor_role, action, target_type,
                      target_id, request_id, payload_json, created_at
                    )
                    select
                      'aud-v10-denied-$index', 'usr_demo_admin', 'ADMIN',
                      unsafe.action, 'KILL_SWITCH', 'GLOBAL',
                      'req-audit-${listOf("missing", "extra", "action")[index]}',
                      unsafe.payload, now()
                    from (
                      select $actionAndPayload
                    ) as unsafe(action, payload)
                    """.trimIndent(),
                )
            }
        }
    }

    @Test
    fun `Kill Switch admin revalidation classifies current status role and security version`() {
        val original =
            jdbcTemplate.queryForMap(
                """
                select role, status, security_version
                from users
                where user_id = 'usr_demo_admin'
                """.trimIndent(),
            )
        val securityVersion = (original["security_version"] as Number).toLong()
        try {
            assertEquals("AUTHORIZED", revalidateKillSwitchAdmin(securityVersion))

            jdbcTemplate.update("update users set role = 'USER' where user_id = 'usr_demo_admin'")
            assertEquals("FORBIDDEN", revalidateKillSwitchAdmin(securityVersion))

            jdbcTemplate.update(
                "update users set role = 'ADMIN', status = 'DISABLED' where user_id = 'usr_demo_admin'",
            )
            assertEquals("UNAUTHORIZED", revalidateKillSwitchAdmin(securityVersion))

            jdbcTemplate.update(
                """
                update users
                set status = 'ACTIVE', security_version = security_version + 1
                where user_id = 'usr_demo_admin'
                """.trimIndent(),
            )
            assertEquals("UNAUTHORIZED", revalidateKillSwitchAdmin(securityVersion))
        } finally {
            jdbcTemplate.update(
                """
                update users
                set role = ?, status = ?, security_version = ?
                where user_id = 'usr_demo_admin'
                """.trimIndent(),
                original["role"],
                original["status"],
                original["security_version"],
            )
        }
    }

    @Test
    fun `global invalidation spans owners excludes consumed decisions and remains owner scoped`() {
        cleanupS24InvalidationFixtures()
        try {
            insertOrderFixture()
            insertSecondDecisionFixture()
            insertAdminDecisionFixture()
            jdbcTemplate.update(
                """
                update risk_kill_switch
                set active = true,
                    reason_class = 'OPERATOR_MANUAL_STOP',
                    generation = 7,
                    changed_by = 'usr_demo_admin',
                    changed_by_role = 'ADMIN',
                    changed_at = now(),
                    request_id = 'req-v10-global-invalidation'
                where kill_switch_id = 'GLOBAL'
                """.trimIndent(),
            )
            jdbcTemplate.update(
                """
                insert into orders (
                  order_id, user_id, account_id, account_scope_hash, decision_id,
                  decision_evaluation_id, brokerage_mode, idempotency_scope_hash,
                  idempotency_owner_scope_hash, request_hash, symbol, side, order_type,
                  quantity, submitted_price_krw, status, order_intent_json,
                  result_canonical_json, acknowledged_by, acknowledged_at, submitted_at
                ) values (
                  'ord_mock_0000000000000000000000000000000b', 'usr-flyway',
                  'acct_0000000000000000000000000000000b', repeat('b', 64),
                  'dec-flyway-b', 'eval-flyway-b', 'KIS_MOCK', repeat('1', 64),
                  repeat('2', 64), repeat('3', 64), '005930', 'BUY', 'MARKET',
                  1, null, 'SUBMITTED',
                  '{"symbol":"005930","side":"BUY","orderType":"MARKET","quantity":"1","estimatedPrice":"70000","estimatedAmount":"70000","timeframe":"1d","strategyId":"flyway-fixture"}'::jsonb,
                  '{"orderId":"ord_mock_0000000000000000000000000000000b","brokerageMode":"KIS_MOCK","status":"SUBMITTED","submittedAt":"2030-01-02T03:04:05Z"}',
                  'usr-flyway', now(), now()
                )
                """.trimIndent(),
            )

            DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { connection ->
                connection
                    .prepareStatement(
                        """
                        select invalidate_unused_decisions_for_kill_switch(
                          generation,
                          changed_at,
                          request_id
                        )
                        from risk_kill_switch
                        where kill_switch_id = 'GLOBAL'
                        """.trimIndent(),
                    ).use { statement ->
                        statement.executeQuery().use { result ->
                            assertTrue(result.next())
                            assertEquals(2, result.getInt(1))
                        }
                    }
                connection
                    .prepareStatement(
                        """
                        select invalidate_unused_decisions_for_kill_switch(
                          generation,
                          changed_at,
                          request_id
                        )
                        from risk_kill_switch
                        where kill_switch_id = 'GLOBAL'
                        """.trimIndent(),
                    ).use { statement ->
                        statement.executeQuery().use { result ->
                            assertTrue(result.next())
                            assertEquals(0, result.getInt(1))
                        }
                    }
            }

            assertEquals(1, countDecisionInvalidations("dec-flyway"))
            assertEquals(0, countDecisionInvalidations("dec-flyway-b"))
            assertEquals(1, countDecisionInvalidations("dec-v10-admin"))

            jdbcTemplate.execute("grant select on table decision_invalidations to decision_app")
            try {
                assertInvalidationOwnerScope("usr-flyway", 1)
                assertInvalidationOwnerScope("usr_demo_admin", 1)
                assertInvalidationOwnerScope("usr_demo_user", 0)
            } finally {
                jdbcTemplate.execute("revoke select on table decision_invalidations from decision_app")
            }
            assertDecisionUsability("usr-flyway", "dec-flyway", expectedRows = 1, invalidated = true, consumed = null)
            assertDecisionUsability(
                "usr-flyway",
                "dec-flyway-b",
                expectedRows = 1,
                invalidated = false,
                consumed = "ord_mock_0000000000000000000000000000000b",
            )
            assertDecisionUsability(
                "usr-flyway",
                "dec-v10-admin",
                expectedRows = 0,
                invalidated = false,
                consumed = null,
            )
        } finally {
            jdbcTemplate.update(
                """
                update risk_kill_switch
                set active = false,
                    reason_class = 'INITIAL_STATE',
                    generation = 1,
                    changed_by = null,
                    changed_by_role = 'SYSTEM',
                    changed_at = now(),
                    request_id = null
                where kill_switch_id = 'GLOBAL'
                """.trimIndent(),
            )
            cleanupS24InvalidationFixtures()
        }
    }

    @Test
    fun `fresh V9 migration leaves every production source table empty`() {
        val migrationUrl = createDatabase("v9_source_seed_zero")
        flyway(migrationUrl).migrate()

        val sourceTables =
            listOf(
                "market_quote_observations",
                "instrument_catalog_observations",
                "portfolio_balance_observations",
                "portfolio_position_observations",
                "deterministic_risk_observations",
                "daily_order_count_observations",
                "corporation_registry_observations",
            )
        DriverManager.getConnection(migrationUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                sourceTables.forEach { tableName ->
                    statement.executeQuery("select count(*) from $tableName").use { result ->
                        assertTrue(result.next())
                        assertEquals(0, result.getInt(1), "$tableName must not receive production seed rows")
                    }
                }
            }
        }
    }

    @Test
    fun `V9 populated precondition failure preserves the complete V8 schema and row`() {
        val migrationUrl = createDatabase("v9_populated_precondition")
        flyway(migrationUrl, target = "8").migrate()
        DriverManager.getConnection(migrationUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.execute(
                    """
                    insert into principles (
                      principle_id, user_id, preset_id, title, mode, status, current_version
                    ) values (
                      'prn-v9-guard', 'usr_demo_user', 'balanced',
                      'V9 guard fixture', 'GUIDE', 'ACTIVE', 1
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into principle_versions (
                      principle_version_id, principle_id, version, preset_id, title,
                      mode, status, rules_json, changed_fields, created_by
                    )
                    select
                      'prv-v9-guard', 'prn-v9-guard', 1, preset_id, 'V9 guard fixture',
                      'GUIDE', 'ACTIVE', rules_json, array['title'], 'usr_demo_user'
                    from principle_presets
                    where preset_id = 'balanced'
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into decisions (
                      decision_id, user_id, account_id, principle_version_id,
                      symbol, side, decision, mode, reason_json,
                      signal_snapshot_json, created_at, valid_until
                    ) values (
                      'dec-v9-guard', 'usr_demo_user', 'sanitized-account-scope',
                      'prv-v9-guard', '005930', 'BUY', 'HOLD', 'GUIDE',
                      '{}'::jsonb, '{}'::jsonb, now(), now() + interval '10 minutes'
                    )
                    """.trimIndent(),
                )
            }
        }

        val failure = assertThrows<FlywayException> { flyway(migrationUrl).migrate() }
        assertTrue(failure.stackTraceToString().contains("S2.3 V9 precondition failed"))

        DriverManager.getConnection(migrationUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.executeQuery("select count(*) from decisions where decision_id = 'dec-v9-guard'").use {
                    assertTrue(it.next())
                    assertEquals(1, it.getInt(1))
                }
                statement
                    .executeQuery(
                        """
                        select
                          count(*) filter (where column_name = 'account_id'),
                          count(*) filter (where column_name = 'evaluation_id')
                        from information_schema.columns
                        where table_schema = 'public' and table_name = 'decisions'
                        """.trimIndent(),
                    ).use {
                        assertTrue(it.next())
                        assertEquals(1, it.getInt(1))
                        assertEquals(0, it.getInt(2))
                    }
                statement
                    .executeQuery(
                        "select version from flyway_schema_history where success order by installed_rank desc limit 1",
                    ).use {
                        assertTrue(it.next())
                        assertEquals("8", it.getString(1))
                    }
            }
        }
    }

    @Test
    fun `V7 seeds exact demo identities with attested separated credential bundles`() {
        val users =
            jdbcTemplate.query(
                """
                select user_id, username, role, status, security_version, password_hash
                from users
                where user_id in ('usr_demo_user', 'usr_demo_admin')
                order by user_id
                """.trimIndent(),
            ) { result, _ ->
                listOf(
                    result.getString("user_id"),
                    result.getString("username"),
                    result.getString("role"),
                    result.getString("status"),
                    result.getLong("security_version").toString(),
                    result.getString("password_hash"),
                )
            }
        assertEquals(listOf("usr_demo_admin", "demo-admin", "ADMIN", "ACTIVE", "1"), users[0].take(5))
        assertEquals(listOf("usr_demo_user", "demo-user", "USER", "ACTIVE", "1"), users[1].take(5))
        assertTrue(users.all { Regex("^\\$2[aby]\\$12\\$[./A-Za-z0-9]{53}$").matches(it.last()) })

        val evidence =
            jdbcTemplate.query(
                """
                select user_id,
                       octet_length(credential_reuse_tag),
                       octet_length(credential_bundle_mac),
                       credential_policy_version,
                       encode(credential_reuse_tag, 'hex')
                from users
                where user_id in ('usr_demo_user', 'usr_demo_admin')
                order by user_id
                """.trimIndent(),
            ) { result, _ ->
                listOf(
                    result.getString("user_id"),
                    result.getInt(2).toString(),
                    result.getInt(3).toString(),
                    result.getInt(4).toString(),
                    result.getString(5),
                )
            }
        assertTrue(evidence.all { it.subList(1, 4) == listOf("32", "32", "1") })
        assertNotEquals(evidence[0].last(), evidence[1].last())

        val sharedPlaintextAdminBundle =
            SpringApiIntegrationTestBase.prepareTestBundle("usr_demo_admin", TEST_USER_PASSWORD)
        assertThrows<IllegalArgumentException> {
            s21ActorTrustMigration(adminBundle = sharedPlaintextAdminBundle)
        }
        assertThrows<IllegalArgumentException> {
            s21ActorTrustMigration(userBundle = "not-a-credential-bundle")
        }
    }

    @Test
    fun `V7 permits statement logging when credential bind values are suppressed`() {
        val migrationUrl = createDatabase("migration_logging_safe")
        flyway(migrationUrl, target = "6").migrate()
        setMigrationLoggingPolicy(logStatement = "all", parameterMaxLength = 0, errorParameterMaxLength = 0)
        try {
            val logOffset = postgres.logs.length

            flyway(migrationUrl).migrate()

            val migrationLogs = postgres.logs.drop(logOffset)
            assertTrue(migrationLogs.contains("insert into users"), "statement logging did not observe V7 seed SQL")
            assertCredentialEvidenceAbsent(migrationLogs)
        } finally {
            setMigrationLoggingPolicy(logStatement = "none", parameterMaxLength = 0, errorParameterMaxLength = 0)
        }
    }

    @Test
    fun `V7 fails closed before credential binds when parameter logging is unsafe`() {
        val migrationUrl = createDatabase("migration_logging_unsafe")
        flyway(migrationUrl, target = "6").migrate()
        setMigrationLoggingPolicy(logStatement = "all", parameterMaxLength = -1, errorParameterMaxLength = -1)
        try {
            val logOffset = postgres.logs.length

            assertThrows<FlywayException> { flyway(migrationUrl).migrate() }

            assertCredentialEvidenceAbsent(postgres.logs.drop(logOffset))
            assertV7RolledBack(migrationUrl)
        } finally {
            setMigrationLoggingPolicy(logStatement = "none", parameterMaxLength = 0, errorParameterMaxLength = 0)
        }
    }

    @Test
    fun `V7 upgrade preserves unrelated users and rolls back identity conflicts without exposing hashes`() {
        val preservedUrl = createDatabase("existing_auth_user")
        flyway(preservedUrl, target = "6").migrate()
        DriverManager.getConnection(preservedUrl, postgres.username, postgres.password).use { connection ->
            connection
                .prepareStatement(
                    "insert into users (user_id, username, role, password_hash) values ('usr-existing', 'existing-user', 'USER', ?)",
                ).use { statement ->
                    statement.setString(1, TEST_USER_PASSWORD_HASH)
                    assertEquals(1, statement.executeUpdate())
                }
        }
        flyway(preservedUrl).migrate()
        DriverManager.getConnection(preservedUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement.executeQuery("select count(*) from users where user_id = 'usr-existing'").use { result ->
                    assertTrue(result.next())
                    assertEquals(1, result.getInt(1))
                }
            }
        }

        val conflictUrl = createDatabase("conflicting_auth_identity")
        flyway(conflictUrl, target = "6").migrate()
        DriverManager.getConnection(conflictUrl, postgres.username, postgres.password).use { connection ->
            connection
                .prepareStatement(
                    "insert into users (user_id, username, role, password_hash) values ('usr_demo_user', 'conflicting-user', 'USER', ?)",
                ).use { statement ->
                    statement.setString(1, TEST_USER_PASSWORD_HASH)
                    assertEquals(1, statement.executeUpdate())
                }
        }

        val failure = assertThrows<FlywayException> { flyway(conflictUrl).migrate() }
        val failureText = failure.stackTraceToString()
        assertFalse(failureText.contains(TEST_USER_PASSWORD_HASH))
        assertFalse(
            failureText.contains(
                Base64.getEncoder().encodeToString(TEST_USER_PASSWORD_HASH.toByteArray()),
            ),
        )
        DriverManager.getConnection(conflictUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement
                    .executeQuery(
                        "select count(*) from information_schema.columns " +
                            "where table_schema = 'public' and table_name = 'users' " +
                            "and column_name in ('security_version', 'credential_reuse_tag', " +
                            "'credential_bundle_mac', 'credential_policy_version')",
                    ).use { result ->
                        assertTrue(result.next())
                        assertEquals(0, result.getInt(1))
                    }
                statement.executeQuery("select count(*) from flyway_schema_history where version = '7'").use { result ->
                    assertTrue(result.next())
                    assertEquals(0, result.getInt(1))
                }
            }
        }
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("remainingTrustRootConflicts")
    fun `V7 rejects every remaining demo identity conflict shape`(
        caseName: String,
        databaseName: String,
        userId: String,
        username: String,
        role: String,
        status: String,
        passwordHash: String,
    ) {
        val conflictUrl = createDatabase(databaseName)
        flyway(conflictUrl, target = "6").migrate()
        DriverManager.getConnection(conflictUrl, postgres.username, postgres.password).use { connection ->
            connection
                .prepareStatement(
                    """
                    insert into users (user_id, username, role, password_hash, status)
                    values (?, ?, ?, ?, ?)
                    """.trimIndent(),
                ).use { statement ->
                    statement.setString(1, userId)
                    statement.setString(2, username)
                    statement.setString(3, role)
                    statement.setString(4, passwordHash)
                    statement.setString(5, status)
                    assertEquals(1, statement.executeUpdate(), caseName)
                }
        }

        assertThrows<FlywayException> { flyway(conflictUrl).migrate() }
        assertV7RolledBack(conflictUrl)
    }

    @Test
    fun `calendar runtime roles receive exact allowlisted privileges`() {
        assertTrue(hasTablePrivilege("decision_collector", "opendart_quota_usage", "SELECT"))
        assertTrue(hasTablePrivilege("decision_collector", "opendart_quota_usage", "INSERT"))
        assertTrue(hasTablePrivilege("decision_collector", "opendart_quota_usage", "UPDATE"))
        assertTrue(hasTablePrivilege("decision_collector", "calendar_observations", "INSERT"))
        assertFalse(hasTablePrivilege("decision_collector", "calendar_observations", "UPDATE"))
        assertFalse(hasTablePrivilege("decision_collector", "calendar_observations", "DELETE"))
        assertTrue(hasTablePrivilege("decision_collector", "trading_session_revisions", "INSERT"))
        assertFalse(hasTablePrivilege("decision_collector", "trading_session_revisions", "UPDATE"))
        assertFalse(hasTablePrivilege("decision_collector", "users", "SELECT"))
        assertFalse(hasTablePrivilege("decision_collector", "flyway_schema_history", "SELECT"))
        assertFalse(hasSchemaPrivilege("decision_collector", "CREATE"))

        assertTrue(hasTablePrivilege("decision_app", "trading_sessions", "SELECT"))
        assertTrue(hasTablePrivilege("decision_app", "current_calendar_events", "SELECT"))
        assertTrue(hasTablePrivilege("decision_app", "active_disclosure_risk_states", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "calendar_observations", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "trading_session_revisions", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "opendart_quota_usage", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "trading_sessions", "INSERT"))
        assertFalse(hasTablePrivilege("decision_app", "flyway_schema_history", "SELECT"))
        assertFalse(hasSchemaPrivilege("decision_app", "CREATE"))
    }

    @Test
    fun `decision application role has exact append only V9 privileges`() {
        listOf(
            "decisions",
            "decision_violations",
            "decision_artifacts",
            "decision_traces",
            "audit_logs",
            "event_outbox",
            "decision_idempotency_results",
        ).forEach { table ->
            assertTrue(hasTablePrivilege("decision_app", table, "INSERT"), "missing INSERT on $table")
        }
        listOf(
            "decision_owner_projection",
            "decision_audit_projection",
            "latest_market_quote_observations",
            "latest_portfolio_balance_observations",
            "active_paper_portfolio_projection",
            "latest_instrument_catalog_observations",
            "latest_deterministic_risk_observations",
            "latest_daily_order_count_observations",
        ).forEach { table ->
            assertTrue(hasTablePrivilege("decision_app", table, "SELECT"), "missing SELECT on $table")
        }
        listOf(
            "decisions",
            "decision_violations",
            "decision_artifacts",
            "decision_traces",
            "audit_logs",
            "event_outbox",
        ).forEach { table ->
            assertFalse(hasTablePrivilege("decision_app", table, "SELECT"), "unexpected SELECT on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "UPDATE"), "unexpected UPDATE on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "DELETE"), "unexpected DELETE on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "TRUNCATE"), "unexpected TRUNCATE on $table")
        }
        listOf(
            "market_quote_observations",
            "portfolio_balance_observations",
            "portfolio_position_observations",
            "instrument_catalog_observations",
            "deterministic_risk_observations",
            "daily_order_count_observations",
            "corporation_registry_observations",
            "current_corporation_registry_projection",
            "disclosure_event_observation_projection",
            "disclosure_collection_status_projection",
        ).forEach { table ->
            assertFalse(hasTablePrivilege("decision_app", table, "SELECT"), "unexpected source SELECT on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "INSERT"), "unexpected source INSERT on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "UPDATE"), "unexpected source UPDATE on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "DELETE"), "unexpected source DELETE on $table")
            assertFalse(hasTablePrivilege("decision_app", table, "TRUNCATE"), "unexpected source TRUNCATE on $table")
        }
        assertFalse(hasTablePrivilege("decision_app", "rag_answers_v2_legacy", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "decision_idempotency_results", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "flyway_schema_history", "SELECT"))
        assertFalse(hasSchemaPrivilege("decision_app", "CREATE"))

        listOf(
            "current_corporation_registry_projection",
            "disclosure_event_observation_projection",
            "disclosure_collection_status_projection",
        ).forEach { table ->
            assertTrue(hasTablePrivilege("decision_disclosure_reader", table, "SELECT"), "missing reader SELECT on $table")
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
                    hasTablePrivilege("decision_disclosure_reader", table, privilege),
                    "unexpected disclosure reader $privilege on $table",
                )
            }
        }
        assertFalse(hasSchemaPrivilege("decision_disclosure_reader", "CREATE"))
        assertRolePermissionDenied(
            "decision_disclosure_reader",
            DISCLOSURE_READER_PASSWORD,
            "insert into decisions (decision_id) values ('reader-forbidden')",
        )
        assertRolePermissionDenied(
            "decision_disclosure_reader",
            DISCLOSURE_READER_PASSWORD,
            "select * from flyway_schema_history",
        )

        val roleFlags =
            jdbcTemplate.queryForMap(
                """
                select rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls
                from pg_roles
                where rolname = 'decision_app'
                """.trimIndent(),
            )
        assertTrue(roleFlags.values.all { it == false })
    }

    @Test
    fun `decision application role receives SQLSTATE 42501 for forbidden history source and schema operations`() {
        listOf(
            "update decisions set outcome = outcome where false",
            "delete from decisions where false",
            "truncate table decisions",
            "update audit_logs set action = action where false",
            "delete from audit_logs where false",
            "truncate table audit_logs",
            "update event_outbox set status = status where false",
            "delete from event_outbox where false",
            "truncate table event_outbox",
            "insert into market_quote_observations " +
                "(observation_id, symbol, source, price_krw, completeness, observed_at, received_at, " +
                "schema_version, source_version, payload_json, source_ref, artifact_hash) values " +
                "('forbidden', '005930', 'KIS_MOCK', 1, 'COMPLETE', now(), now(), 'v1', 'v1', " +
                "'{}'::jsonb, repeat('a', 64), repeat('b', 64))",
            "select * from rag_answers_v2_legacy limit 0",
            "select * from decisions limit 0",
            "select * from audit_logs limit 0",
            "select * from event_outbox limit 0",
            "select * from decision_idempotency_results limit 0",
            "update flyway_schema_history set success = success where false",
            "create table s23_forbidden_schema_write (id integer)",
        ).forEach(::assertDecisionAppPermissionDenied)
    }

    @Test
    fun `V9 owner views use invoker mode while bounded functions keep base tables denied`() {
        listOf("decision_owner_projection", "decision_audit_projection").forEach { view ->
            val options =
                jdbcTemplate.queryForObject(
                    "select coalesce(array_to_string(reloptions, ','), '') from pg_class where oid = ?::regclass",
                    String::class.java,
                    view,
                ) ?: ""
            assertTrue(options.contains("security_invoker=true"), "$view must be security_invoker")
        }
        assertTrue(hasFunctionPrivilege("decision_app", "find_decision_idempotency_result(text,text,timestamp with time zone)"))
        assertFalse(hasTablePrivilege("decision_app", "decision_idempotency_results", "SELECT"))

        val probe = "s23_future_acl_probe"
        jdbcTemplate.execute("drop table if exists $probe")
        try {
            DriverManager.getConnection(postgres.jdbcUrl, "flyway", FLYWAY_PASSWORD).use { connection ->
                connection.createStatement().use { statement ->
                    statement.execute("create table $probe (id integer)")
                }
            }
            assertDecisionAppPermissionDenied("select * from $probe")
        } finally {
            jdbcTemplate.execute("drop table if exists $probe")
        }
    }

    @Test
    fun `source writer roles can append only their own bounded observations`() {
        assertWriterInsert(
            "decision_market_writer",
            MARKET_WRITER_PASSWORD,
            """
            insert into instrument_catalog_observations (
              observation_id, symbol, is_etf_etn, is_gold_etf_etn, product_risk_score,
              catalog_version, observed_at, received_at, completeness, schema_version,
              source_version, payload_json, source_ref, artifact_hash
            ) values (
              'ins-writer-role', 'WRITER01', true, false, 0.25, 'catalog-writer-v1',
              '2031-01-01T00:00:00Z', '2031-01-01T00:00:01Z', 'COMPLETE',
              'instrument-catalog-observation.v1', 'fixture-v1',
              '{"symbol":"WRITER01"}'::jsonb, repeat('1', 64), repeat('2', 64)
            )
            """.trimIndent(),
        )
        assertWriterInsert(
            "decision_market_writer",
            MARKET_WRITER_PASSWORD,
            """
            insert into market_quote_observations (
              observation_id, symbol, source, price_krw, bid_krw, ask_krw,
              observed_at, received_at, completeness, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) values (
              'quote-writer-role', 'WRITER02', 'KIS_MOCK', 1000, 990, 1010,
              '2031-01-01T00:00:00Z', '2031-01-01T00:00:01Z', 'COMPLETE',
              'market-quote-observation.v1', 'fixture-v1',
              '{"symbol":"WRITER02"}'::jsonb, repeat('b', 64), repeat('c', 64)
            )
            """.trimIndent(),
        )
        assertWriterInsert(
            "decision_risk_writer",
            RISK_WRITER_PASSWORD,
            """
            insert into deterministic_risk_observations (
              observation_id, owner_user_id, owner_scope_hash, portfolio_source,
              daily_loss_rate, max_drawdown, annualized_volatility, completeness,
              observed_at, received_at, schema_version, source_version, payload_json,
              source_ref, artifact_hash
            ) values (
              'risk-writer-role', 'usr_demo_user', repeat('3', 64), 'KIS_MOCK',
              -0.01, -0.05, 0.20, 'COMPLETE',
              '2026-06-23T06:31:00Z', '2026-06-23T06:31:01Z',
              'deterministic-risk-observation.v1', 'fixture-v1',
              '{"ownerScopeHash":"sanitized"}'::jsonb, repeat('4', 64), repeat('5', 64)
            )
            """.trimIndent(),
        )
        assertWriterInsert(
            "decision_risk_writer",
            RISK_WRITER_PASSWORD,
            """
            insert into daily_order_count_observations (
              observation_id, owner_user_id, owner_scope_hash, portfolio_source,
              trading_date, order_count, covered_through, completeness,
              observed_at, received_at, schema_version, source_version, payload_json,
              source_ref, artifact_hash
            ) values (
              'orders-writer-role', 'usr_demo_user', repeat('d', 64), 'KIS_MOCK',
              '2031-01-01', 0, '2031-01-01T00:00:00Z', 'COMPLETE',
              '2031-01-01T00:00:00Z', '2031-01-01T00:00:01Z',
              'daily-order-count-observation.v1', 'fixture-v1', '{}'::jsonb,
              repeat('e', 64), repeat('f', 64)
            )
            """.trimIndent(),
        )
        assertWriterInsert(
            "decision_portfolio_writer",
            PORTFOLIO_WRITER_PASSWORD,
            """
            insert into portfolio_balance_observations (
              observation_id, owner_user_id, account_scope_hash, source, context_status,
              cash_krw, portfolio_equity_krw, margin_requirement_krw, completeness,
              position_count, observed_at, received_at, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) values (
              'balance-writer-role', 'usr_demo_admin', repeat('6', 64), 'KIS_MOCK', 'ACTIVE',
              1, 1, 0, 'COMPLETE', 1, '2031-01-01T00:00:00Z', '2031-01-01T00:00:01Z',
              'portfolio-balance-observation.v1', 'fixture-v1', '{}'::jsonb,
              repeat('7', 64), repeat('8', 64)
            )
            """.trimIndent(),
        )
        assertWriterInsert(
            "decision_portfolio_writer",
            PORTFOLIO_WRITER_PASSWORD,
            """
            insert into portfolio_position_observations (
              balance_observation_id, symbol, quantity, market_value_krw, is_gold_etf_etn
            ) values ('balance-writer-role', 'WRITER03', 1, 1, false)
            """.trimIndent(),
        )
        assertWriterInsert(
            "decision_collector",
            COLLECTOR_PASSWORD,
            """
            insert into corporation_registry_observations (
              observation_id, symbol, corp_code, registry_status, completeness,
              observed_at, received_at, schema_version, source_version, payload_json,
              source_ref, artifact_hash
            ) values (
              'corp-writer-role', '999998', '12345678', 'ACTIVE', 'COMPLETE',
              '2031-01-01T00:00:00Z', '2031-01-01T00:00:01Z',
              'corporation-registry-observation.v1', 'fixture-v1',
              '{"symbol":"999998"}'::jsonb, repeat('9', 64), repeat('a', 64)
            )
            """.trimIndent(),
        )

        assertRolePermissionDenied(
            "decision_market_writer",
            MARKET_WRITER_PASSWORD,
            "select * from instrument_catalog_observations",
        )
        assertRolePermissionDenied(
            "decision_market_writer",
            MARKET_WRITER_PASSWORD,
            "insert into deterministic_risk_observations " +
                "(observation_id, owner_user_id, owner_scope_hash, portfolio_source, completeness, " +
                "observed_at, received_at, schema_version, source_version, payload_json, source_ref, artifact_hash) " +
                "values ('forbidden-risk', 'usr_demo_user', repeat('b',64), 'KIS_MOCK', 'PARTIAL', " +
                "now(), now(), 'v1', 'v1', '{}'::jsonb, repeat('c',64), repeat('d',64))",
        )
        assertRolePermissionDenied(
            "decision_market_writer",
            MARKET_WRITER_PASSWORD,
            "create table forbidden_writer_ddl (id integer)",
        )
        val writerOwnedTables =
            listOf(
                arrayOf(
                    "decision_market_writer",
                    MARKET_WRITER_PASSWORD,
                    "market_quote_observations",
                    "observation_id = observation_id",
                ),
                arrayOf(
                    "decision_market_writer",
                    MARKET_WRITER_PASSWORD,
                    "instrument_catalog_observations",
                    "observation_id = observation_id",
                ),
                arrayOf(
                    "decision_portfolio_writer",
                    PORTFOLIO_WRITER_PASSWORD,
                    "portfolio_balance_observations",
                    "observation_id = observation_id",
                ),
                arrayOf(
                    "decision_portfolio_writer",
                    PORTFOLIO_WRITER_PASSWORD,
                    "portfolio_position_observations",
                    "symbol = symbol",
                ),
                arrayOf(
                    "decision_risk_writer",
                    RISK_WRITER_PASSWORD,
                    "deterministic_risk_observations",
                    "observation_id = observation_id",
                ),
                arrayOf(
                    "decision_risk_writer",
                    RISK_WRITER_PASSWORD,
                    "daily_order_count_observations",
                    "observation_id = observation_id",
                ),
                arrayOf(
                    "decision_collector",
                    COLLECTOR_PASSWORD,
                    "corporation_registry_observations",
                    "observation_id = observation_id",
                ),
            )
        writerOwnedTables.forEach { (role, password, ownedTable, noOpAssignment) ->
            assertTrue(hasTablePrivilege(role, ownedTable, "INSERT"), "missing writer INSERT on $ownedTable")
            listOf("SELECT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
                assertFalse(
                    hasTablePrivilege(role, ownedTable, privilege),
                    "unexpected writer $privilege on $ownedTable",
                )
            }
            assertRolePermissionDenied(role, password, "select * from $ownedTable")
            assertRolePermissionDenied(role, password, "update $ownedTable set $noOpAssignment")
            assertRolePermissionDenied(role, password, "delete from $ownedTable")
            assertRolePermissionDenied(role, password, "truncate table $ownedTable")
        }
        writerOwnedTables
            .map { it[0] to it[1] }
            .distinct()
            .forEach { (role, password) ->
                assertRolePermissionDenied(role, password, "insert into decisions (decision_id) values ('forbidden')")
                assertRolePermissionDenied(role, password, "create table forbidden_${role}_ddl (id integer)")
            }
    }

    @Test
    fun `stored quote and KIS mock balance are owner scoped exact observations outside the persistence transaction`() {
        val observedAt = "2031-02-03T04:05:06Z"
        jdbcTemplate.update(
            """
            insert into market_quote_observations (
              observation_id, symbol, source, price_krw, bid_krw, ask_krw,
              observed_at, received_at,
              completeness, schema_version, source_version, payload_json, source_ref, artifact_hash
            )
            values (
              'obs-quote-s23', '005930', 'KIS_MOCK', 70000, 69900, 70000,
              ?::timestamptz, ?::timestamptz,
              'COMPLETE', 'market-quote-observation.v1', 'kis-mock-fixture-v1',
              '{"symbol":"005930"}'::jsonb, repeat('a', 64), repeat('b', 64)
            )
            """.trimIndent(),
            observedAt,
            observedAt,
        )
        jdbcTemplate.update(
            """
            insert into portfolio_balance_observations (
              observation_id, owner_user_id, account_scope_hash, source, context_status,
              cash_krw, portfolio_equity_krw, margin_requirement_krw, completeness, position_count,
              observed_at, received_at, schema_version, source_version, payload_json,
              source_ref, artifact_hash
            )
            values (
              'obs-balance-s23', 'usr_demo_user', repeat('c', 64), 'KIS_MOCK', 'ACTIVE',
              500000, 1000000, 140000, 'COMPLETE', 1,
              ?::timestamptz, ?::timestamptz,
              'portfolio-balance-observation.v1', 'kis-mock-fixture-v1',
              '{"ownerScopeHash":"sanitized"}'::jsonb,
              repeat('d', 64), repeat('e', 64)
            )
            """.trimIndent(),
            observedAt,
            observedAt,
        )
        jdbcTemplate.update(
            """
            insert into portfolio_position_observations (
              balance_observation_id, symbol, quantity, market_value_krw, is_gold_etf_etn
            )
            values ('obs-balance-s23', '005930', 10, 700000, false)
            """.trimIndent(),
        )

        assertFalse(TransactionSynchronizationManager.isActualTransactionActive())
        val context =
            portfolioContextAdapter.resolve("usr_demo_user", PortfolioSource.KIS_MOCK)
                as PortfolioContextResolution.Available
        val sourceRequest =
            EvaluationSourceRequest(
                actorUserId = "usr_demo_user",
                portfolioContext = context.context,
                orderIntent =
                    OrderIntentSnapshot(
                        symbol = "005930",
                        side = "BUY",
                        orderType = "MARKET",
                        quantity = 2,
                        estimatedPrice = 70000,
                        estimatedAmount = 140000,
                        timeframe = "1d",
                        strategyId = "stored-source-test",
                    ),
                evaluationAsOf = java.time.Instant.parse(observedAt),
            )

        val price = marketQuoteAdapter.load(sourceRequest) as MetricCell.Available
        val balance = kisMockBalanceAdapter.load(sourceRequest) as MetricCell.Available
        val margin = storedMarginAdapter.load(sourceRequest) as MetricCell.Available
        assertEquals(70000L, (price.value as MetricValue.Whole).value)
        assertEquals(1000000L, balance.value.portfolioEquityKrw)
        assertEquals(listOf("005930"), balance.value.positions.map { it.symbol })
        assertEquals(140000L, (margin.value as MetricValue.Whole).value)
        assertEquals(java.time.Instant.parse("2031-02-03T04:10:06Z"), price.freshUntil)
        assertEquals(java.time.Instant.parse("2031-02-03T04:06:06Z"), balance.freshUntil)
        assertFalse(TransactionSynchronizationManager.isActualTransactionActive())

        assertTrue(
            portfolioContextAdapter.resolve("usr_missing_context", PortfolioSource.KIS_MOCK)
                is PortfolioContextResolution.Unavailable,
        )
    }

    @Test
    fun `V14 fill projection observation checks and writer role are fail closed`() {
        insertOrderFixture()
        val orderId = "ord_mock_${"e".repeat(32)}"
        jdbcTemplate.update(
            """
            insert into orders (
              order_id, user_id, account_id, account_scope_hash, decision_id,
              decision_evaluation_id, brokerage_mode, idempotency_scope_hash,
              idempotency_owner_scope_hash, request_hash, symbol, side, order_type,
              quantity, submitted_price_krw, status, order_intent_json,
              result_canonical_json, acknowledged_by, acknowledged_at, submitted_at
            ) values (
              ?, 'usr-flyway', 'acct_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', repeat('1', 64),
              'dec-flyway', 'eval-flyway', 'KIS_MOCK', repeat('2', 64),
              repeat('3', 64), repeat('4', 64), '005930', 'BUY', 'MARKET',
              10, null, 'SUBMITTED',
              '{"symbol":"005930","side":"BUY","orderType":"MARKET","quantity":"10","estimatedPrice":"10000","estimatedAmount":"100000","timeframe":"1d","strategyId":"s33-check"}'::jsonb,
              '{"orderId":"sanitized","brokerageMode":"KIS_MOCK","status":"SUBMITTED"}',
              'usr-flyway', now(), now()
            )
            """.trimIndent(),
            orderId,
        )
        val projection =
            jdbcTemplate.queryForMap(
                """
                select filled_quantity, leaves_quantity, unfilled_terminated_quantity,
                       average_fill_price_krw, reconciliation_status, reconciled_at
                from orders
                where order_id = ?
                """.trimIndent(),
                orderId,
            )
        assertEquals(0L, projection["filled_quantity"])
        assertEquals(10L, projection["leaves_quantity"])
        assertEquals(0L, projection["unfilled_terminated_quantity"])
        assertEquals(null, projection["average_fill_price_krw"])
        assertEquals("NOT_APPLICABLE", projection["reconciliation_status"])
        assertEquals(null, projection["reconciled_at"])

        assertCheckViolation {
            jdbcTemplate.update(
                "update orders set filled_quantity = 1 where order_id = ?",
                orderId,
            )
        }
        assertCheckViolation {
            jdbcTemplate.update(
                """
                insert into order_fill_observations (
                  observation_id, order_id, provider_exec_ref_hash, exec_type,
                  fill_quantity, fill_price_krw, cumulative_quantity, leaves_quantity,
                  average_fill_price_krw, observed_at, received_at, schema_version,
                  source_version, source_ref, completeness, artifact_hash
                ) values (
                  'ofo_ffffffffffffffffffffffffffffffff', ?, repeat('5', 64),
                  'FILL', 10, null, 10, 0, 10000, now(), now(), '1',
                  's3.3-fill-observation-v1', 'fixture-s33-invalid',
                  'COMPLETE', repeat('6', 64)
                )
                """.trimIndent(),
                orderId,
            )
        }

        assertTrue(hasTablePrivilege("decision_fill_writer", "order_fill_observations", "INSERT"))
        listOf("SELECT", "UPDATE", "DELETE", "TRUNCATE").forEach { privilege ->
            assertFalse(
                hasTablePrivilege("decision_fill_writer", "order_fill_observations", privilege),
                "unexpected decision_fill_writer $privilege",
            )
        }
        assertFalse(hasTablePrivilege("decision_app", "order_fill_observations", "SELECT"))
        assertFalse(hasTablePrivilege("decision_app", "order_fill_observations", "INSERT"))
        assertWriterInsert(
            "decision_fill_writer",
            FILL_WRITER_PASSWORD,
            """
            insert into order_fill_observations (
              observation_id, order_id, provider_exec_ref_hash, exec_type,
              fill_quantity, fill_price_krw, cumulative_quantity, leaves_quantity,
              average_fill_price_krw, observed_at, received_at, schema_version,
              source_version, source_ref, completeness, artifact_hash
            ) values (
              'ofo_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', '$orderId', repeat('7', 64),
              'PARTIAL_FILL', 4, 10000, 4, 6, 10000,
              '2031-01-01T00:00:00Z', '2031-01-01T00:00:01Z', '1',
              's3.3-fill-observation-v1', 'fixture-s33-valid',
              'COMPLETE', repeat('8', 64)
            )
            """.trimIndent(),
        )
        assertUniqueViolation {
            jdbcTemplate.update(
                """
                insert into order_fill_observations (
                  observation_id, order_id, provider_exec_ref_hash, exec_type,
                  fill_quantity, fill_price_krw, cumulative_quantity, leaves_quantity,
                  observed_at, received_at, schema_version, source_version,
                  source_ref, completeness, artifact_hash
                ) values (
                  'ofo_dddddddddddddddddddddddddddddddd', ?, repeat('7', 64),
                  'PARTIAL_FILL', 4, 10000, 4, 6, now(), now(), '1',
                  's3.3-fill-observation-v1', 'fixture-s33-duplicate',
                  'COMPLETE', repeat('9', 64)
                )
                """.trimIndent(),
                orderId,
            )
        }
        assertRolePermissionDenied(
            "decision_fill_writer",
            FILL_WRITER_PASSWORD,
            "select * from order_fill_observations",
        )
        assertRolePermissionDenied(
            "decision_fill_writer",
            FILL_WRITER_PASSWORD,
            "update orders set status = status where false",
        )
        assertRolePermissionDenied(
            "decision_app",
            APP_PASSWORD,
            "select * from apply_stored_order_fills('{}'::jsonb, 'invalid-capability')",
        )
    }

    @Test
    fun `internal paper uses only explicit margin and never synthesizes position classification`() {
        jdbcTemplate.update(
            """
            insert into paper_accounts (
              account_id, user_id, name, cash_balance, currency, status,
              created_at, updated_at, owner_scope_hash, margin_requirement_krw
            )
            values (
              'acct_00000000000000000000000000000023',
              'usr_demo_admin', 'Paper S2.3', 900000, 'KRW', 'ACTIVE',
              '2031-02-03T04:00:00Z', '2031-02-03T04:05:06Z',
              repeat('2', 64), null
            )
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into paper_positions (
              position_id, account_id, symbol, quantity, average_price, market_value, updated_at
            )
            values (
              'position-s23', 'acct_00000000000000000000000000000023',
              '999999', 1, 100000, 100000, '2031-02-03T04:05:06Z'
            )
            """.trimIndent(),
        )
        val resolution =
            portfolioContextAdapter.resolve("usr_demo_admin", PortfolioSource.INTERNAL_PAPER)
                as PortfolioContextResolution.Available
        val request =
            EvaluationSourceRequest(
                actorUserId = "usr_demo_admin",
                portfolioContext = resolution.context,
                orderIntent =
                    OrderIntentSnapshot(
                        symbol = "005930",
                        side = "BUY",
                        orderType = "LIMIT",
                        quantity = 1,
                        estimatedPrice = 70000,
                        estimatedAmount = 70000,
                        timeframe = "1d",
                        strategyId = "paper-source-test",
                    ),
                evaluationAsOf = java.time.Instant.parse("2031-02-03T04:05:06Z"),
            )

        assertTrue(internalPaperBalanceAdapter.load(request) is MetricCell.Incomplete)
        assertTrue(storedMarginAdapter.load(request) is MetricCell.Missing)

        jdbcTemplate.update(
            """
            update paper_accounts
            set margin_requirement_krw = 0
            where account_id = 'acct_00000000000000000000000000000023'
            """.trimIndent(),
        )
        val explicitResolution =
            portfolioContextAdapter.resolve("usr_demo_admin", PortfolioSource.INTERNAL_PAPER)
                as PortfolioContextResolution.Available
        val explicitRequest = request.copy(portfolioContext = explicitResolution.context)
        val explicitMargin = storedMarginAdapter.load(explicitRequest) as MetricCell.Available
        assertEquals(0L, (explicitMargin.value as MetricValue.Whole).value)
    }

    @Test
    fun `approved stored instrument risk and daily order sources are bounded production ports`() {
        val evaluationAsOf = Instant.parse("2026-06-24T03:00:00Z")
        jdbcTemplate.update(
            """
            insert into instrument_catalog_observations (
              observation_id, symbol, is_etf_etn, is_gold_etf_etn, product_risk_score,
              catalog_version, observed_at, received_at, completeness, schema_version,
              source_version, payload_json, source_ref, artifact_hash
            ) values (
              'ins-s23-v1', '005930', false, false, null, 'catalog-v1',
              '2026-06-24T01:00:00Z', '2026-06-24T01:00:01Z', 'COMPLETE',
              'instrument-catalog-observation.v1', 'sanitized-fixture-v1',
              '{"symbol":"005930","catalogVersion":"catalog-v1"}'::jsonb,
              repeat('1', 64), repeat('2', 64)
            ), (
              'ins-s23-v2', '005930', true, false, 0.35, 'catalog-v2',
              '2026-06-24T02:00:00Z', '2026-06-24T02:00:01Z', 'COMPLETE',
              'instrument-catalog-observation.v1', 'sanitized-fixture-v2',
              '{"symbol":"005930","catalogVersion":"catalog-v2"}'::jsonb,
              repeat('3', 64), repeat('4', 64)
            )
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into deterministic_risk_observations (
              observation_id, owner_user_id, owner_scope_hash, portfolio_source,
              daily_loss_rate, max_drawdown, annualized_volatility, completeness,
              observed_at, received_at, schema_version, source_version, payload_json,
              source_ref, artifact_hash
            ) values (
              'risk-s23-read', 'usr_demo_user', repeat('c', 64), 'KIS_MOCK',
              -0.0125, -0.0800, 0.2200, 'COMPLETE',
              '2026-06-23T06:31:00Z', '2026-06-23T06:31:01Z',
              'deterministic-risk-observation.v1', 'risk-fixture-v1',
              '{"ownerScopeHash":"sanitized"}'::jsonb, repeat('5', 64), repeat('6', 64)
            )
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into daily_order_count_observations (
              observation_id, owner_user_id, owner_scope_hash, portfolio_source,
              trading_date, order_count, covered_through, completeness,
              observed_at, received_at, schema_version, source_version, payload_json,
              source_ref, artifact_hash
            ) values (
              'orders-s23-read', 'usr_demo_user', repeat('c', 64), 'KIS_MOCK',
              '2026-06-24', 0, ?::timestamptz, 'COMPLETE',
              ?::timestamptz, ?::timestamptz,
              'daily-order-count-observation.v1', 'order-ledger-fixture-v1',
              '{"ownerScopeHash":"sanitized","orderCount":0}'::jsonb,
              repeat('7', 64), repeat('8', 64)
            )
            """.trimIndent(),
            java.time.OffsetDateTime.ofInstant(evaluationAsOf, java.time.ZoneOffset.UTC),
            java.time.OffsetDateTime.ofInstant(evaluationAsOf, java.time.ZoneOffset.UTC),
            java.time.OffsetDateTime.ofInstant(evaluationAsOf, java.time.ZoneOffset.UTC),
        )
        val request =
            EvaluationSourceRequest(
                actorUserId = "usr_demo_user",
                portfolioContext =
                    com.capstone.decision.application.risk.port.PortfolioContextRef(
                        opaqueRef = "c".repeat(64),
                        source = PortfolioSource.KIS_MOCK,
                        ownerScopeHash = "c".repeat(64),
                    ),
                orderIntent =
                    OrderIntentSnapshot(
                        symbol = "005930",
                        side = "BUY",
                        orderType = "MARKET",
                        quantity = 1,
                        estimatedPrice = 70000,
                        estimatedAmount = 70000,
                        timeframe = "1d",
                        strategyId = "stored-hard-source-test",
                    ),
                evaluationAsOf = evaluationAsOf,
            )

        val instrument = instrumentCatalogPort.load(request) as MetricCell.Available
        assertEquals("catalog-v2", instrument.value.catalogVersion)
        assertEquals(MetricSource.INSTRUMENT_CATALOG, instrument.source)
        assertEquals(
            "0.35",
            instrument.value.productRiskScore
                ?.stripTrailingZeros()
                ?.toPlainString(),
        )

        val risk = riskSnapshotPort.load(request)
        assertEquals("-0.0125", metricDecimal(risk.dailyLossRate))
        assertEquals("-0.08", metricDecimal(risk.maxDrawdown))
        assertEquals("0.22", metricDecimal(risk.annualizedVolatility))

        val orderCount = orderMetricPort.loadDailyOrderCount(request) as MetricCell.Available
        assertEquals(0L, (orderCount.value as MetricValue.Whole).value)
    }

    @Test
    fun `partial and inactive observations never become complete current source values`() {
        jdbcTemplate.update(
            """
            insert into market_quote_observations (
              observation_id, symbol, source, price_krw, completeness,
              observed_at, received_at, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) values (
              'quote-partial-s23', '000660', 'KIS_MOCK', 180000, 'PARTIAL',
              '2026-06-24T02:59:00Z', '2026-06-24T02:59:01Z',
              'market-quote-observation.v1', 'partial-fixture-v1',
              '{"symbol":"000660"}'::jsonb, repeat('1', 64), repeat('2', 64)
            )
            """.trimIndent(),
        )
        val sourceRequest =
            EvaluationSourceRequest(
                actorUserId = "usr_demo_user",
                portfolioContext =
                    com.capstone.decision.application.risk.port.PortfolioContextRef(
                        opaqueRef = "c".repeat(64),
                        source = PortfolioSource.KIS_MOCK,
                        ownerScopeHash = "c".repeat(64),
                    ),
                orderIntent =
                    OrderIntentSnapshot(
                        symbol = "000660",
                        side = "BUY",
                        orderType = "MARKET",
                        quantity = 1,
                        estimatedPrice = 180000,
                        estimatedAmount = 180000,
                        timeframe = "1d",
                        strategyId = "partial-source-test",
                    ),
                evaluationAsOf = Instant.parse("2026-06-24T03:00:00Z"),
            )
        assertTrue(marketQuoteAdapter.load(sourceRequest) is MetricCell.Incomplete)

        assertCheckViolation {
            jdbcTemplate.update(
                """
                insert into deterministic_risk_observations (
                  observation_id, owner_user_id, owner_scope_hash, portfolio_source,
                  daily_loss_rate, completeness, observed_at, received_at,
                  schema_version, source_version, payload_json, source_ref, artifact_hash
                ) values (
                  'risk-partial-invalid', 'usr_demo_user', repeat('d', 64), 'KIS_MOCK',
                  -2, 'PARTIAL', now(), now(), 'risk-v1', 'fixture-v1', '{}'::jsonb,
                  repeat('3', 64), repeat('4', 64)
                )
                """.trimIndent(),
            )
        }
        assertCheckViolation {
            jdbcTemplate.update(
                """
                insert into daily_order_count_observations (
                  observation_id, owner_user_id, owner_scope_hash, portfolio_source,
                  trading_date, order_count, covered_through, completeness,
                  observed_at, received_at, schema_version, source_version,
                  payload_json, source_ref, artifact_hash
                ) values (
                  'orders-partial-invalid', 'usr_demo_user', repeat('d', 64), 'KIS_MOCK',
                  current_date, -1, now(), 'PARTIAL', now(), now(),
                  'orders-v1', 'fixture-v1', '{}'::jsonb, repeat('5', 64), repeat('6', 64)
                )
                """.trimIndent(),
            )
        }

        jdbcTemplate.update(
            """
            insert into portfolio_balance_observations (
              observation_id, owner_user_id, account_scope_hash, source, context_status,
              cash_krw, portfolio_equity_krw, margin_requirement_krw, completeness,
              position_count, observed_at, received_at, schema_version, source_version,
              payload_json, source_ref, artifact_hash
            ) values
              (
                'balance-active-old', 'usr_demo_user', repeat('e', 64), 'KIS_MOCK', 'ACTIVE',
                1, 1, 0, 'COMPLETE', 0, '2026-06-24T01:00:00Z', '2026-06-24T01:00:01Z',
                'balance-v1', 'fixture-v1', '{}'::jsonb, repeat('7', 64), repeat('8', 64)
              ),
              (
                'balance-inactive-new', 'usr_demo_user', repeat('e', 64), 'KIS_MOCK', 'INACTIVE',
                1, 1, 0, 'COMPLETE', 0, '2026-06-24T02:00:00Z', '2026-06-24T02:00:01Z',
                'balance-v1', 'fixture-v1', '{}'::jsonb, repeat('9', 64), repeat('a', 64)
              )
            """.trimIndent(),
        )
        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { connection ->
            connection.autoCommit = false
            connection
                .prepareStatement("select set_config('app.actor_user_id', ?, true)")
                .use { statement ->
                    statement.setString(1, "usr_demo_user")
                    statement.executeQuery().close()
                }
            connection
                .prepareStatement(
                    "select count(*) from latest_portfolio_balance_observations where account_scope_hash = ?",
                ).use { statement ->
                    statement.setString(1, "e".repeat(64))
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        assertEquals(0, result.getInt(1))
                    }
                }
            connection.rollback()
        }
    }

    @Test
    fun `previous close only observation never becomes a zero current price metric`() {
        jdbcTemplate.update(
            """
            insert into market_quote_observations (
              observation_id, symbol, source, price_krw, previous_close_krw,
              bid_krw, ask_krw, completeness, observed_at, received_at,
              schema_version, source_version, payload_json, source_ref, artifact_hash
            ) values (
              'quote-previous-close-only-s32', '035720', 'KIS_MOCK', null, 45000,
              44950, 45000, 'COMPLETE',
              '2026-06-24T02:59:00Z', '2026-06-24T02:59:01Z',
              'market-quote-observation.v1', 'previous-close-only-v1',
              '{"symbol":"035720","priceKrw":null,"previousCloseKrw":45000}'::jsonb,
              repeat('b', 64), repeat('c', 64)
            )
            """.trimIndent(),
        )
        val request =
            EvaluationSourceRequest(
                actorUserId = "usr_demo_user",
                portfolioContext =
                    com.capstone.decision.application.risk.port.PortfolioContextRef(
                        opaqueRef = "c".repeat(64),
                        source = PortfolioSource.KIS_MOCK,
                        ownerScopeHash = "c".repeat(64),
                    ),
                orderIntent =
                    OrderIntentSnapshot(
                        symbol = "035720",
                        side = "BUY",
                        orderType = "MARKET",
                        quantity = 1,
                        estimatedPrice = 45_000,
                        estimatedAmount = 45_000,
                        timeframe = "1d",
                        strategyId = "previous-close-only-test",
                    ),
                evaluationAsOf = Instant.parse("2026-06-24T03:00:00Z"),
            )

        assertTrue(marketQuoteAdapter.load(request) is MetricCell.Missing)
    }

    @Test
    fun `processed event rejects duplicate event per consumer`() {
        jdbcTemplate.update(
            """
            insert into processed_event (event_id, consumer_name, processed_at)
            values ('evt-duplicate', 'risk-consumer', now())
            """.trimIndent(),
        )

        assertUniqueViolation {
            jdbcTemplate.update(
                """
                insert into processed_event (event_id, consumer_name, processed_at)
                values ('evt-duplicate', 'risk-consumer', now())
                """.trimIndent(),
            )
        }
    }

    @Test
    fun `artifact ingest state rejects duplicate run file and schema version`() {
        jdbcTemplate.update(
            """
            insert into artifact_ingest_state (run_id, file_hash, schema_version, file_name, status)
            values ('run-2026-06-23', 'hash-abc', '1.0.0', 'lstm_signals.parquet', 'VALIDATED')
            """.trimIndent(),
        )

        assertUniqueViolation {
            jdbcTemplate.update(
                """
                insert into artifact_ingest_state (run_id, file_hash, schema_version, file_name, status)
                values ('run-2026-06-23', 'hash-abc', '1.0.0', 'lstm_signals.parquet', 'VALIDATED')
                """.trimIndent(),
            )
        }
    }

    @Test
    fun `orders reject reusing the same decision id`() {
        insertOrderFixture()
        jdbcTemplate.update(
            """
            insert into orders (
                order_id, user_id, account_id, account_scope_hash, decision_id,
                decision_evaluation_id, brokerage_mode, idempotency_scope_hash,
                idempotency_owner_scope_hash, request_hash, symbol, side, order_type,
                quantity, submitted_price_krw, status, order_intent_json,
                result_canonical_json, acknowledged_by, acknowledged_at, submitted_at
            )
            values (
                'ord_mock_00000000000000000000000000000001', 'usr-flyway',
                'acct_00000000000000000000000000000001', repeat('1', 64),
                'dec-flyway', 'eval-flyway', 'KIS_MOCK', repeat('2', 64),
                repeat('3', 64), repeat('4', 64), '005930', 'BUY', 'MARKET',
                1, null, 'SUBMITTED',
                '{"symbol":"005930","side":"BUY","orderType":"MARKET","quantity":"1","estimatedPrice":"70000","estimatedAmount":"70000","timeframe":"1d","strategyId":"flyway-fixture"}'::jsonb,
                '{"orderId":"ord_mock_00000000000000000000000000000001","brokerageMode":"KIS_MOCK","status":"SUBMITTED","submittedAt":"2030-01-02T03:04:05Z"}',
                'usr-flyway', now(), now()
            )
            """.trimIndent(),
        )

        assertUniqueViolation {
            jdbcTemplate.update(
                """
                insert into orders (
                    order_id, user_id, account_id, account_scope_hash, decision_id,
                    decision_evaluation_id, brokerage_mode, idempotency_scope_hash,
                    idempotency_owner_scope_hash, request_hash, symbol, side, order_type,
                    quantity, submitted_price_krw, status, order_intent_json,
                    result_canonical_json, acknowledged_by, acknowledged_at, submitted_at
                )
                values (
                    'ord_mock_00000000000000000000000000000002', 'usr-flyway',
                    'acct_00000000000000000000000000000001', repeat('1', 64),
                    'dec-flyway', 'eval-flyway', 'KIS_MOCK', repeat('5', 64),
                    repeat('6', 64), repeat('7', 64), '005930', 'BUY', 'MARKET',
                    1, null, 'SUBMITTED',
                    '{"symbol":"005930","side":"BUY","orderType":"MARKET","quantity":"1","estimatedPrice":"70000","estimatedAmount":"70000","timeframe":"1d","strategyId":"flyway-fixture"}'::jsonb,
                    '{"orderId":"ord_mock_00000000000000000000000000000002","brokerageMode":"KIS_MOCK","status":"SUBMITTED","submittedAt":"2030-01-02T03:04:05Z"}',
                    'usr-flyway', now(), now()
                )
                """.trimIndent(),
            )
        }
    }

    @Test
    fun `Decision child rows reject cross wired decision and evaluation identities`() {
        insertOrderFixture()
        insertSecondDecisionFixture()

        assertForeignKeyViolation {
            jdbcTemplate.update(
                """
                insert into decision_artifacts (
                  decision_id, evaluation_id, result_canonical_json,
                  snapshot_artifact_canonical_json, semantic_input_hash,
                  snapshot_artifact_hash, created_at
                ) values (
                  'dec-flyway', 'eval-flyway-b', '{}', '{}',
                  repeat('a', 64), repeat('b', 64), now()
                )
                """.trimIndent(),
            )
        }
        assertForeignKeyViolation {
            jdbcTemplate.update(
                """
                insert into decision_violations (
                  violation_id, decision_id, evaluation_id, ordinal, rule_id,
                  severity, message, created_at
                ) values (
                  'vio-cross-wire', 'dec-flyway', 'eval-flyway-b', 1,
                  'cross-wire-guard', 'INFO', 'sanitized fixture', now()
                )
                """.trimIndent(),
            )
        }
        assertForeignKeyViolation {
            jdbcTemplate.update(
                """
                insert into decision_traces (
                  trace_id, decision_id, evaluation_id, step, trace_type,
                  trace_json, created_at
                ) values (
                  'trc-cross-wire', 'dec-flyway', 'eval-flyway-b', 1,
                  'ORDER_VALIDATED', '{}'::jsonb, now()
                )
                """.trimIndent(),
            )
        }
        assertForeignKeyViolation {
            jdbcTemplate.update(
                """
                with clock as (select now() as created_at)
                insert into decision_idempotency_results (
                  idempotency_result_id, scope_hash, generation, request_hash,
                  owner_scope_hash, purpose_version, decision_id, evaluation_id,
                  http_status, content_type, result_canonical_json, created_at, expires_at
                )
                select
                  'idr-cross-wire', repeat('1', 64), 1, repeat('2', 64),
                  repeat('3', 64), 's2.3-idempotency-v1',
                  'dec-flyway', 'eval-flyway-b', 200, 'application/json',
                  '{}', created_at, created_at + interval '24 hours'
                from clock
                """.trimIndent(),
            )
        }
    }

    @Test
    fun `Decision audit target must equal its sanitized payload identity`() {
        insertOrderFixture()
        insertSecondDecisionFixture()

        assertCheckViolation {
            jdbcTemplate.update(
                """
                insert into audit_logs (
                  audit_log_id, user_id, actor_role, action, target_type,
                  target_id, request_id, payload_json, created_at
                ) values (
                  'aud-cross-wire', 'usr-flyway', 'USER', 'DECISION_EVALUATED',
                  'DECISION', 'dec-flyway', 'req-cross-wire',
                  jsonb_build_object(
                    'evaluationId', 'eval-flyway-b',
                    'decisionId', 'dec-flyway-b',
                    'outcome', 'ALLOW',
                    'principleVersionId', 'prv-flyway-v1',
                    'semanticInputHash', repeat('a', 64),
                    'snapshotArtifactHash', repeat('b', 64)
                  ),
                  now()
                )
                """.trimIndent(),
            )
        }
    }

    @Test
    fun `Decision validity constraint rejects an already expired persisted result`() {
        insertOrderFixture()

        assertCheckViolation {
            jdbcTemplate.update(
                "update decisions set valid_until = created_at where decision_id = 'dec-flyway'",
            )
        }
    }

    @Test
    fun `instrument latest projection resolves equal times by observation id only`() {
        jdbcTemplate.update(
            """
            insert into instrument_catalog_observations (
              observation_id, symbol, is_etf_etn, is_gold_etf_etn,
              product_risk_score, catalog_version, observed_at, received_at,
              completeness, schema_version, source_version, payload_json,
              source_ref, artifact_hash
            ) values
              (
                'ins-tie-a', 'ZZTIE', false, false, null, 'catalog-a',
                '2030-01-01T00:00:00Z', '2030-01-01T00:00:01Z',
                'COMPLETE', 'instrument-catalog-observation.v1', 'fixture-v1',
                '{}'::jsonb, repeat('1', 64), repeat('2', 64)
              ),
              (
                'ins-tie-b', 'ZZTIE', true, false, 0.25, 'catalog-z',
                '2030-01-01T00:00:00Z', '2030-01-01T00:00:01Z',
                'COMPLETE', 'instrument-catalog-observation.v1', 'fixture-v1',
                '{}'::jsonb, repeat('3', 64), repeat('4', 64)
              )
            """.trimIndent(),
        )

        assertEquals(
            "ins-tie-a",
            jdbcTemplate.queryForObject(
                "select observation_id from latest_instrument_catalog_observations where symbol = 'ZZTIE'",
                String::class.java,
            ),
        )
    }

    @Test
    fun `latest observation indexes match their exact projection partition order`() {
        val instrumentIndex =
            jdbcTemplate.queryForObject(
                "select pg_get_indexdef(indexrelid) from pg_index where indexrelid = 'instrument_catalog_latest_idx'::regclass",
                String::class.java,
            )
        val portfolioIndex =
            jdbcTemplate.queryForObject(
                "select pg_get_indexdef(indexrelid) from pg_index where indexrelid = 'portfolio_balance_latest_idx'::regclass",
                String::class.java,
            )

        assertTrue(
            requireNotNull(instrumentIndex).contains(
                "(symbol, observed_at DESC, received_at DESC, observation_id)",
            ),
        )
        assertTrue(
            requireNotNull(portfolioIndex).contains(
                "(owner_user_id, account_scope_hash, observed_at DESC, received_at DESC, observation_id)",
            ),
        )
    }

    @Test
    fun `Decision owner projections bind the requested id inside the definer function`() {
        listOf(
            "read_decision_owner_projection()",
            "read_decision_audit_projection()",
        ).forEach { functionName ->
            val definition =
                jdbcTemplate.queryForObject(
                    "select pg_get_functiondef(?::regprocedure)",
                    String::class.java,
                    functionName,
                )
            assertTrue(requireNotNull(definition).contains("app.requested_decision_id"))
        }
        assertTrue(
            indexExists("decision_audit_projection_target_idx"),
            "missing bounded Decision audit lookup index",
        )
    }

    private fun assertInvalidationOwnerScope(
        actorUserId: String,
        expectedRows: Int,
    ) {
        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { connection ->
            connection.autoCommit = false
            connection.prepareStatement("select set_config('app.actor_user_id', ?, true)").use { statement ->
                statement.setString(1, actorUserId)
                statement.executeQuery().close()
            }
            connection.createStatement().use { statement ->
                statement.executeQuery("select count(*) from decision_invalidations").use { result ->
                    assertTrue(result.next())
                    assertEquals(expectedRows, result.getInt(1))
                }
            }
            connection.rollback()
        }
    }

    private fun revalidateKillSwitchAdmin(securityVersion: Long): String =
        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { connection ->
            connection.prepareStatement("select revalidate_kill_switch_admin('usr_demo_admin', ?)").use { statement ->
                statement.setLong(1, securityVersion)
                statement.executeQuery().use { result ->
                    assertTrue(result.next())
                    result.getString(1)
                }
            }
        }

    private fun countDecisionInvalidations(decisionId: String): Int =
        jdbcTemplate.queryForObject(
            "select count(*) from decision_invalidations where decision_id = ?",
            Int::class.java,
            decisionId,
        ) ?: 0

    private fun assertDecisionUsability(
        actorUserId: String,
        decisionId: String,
        expectedRows: Int,
        invalidated: Boolean,
        consumed: String?,
    ) {
        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { connection ->
            connection.autoCommit = false
            connection.prepareStatement("select set_config('app.actor_user_id', ?, true)").use { statement ->
                statement.setString(1, actorUserId)
                statement.executeQuery().close()
            }
            connection.prepareStatement("select set_config('app.requested_decision_id', ?, true)").use { statement ->
                statement.setString(1, decisionId)
                statement.executeQuery().close()
            }
            connection.createStatement().use { statement ->
                statement.executeQuery("select * from read_decision_usability()").use { result ->
                    var rows = 0
                    while (result.next()) {
                        rows += 1
                        assertEquals(invalidated, result.getBoolean("invalidated"))
                        assertEquals(consumed, result.getString("consumed_by_order_id"))
                    }
                    assertEquals(expectedRows, rows)
                }
            }
            connection.rollback()
        }
    }

    private fun insertAdminDecisionFixture() {
        jdbcTemplate.update(
            """
            insert into principles (
              principle_id, user_id, preset_id, title, mode, status, current_version
            ) values (
              'prn-v10-admin', 'usr_demo_admin', 'balanced',
              'V10 Admin Principle', 'GUIDE', 'ACTIVE', 1
            )
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into principle_versions (
              principle_version_id, principle_id, version, preset_id, title,
              mode, status, rules_json, changed_fields, created_by
            )
            select
              'prv-v10-admin-v1', 'prn-v10-admin', 1, 'balanced',
              'V10 Admin Principle', 'GUIDE', 'ACTIVE', rules_json,
              array['presetId', 'title', 'mode', 'status', 'rules'], 'usr_demo_admin'
            from principle_presets
            where preset_id = 'balanced'
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into decisions (
              decision_id, evaluation_id, user_id, principle_id, principle_version_id,
              principle_version, portfolio_source, symbol, side, outcome, mode,
              can_submit_order, enforcement_action, evaluation_as_of, created_at, valid_until,
              result_schema_version, snapshot_schema_version, catalog_version,
              readiness_policy_version, mapping_versions_json, semantic_input_hash,
              snapshot_artifact_hash, result_json
            ) values (
              'dec-v10-admin', 'eval-v10-admin', 'usr_demo_admin',
              'prn-v10-admin', 'prv-v10-admin-v1', 1, 'INTERNAL_PAPER',
              '005930', 'BUY', 'ALLOW', 'GUIDE', true, 'NONE',
              now(), now(), now() + interval '10 minutes',
              'risk-decision.v1', 's2.2-metric-snapshot-v2', 1,
              's2.3-readiness-v1', '{}'::jsonb, repeat('e', 64),
              repeat('f', 64), '{}'::jsonb
            )
            """.trimIndent(),
        )
    }

    private fun cleanupS24InvalidationFixtures() {
        deleteOrderFillFixtures("('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')")
        jdbcTemplate.update(
            "delete from orders where decision_id in ('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')",
        )
        jdbcTemplate.update(
            "delete from decision_invalidations where decision_id in ('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')",
        )
        jdbcTemplate.update(
            "delete from decision_idempotency_results where decision_id in ('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')",
        )
        jdbcTemplate.update(
            "delete from decision_traces where decision_id in ('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')",
        )
        jdbcTemplate.update(
            "delete from decision_artifacts where decision_id in ('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')",
        )
        jdbcTemplate.update(
            "delete from decision_violations where decision_id in ('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')",
        )
        jdbcTemplate.update(
            "delete from audit_logs where target_id in ('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')",
        )
        jdbcTemplate.update(
            "delete from decisions where decision_id in ('dec-flyway', 'dec-flyway-b', 'dec-v10-admin')",
        )
        jdbcTemplate.update(
            "delete from principle_versions where principle_id in ('prn-flyway', 'prn-v10-admin')",
        )
        jdbcTemplate.update("delete from principles where principle_id in ('prn-flyway', 'prn-v10-admin')")
        jdbcTemplate.update("delete from users where user_id = 'usr-flyway'")
    }

    private fun insertOrderFixture() {
        deleteOrderFillFixtures("('dec-flyway', 'dec-flyway-b')")
        jdbcTemplate.update("delete from orders where decision_id in ('dec-flyway', 'dec-flyway-b')")
        jdbcTemplate.update(
            "delete from decision_invalidations where decision_id in ('dec-flyway', 'dec-flyway-b')",
        )
        jdbcTemplate.update(
            "delete from decision_idempotency_results where decision_id in ('dec-flyway', 'dec-flyway-b')",
        )
        jdbcTemplate.update("delete from decision_traces where decision_id in ('dec-flyway', 'dec-flyway-b')")
        jdbcTemplate.update("delete from decision_artifacts where decision_id in ('dec-flyway', 'dec-flyway-b')")
        jdbcTemplate.update("delete from decision_violations where decision_id in ('dec-flyway', 'dec-flyway-b')")
        jdbcTemplate.update("delete from audit_logs where target_id in ('dec-flyway', 'dec-flyway-b')")
        jdbcTemplate.update("delete from decisions where decision_id in ('dec-flyway', 'dec-flyway-b')")
        jdbcTemplate.update("delete from principle_versions where principle_id = 'prn-flyway'")
        jdbcTemplate.update("delete from principles where principle_id = 'prn-flyway'")
        jdbcTemplate.update("delete from users where user_id = 'usr-flyway'")
        jdbcTemplate.update(
            """
            insert into users (user_id, username, role, password_hash)
            values ('usr-flyway', 'flyway-user', 'USER', 'test-password-hash')
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into principles (
                principle_id, user_id, preset_id, title, mode, status, current_version
            )
            values (
                'prn-flyway', 'usr-flyway', 'balanced', 'Flyway Principle', 'GUIDE', 'ACTIVE', 1
            )
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into principle_versions (
                principle_version_id, principle_id, version, preset_id, title,
                mode, status, rules_json, changed_fields, created_by
            )
            select
                'prv-flyway-v1', 'prn-flyway', 1, 'balanced', 'Flyway Principle',
                'GUIDE', 'ACTIVE', rules_json,
                array['presetId', 'title', 'mode', 'status', 'rules'], 'usr-flyway'
            from principle_presets
            where preset_id = 'balanced'
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            insert into decisions (
                decision_id, evaluation_id, user_id, principle_id, principle_version_id,
                principle_version, portfolio_source, symbol, side, outcome, mode,
                can_submit_order, enforcement_action, evaluation_as_of, created_at, valid_until,
                result_schema_version, snapshot_schema_version, catalog_version,
                readiness_policy_version, mapping_versions_json, semantic_input_hash,
                snapshot_artifact_hash, result_json
            )
            values (
                'dec-flyway', 'eval-flyway', 'usr-flyway', 'prn-flyway', 'prv-flyway-v1',
                1, 'INTERNAL_PAPER', '005930', 'BUY', 'ALLOW', 'GUIDE',
                true, 'NONE', now(), now(), now() + interval '10 minutes',
                'risk-decision.v1', 's2.2-metric-snapshot-v2', 1,
                's2.3-readiness-v1', '{}'::jsonb, repeat('a', 64),
                repeat('b', 64), '{}'::jsonb
            )
            """.trimIndent(),
        )
    }

    private fun deleteOrderFillFixtures(decisionIds: String) {
        // append-only 운영 계약은 유지하되, Testcontainers superuser만 격리 fixture를 역순 정리한다.
        jdbcTemplate.update(
            """
            delete from order_fill_application_receipts
            where order_id in (
              select order_id from orders where decision_id in $decisionIds
            )
            """.trimIndent(),
        )
        jdbcTemplate.update(
            """
            delete from order_fill_observations
            where order_id in (
              select order_id from orders where decision_id in $decisionIds
            )
            """.trimIndent(),
        )
    }

    private fun insertSecondDecisionFixture() {
        jdbcTemplate.update(
            """
            insert into decisions (
              decision_id, evaluation_id, user_id, principle_id, principle_version_id,
              principle_version, portfolio_source, symbol, side, outcome, mode,
              can_submit_order, enforcement_action, evaluation_as_of, created_at, valid_until,
              result_schema_version, snapshot_schema_version, catalog_version,
              readiness_policy_version, mapping_versions_json, semantic_input_hash,
              snapshot_artifact_hash, result_json
            )
            select
              'dec-flyway-b', 'eval-flyway-b', user_id, principle_id, principle_version_id,
              principle_version, portfolio_source, symbol, side, outcome, mode,
              can_submit_order, enforcement_action, evaluation_as_of, created_at, valid_until,
              result_schema_version, snapshot_schema_version, catalog_version,
              readiness_policy_version, mapping_versions_json, repeat('c', 64),
              repeat('d', 64), result_json
            from decisions
            where decision_id = 'dec-flyway'
            """.trimIndent(),
        )
    }

    private fun createDatabase(name: String): String {
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement -> statement.execute("create database $name") }
        }
        return postgres.jdbcUrl.substringBeforeLast('/') + "/$name"
    }

    private fun setMigrationLoggingPolicy(
        logStatement: String,
        parameterMaxLength: Int,
        errorParameterMaxLength: Int,
    ) {
        require(postgres.username == "decision")
        require(logStatement in setOf("none", "all"))
        require(parameterMaxLength in setOf(-1, 0))
        require(errorParameterMaxLength in setOf(-1, 0))
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                // 다음 Flyway connection의 실제 role-level logging policy를 synthetic allowlist로 전환한다.
                statement.execute("alter role decision set log_statement = '$logStatement'")
                statement.execute("alter role decision set log_parameter_max_length = $parameterMaxLength")
                statement.execute("alter role decision set log_parameter_max_length_on_error = $errorParameterMaxLength")
            }
        }
    }

    private fun assertCredentialEvidenceAbsent(logs: String) {
        val bundles = listOf(TEST_USER_CREDENTIAL_BUNDLE, TEST_ADMIN_CREDENTIAL_BUNDLE)
        val decodedEvidence =
            bundles.flatMap { bundle ->
                val segments = bundle.split(':')
                check(segments.size == 5)
                listOf(
                    Base64.getUrlDecoder().decode(segments[2]),
                    Base64.getUrlDecoder().decode(segments[4]),
                )
            }
        try {
            val forbiddenEvidence =
                listOf(
                    TEST_USER_PASSWORD,
                    TEST_ADMIN_PASSWORD,
                    TEST_CREDENTIAL_SEPARATION_KEY,
                    TEST_USER_CREDENTIAL_BUNDLE,
                    TEST_ADMIN_CREDENTIAL_BUNDLE,
                    TEST_USER_PASSWORD_HASH,
                    TEST_ADMIN_PASSWORD_HASH,
                ) +
                    bundles.flatMap { bundle -> bundle.split(':').drop(2) } +
                    decodedEvidence.map(HexFormat.of()::formatHex)
            forbiddenEvidence.forEachIndexed { index, evidence ->
                assertFalse(logs.contains(evidence), "credential evidence index $index appeared in PostgreSQL logs")
            }
        } finally {
            decodedEvidence.forEach { it.fill(0) }
        }
    }

    private fun assertV7RolledBack(url: String) {
        DriverManager.getConnection(url, postgres.username, postgres.password).use { connection ->
            connection.createStatement().use { statement ->
                statement
                    .executeQuery(
                        "select count(*) from information_schema.columns " +
                            "where table_schema = 'public' and table_name = 'users' " +
                            "and column_name in ('security_version', 'credential_reuse_tag', " +
                            "'credential_bundle_mac', 'credential_policy_version')",
                    ).use { result ->
                        assertTrue(result.next())
                        assertEquals(0, result.getInt(1))
                    }
                statement.executeQuery("select count(*) from flyway_schema_history where version = '7'").use { result ->
                    assertTrue(result.next())
                    assertEquals(0, result.getInt(1))
                }
            }
        }
    }

    private fun flyway(
        url: String,
        target: String? = null,
    ): Flyway {
        val configuration =
            Flyway
                .configure()
                .dataSource(url, postgres.username, postgres.password)
                .locations("classpath:db/migration")
                .placeholders(
                    mapOf(
                        "brokerageDbCapabilityTokenSha256" to TEST_BROKERAGE_DB_CAPABILITY_TOKEN_SHA256,
                    ),
                ).javaMigrations(s21ActorTrustMigration())
        target?.let(configuration::target)
        return configuration.load()
    }

    private fun callMarketWriterAppend(
        functionName: String,
        payload: String,
    ): String {
        require(
            functionName in
                setOf(
                    "append_market_source_entitlement",
                    "append_cross_market_exposure_catalog_entry",
                    "append_cross_market_observation",
                    "append_analyst_revision_evidence",
                    "append_market_cause_evidence",
                ),
        )
        DriverManager.getConnection(postgres.jdbcUrl, "decision_market_writer", MARKET_WRITER_PASSWORD).use { connection ->
            connection.prepareStatement("select $functionName(?::jsonb)").use { statement ->
                statement.setString(1, payload)
                statement.executeQuery().use { result ->
                    check(result.next())
                    return result.getString(1)
                }
            }
        }
    }

    private fun crossMarketFixture(
        mapper: JsonMapper,
        fileName: String,
    ): ObjectNode =
        mapper
            .readTree(
                Files.readString(repositoryRoot().resolve("contracts/examples").resolve(fileName)),
            ).deepCopy() as ObjectNode

    private fun assertUnknownCrossMarketPayloadIsRejected(
        mapper: JsonMapper,
        functionName: String,
        canonicalPayload: ObjectNode,
    ) {
        val poisonedPayload = canonicalPayload.deepCopy() as ObjectNode
        poisonedPayload.put("untrustedContent", "raw provider article payload")

        val exception =
            assertThrows<SQLException> {
                callMarketWriterAppend(functionName, mapper.writeValueAsString(poisonedPayload))
            }
        assertEquals("22023", exception.sqlState, "unknown payload key must fail before persistence")
        assertTrue(exception.message.orEmpty().contains("cross-market fixture payload is not permitted"))
    }

    private fun assertUnknownNestedCrossMarketPayloadIsRejected(
        mapper: JsonMapper,
        functionName: String,
        canonicalPayload: ObjectNode,
        nestedField: String,
    ) {
        val poisonedPayload = canonicalPayload.deepCopy() as ObjectNode
        val nestedPayload = poisonedPayload.get(nestedField) as ObjectNode
        nestedPayload.put("untrustedContent", "raw provider article payload")

        val exception =
            assertThrows<SQLException> {
                callMarketWriterAppend(functionName, mapper.writeValueAsString(poisonedPayload))
            }
        assertEquals("22023", exception.sqlState, "unknown nested payload key must fail before persistence")
        assertTrue(exception.message.orEmpty().contains("cross-market fixture payload is not permitted"))
    }

    private fun assertObjectAtAllowedScalarIsRejected(
        mapper: JsonMapper,
        functionName: String,
        canonicalPayload: ObjectNode,
        fieldName: String,
    ) {
        val poisonedPayload = canonicalPayload.deepCopy() as ObjectNode
        poisonedPayload.set(
            fieldName,
            mapper.createObjectNode().put("untrustedContent", "raw provider article payload"),
        )

        assertCrossMarketPayloadIsRejected(mapper, functionName, poisonedPayload)
    }

    private fun assertObjectInsideAllowedArrayIsRejected(
        mapper: JsonMapper,
        functionName: String,
        canonicalPayload: ObjectNode,
        fieldName: String,
    ) {
        val poisonedPayload = canonicalPayload.deepCopy() as ObjectNode
        val poisonedArray = mapper.createArrayNode()
        poisonedArray.add(mapper.createObjectNode().put("untrustedContent", "raw provider article payload"))
        poisonedPayload.set(fieldName, poisonedArray)

        assertCrossMarketPayloadIsRejected(mapper, functionName, poisonedPayload)
    }

    private fun assertCrossMarketPayloadIsRejected(
        mapper: JsonMapper,
        functionName: String,
        poisonedPayload: ObjectNode,
    ) {
        val exception =
            assertThrows<SQLException> {
                callMarketWriterAppend(functionName, mapper.writeValueAsString(poisonedPayload))
            }
        assertEquals("22023", exception.sqlState, "payload object must fail before persistence")
        assertTrue(exception.message.orEmpty().contains("cross-market fixture payload is not permitted"))
    }

    private fun repositoryRoot(): Path {
        var current = Path.of(System.getProperty("user.dir")).toAbsolutePath()
        while (!Files.exists(current.resolve("AGENTS.md"))) {
            current = current.parent ?: error("repository root was not found")
        }
        return current
    }

    private data class MarketWriterAppendPayload(
        val functionName: String,
        val payload: ObjectNode,
        val expectedResult: String,
    )

    private fun functionExists(signature: String): Boolean =
        jdbcTemplate.queryForObject(
            "select to_regprocedure(?) is not null",
            Boolean::class.java,
            signature,
        ) ?: false

    private fun tableExists(tableName: String): Boolean =
        jdbcTemplate.queryForObject(
            """
            select exists (
                select 1
                from information_schema.tables
                where table_schema = 'public' and table_name = ?
            )
            """.trimIndent(),
            Boolean::class.java,
            tableName,
        ) ?: false

    private fun indexExists(indexName: String): Boolean =
        jdbcTemplate.queryForObject(
            "select exists (select 1 from pg_indexes where schemaname = 'public' and indexname = ?)",
            Boolean::class.java,
            indexName,
        ) ?: false

    private fun indexDefinitionLike(
        tableName: String,
        pattern: String,
    ): Boolean =
        jdbcTemplate.queryForObject(
            "select exists (select 1 from pg_indexes where schemaname = 'public' and tablename = ? and indexdef like ?)",
            Boolean::class.java,
            tableName,
            pattern,
        ) ?: false

    private fun queryStrings(sql: String): List<String> = jdbcTemplate.query(sql) { rs, _ -> rs.getString(1) }

    private fun tableType(tableName: String): String =
        jdbcTemplate.queryForObject(
            "select table_type from information_schema.tables where table_schema = 'public' and table_name = ?",
            String::class.java,
            tableName,
        ) ?: ""

    private fun countRows(
        tableName: String,
        predicate: String,
    ): Int {
        require(predicate == "canonical_rule_version = 'V4_COMPAT_MIGRATION'")
        // 식별자를 SQL에 직접 보간하지 않고 이 테스트가 승인한 두 이관 대상만 조회한다.
        val sql =
            when (tableName) {
                "trading_sessions" ->
                    "select count(*) from trading_sessions where canonical_rule_version = 'V4_COMPAT_MIGRATION'"
                "trading_session_revisions" ->
                    "select count(*) from trading_session_revisions where canonical_rule_version = 'V4_COMPAT_MIGRATION'"
                else -> error("unsupported migration table: $tableName")
            }
        return jdbcTemplate.queryForObject(
            sql,
            Int::class.java,
        ) ?: 0
    }

    private data class S5Candidate(
        val releaseId: String,
        val batchId: String,
        val releaseManifestSha256: String,
        val batchManifestSha256: String,
    )

    private fun prepareS5CurrentClock(): Pair<java.time.LocalDate, java.time.OffsetDateTime> {
        val kst = java.time.ZoneId.of("Asia/Seoul")
        val session = java.time.LocalDate.of(2026, 8, 13)
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection
                .prepareStatement(
                    """
                    INSERT INTO trading_sessions(
                      exchange_mic, session_date, is_open, open_at, close_at, timezone,
                      reason, chosen_source_id, degraded, fallback_reason, as_of,
                      confidence_bps, has_conflict, canonical_hash, canonical_rule_version,
                      confidence_rule_version
                    ) VALUES (
                      'XKRX', ?, true, ?, ?, 'Asia/Seoul', NULL, 'S5_6_TEST', false, NULL,
                      statement_timestamp(), 9900, false, ?, 'S5_6_TEST', 's1.6-confidence-v1'
                    ) ON CONFLICT (exchange_mic, session_date) DO UPDATE SET
                      is_open = true, open_at = EXCLUDED.open_at, close_at = EXCLUDED.close_at,
                      has_conflict = false
                    """.trimIndent(),
                ).use { statement ->
                    listOf(session, session.plusDays(1), java.time.LocalDate.of(2026, 8, 18))
                        .forEachIndexed { index, day ->
                            statement.setObject(1, day)
                            statement.setObject(2, day.atTime(9, 0).atZone(kst).toOffsetDateTime())
                            statement.setObject(3, day.atTime(15, 30).atZone(kst).toOffsetDateTime())
                            statement.setString(4, (index + 4).toString().repeat(64))
                            statement.executeUpdate()
                        }
                }
        }
        // 하드코딩한 pair를 반환하면 마지막 session 경계가 지난 뒤 activation 대상이 어긋난다.
        // 실제 activation이 비교하는 값과 같은 clock을 DB에서 읽어 어떤 실행 시각에서도 일치시킨다.
        DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password).use { connection ->
            connection
                .prepareStatement(
                    "SELECT session_date, as_of FROM s5_signal_batch_clock_at(statement_timestamp())",
                ).use { statement ->
                    statement.executeQuery().use { result ->
                        assertTrue(result.next())
                        return result.getObject("session_date", java.time.LocalDate::class.java) to
                            result
                                .getTimestamp("as_of")
                                .toInstant()
                                .atZone(kst)
                                .toOffsetDateTime()
                    }
                }
        }
    }

    private fun clearS5ProductionState() {
        jdbcTemplate.update("DELETE FROM active_signal_batch")
        jdbcTemplate.update("DELETE FROM active_signal_model_release")
        jdbcTemplate.update("DELETE FROM signal_batch_publications")
        jdbcTemplate.update("DELETE FROM signal_batch_members")
        jdbcTemplate.update("DELETE FROM signal_batches")
        jdbcTemplate.update("DELETE FROM signal_model_release_transitions")
        jdbcTemplate.update("DELETE FROM signal_model_releases")
        jdbcTemplate.update("DELETE FROM signal_universe_releases")
        jdbcTemplate.update(
            "DELETE FROM ingested_signals WHERE model_release_id IS NOT NULL OR signal_batch_id IS NOT NULL",
        )
    }

    private fun stageS5Candidate(
        seed: String,
        session: java.time.LocalDate,
        asOf: java.time.OffsetDateTime,
        signal: String,
        batchPurpose: String = "DAILY",
        batchSeed: String = seed,
    ): S5Candidate {
        val releaseId = "lgr-${seed.repeat(12)}"
        val modelVersion = "lgbm-v1-${seed.repeat(12)}"
        val reportId = "mrp-${seed.repeat(12)}"
        val releaseManifestText =
            """{"calendarName":"XKRX","calendarVersion":"4.13.2","codeHead":"${seed.repeat(
                40,
            )}","codeTree":"${seed.repeat(
                40,
            )}","featureManifestSha256":"${seed.repeat(
                64,
            )}","files":{},"fixture":false,"modelReleaseId":"$releaseId","modelReportId":"$reportId","modelVersion":"$modelVersion","provenanceClass":"PRODUCTION","releaseVersion":"s5-model-release-v1","semanticSha256":"${seed.repeat(
                64,
            )}","sourceBundleSetSha256":"${seed.repeat(
                64,
            )}","sourcePolicySetSha256":"${seed.repeat(
                64,
            )}","status":"QUALIFIED","temporalQuality":"RECONSTRUCTED_FIXED_LAG","trainingDatasetSha256":"${seed.repeat(
                64,
            )}","uvLockSha256":"${seed.repeat(64)}"}"""
        val releaseManifest = sha256Hex(releaseManifestText)
        val symbols = ((1..29).map { it.toString().padStart(6, '0') } + "005930" + "132030").sorted()
        val membershipJson = symbols.joinToString(separator = ",", prefix = "[", postfix = "]") { "\"$it\"" }
        val membershipDigest = sha256Hex("s5-inference-universe-v1\u0000$membershipJson")
        val universeId = "sur-${membershipDigest.take(12)}"
        val asOfText = asOf.toInstant().toString()
        val members =
            symbols.joinToString(prefix = "[", postfix = "]") { symbol ->
                """{"asOf":"$asOfText","confidence":0.5,"modelReportId":"$reportId","modelVersion":"$modelVersion","signal":"$signal","status":"AVAILABLE","symbol":"$symbol"}"""
            }
        val batchId = "sgb-${batchSeed.repeat(12)}"
        val batchManifestText =
            """{"asOf":"$asOfText","batchPurpose":"$batchPurpose","batchVersion":"s5-signal-batch-v1","fixture":false,"membershipSha256":"$membershipDigest","membersSha256":"${sha256Hex(
                members,
            )}","modelReleaseId":"$releaseId","parquetFile":"signals.parquet","parquetSha256":"${batchSeed.repeat(
                64,
            )}","provenanceClass":"PRODUCTION","rowCount":31,"semanticSha256":"${batchSeed.repeat(
                64,
            )}","sessionDate":"$session","signalBatchId":"$batchId","timeframe":"1d","universeReleaseId":"$universeId"}"""
        val batchManifest = sha256Hex(batchManifestText)
        DriverManager.getConnection(postgres.jdbcUrl, "decision_signal_writer", "signal-writer-test").use { writer ->
            writer.prepareStatement("SELECT stage_signal_model_release(?,?,?,?,?,?,?,?,?,?,?)").use { statement ->
                listOf(
                    releaseManifest,
                    releaseManifestText,
                    releaseId,
                    modelVersion,
                    reportId,
                    seed.repeat(64),
                    seed.repeat(64),
                    seed.repeat(64),
                    seed.repeat(40),
                    seed.repeat(40),
                    seed.repeat(64),
                ).forEachIndexed { index, value -> statement.setString(index + 1, value) }
                statement.executeQuery().use { assertTrue(it.next()) }
            }
            writer.prepareStatement("SELECT stage_signal_batch(?,?,?,?,?,?,?,?,?)").use { statement ->
                statement.setString(1, batchManifest)
                statement.setString(2, batchManifestText)
                statement.setString(3, batchId)
                statement.setString(4, releaseId)
                statement.setString(5, universeId)
                statement.setString(6, membershipDigest)
                statement.setObject(7, session)
                statement.setObject(8, asOf)
                statement.setString(9, members)
                statement.executeQuery().use { assertTrue(it.next()) }
            }
        }
        return S5Candidate(releaseId, batchId, releaseManifest, batchManifest)
    }

    private fun activateS5(
        connection: java.sql.Connection,
        candidate: S5Candidate,
        expectedReleaseId: String,
        expectedBatchId: String,
        reason: String,
    ): Long =
        connection.prepareStatement("SELECT activate_signal_model_and_batch(?,?,?,?,?,?,?)").use { statement ->
            statement.setString(1, candidate.releaseId)
            statement.setString(2, candidate.batchId)
            statement.setString(3, expectedReleaseId)
            statement.setString(4, expectedBatchId)
            statement.setString(5, candidate.releaseManifestSha256)
            statement.setString(6, candidate.batchManifestSha256)
            statement.setString(7, reason)
            statement.executeQuery().use { result ->
                assertTrue(result.next())
                result.getLong(1)
            }
        }

    private fun hasTablePrivilege(
        role: String,
        table: String,
        privilege: String,
    ): Boolean =
        jdbcTemplate.queryForObject(
            "select has_table_privilege(?, 'public.' || ?, ?)",
            Boolean::class.java,
            role,
            table,
            privilege,
        ) ?: false

    private fun hasSchemaPrivilege(
        role: String,
        privilege: String,
    ): Boolean =
        jdbcTemplate.queryForObject(
            "select has_schema_privilege(?, 'public', ?)",
            Boolean::class.java,
            role,
            privilege,
        ) ?: false

    private fun hasFunctionPrivilege(
        role: String,
        functionSignature: String,
    ): Boolean =
        jdbcTemplate.queryForObject(
            "select has_function_privilege(?, ?, 'EXECUTE')",
            Boolean::class.java,
            role,
            functionSignature,
        ) ?: false

    private fun assertDecisionAppPermissionDenied(sql: String) {
        assertRolePermissionDenied("decision_app", APP_PASSWORD, sql)
    }

    private fun assertRolePermissionDenied(
        role: String,
        password: String,
        sql: String,
    ) {
        DriverManager.getConnection(postgres.jdbcUrl, role, password).use { connection ->
            connection.createStatement().use { statement ->
                val exception = assertThrows<SQLException> { statement.execute(sql) }
                assertEquals("42501", exception.sqlState, "expected permission denial for: $sql")
            }
        }
    }

    private fun assertWriterInsert(
        role: String,
        password: String,
        sql: String,
    ) {
        DriverManager.getConnection(postgres.jdbcUrl, role, password).use { connection ->
            connection.createStatement().use { statement ->
                assertEquals(1, statement.executeUpdate(sql), "writer $role failed its exact INSERT")
            }
        }
    }

    private fun metricDecimal(cell: MetricCell<MetricValue>): String {
        val available = cell as MetricCell.Available
        return available.value
            .asBigDecimal()
            .stripTrailingZeros()
            .toPlainString()
    }

    private fun countMarketCalendarRows(
        market: String,
        calendarDate: String,
        isTradingDay: Boolean,
    ): Int =
        jdbcTemplate.queryForObject(
            """
            select count(*)
            from market_calendar
            where market = ? and calendar_date = ?::date and is_trading_day = ?
            """.trimIndent(),
            Int::class.java,
            market,
            calendarDate,
            isTradingDay,
        ) ?: 0

    private fun assertUniqueViolation(block: () -> Unit) {
        val exception =
            org.junit.jupiter.api.assertThrows<DataIntegrityViolationException> {
                block()
            }
        val sqlException = exception.findSqlException()
        assertTrue(
            sqlException?.sqlState == "23505",
            "expected SQLState 23505 but was ${sqlException?.sqlState}: ${exception.mostSpecificCause.message}",
        )
    }

    private fun sha256Hex(value: String): String =
        HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.toByteArray()))

    private fun assertCheckViolation(block: () -> Unit) {
        val exception =
            org.junit.jupiter.api.assertThrows<DataIntegrityViolationException> {
                block()
            }
        val sqlException = exception.findSqlException()
        assertTrue(
            sqlException?.sqlState == "23514",
            "expected SQLState 23514 but was ${sqlException?.sqlState}: ${exception.mostSpecificCause.message}",
        )
    }

    private fun assertForeignKeyViolation(block: () -> Unit) {
        val exception =
            org.junit.jupiter.api.assertThrows<DataIntegrityViolationException> {
                block()
            }
        val sqlException = exception.findSqlException()
        assertTrue(
            sqlException?.sqlState == "23503",
            "expected SQLState 23503 but was ${sqlException?.sqlState}: ${exception.mostSpecificCause.message}",
        )
    }

    private fun Throwable.findSqlException(): SQLException? {
        var current: Throwable? = this
        while (current != null) {
            if (current is SQLException) {
                return current
            }
            current = current.cause
        }
        return null
    }

    companion object {
        private const val APP_PASSWORD = "app-test"
        private const val COLLECTOR_PASSWORD = "collector-test"
        private const val DISCLOSURE_READER_PASSWORD = "disclosure-reader-test"
        private const val MARKET_WRITER_PASSWORD = "market-writer-test"
        private const val PORTFOLIO_WRITER_PASSWORD = "portfolio-writer-test"
        private const val RISK_WRITER_PASSWORD = "risk-writer-test"
        private const val FILL_WRITER_PASSWORD = "fill-writer-test"
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
                .withDatabaseName("decision")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")

        @DynamicPropertySource
        @JvmStatic
        fun postgresProperties(registry: DynamicPropertyRegistry) {
            registry.add("spring.datasource.url", postgres::getJdbcUrl)
            registry.add("spring.datasource.username", postgres::getUsername)
            registry.add("spring.datasource.password", postgres::getPassword)
            registry.add("spring.flyway.user", postgres::getUsername)
            registry.add("spring.flyway.password", postgres::getPassword)
            // V73 historical capability tests remain executable in the primary test database.
            // V74 is applied and verified independently by the research-only migration test above.
            registry.add("spring.flyway.target") { "73" }
            registry.add("app.decision.grpc.shared-secret") { SpringApiIntegrationTestBase.TEST_GRPC_SHARED_SECRET }
            registry.add("app.rag.grpc.shared-secret") {
                SpringApiIntegrationTestBase.TEST_RAG_GRPC_SHARED_SECRET
            }
        }

        @JvmStatic
        fun remainingTrustRootConflicts(): Stream<Arguments> =
            Stream.of(
                Arguments.of(
                    "username collision with another user id",
                    "auth_username_collision",
                    "usr-unrelated",
                    "demo-user",
                    "USER",
                    "ACTIVE",
                    TEST_USER_PASSWORD_HASH,
                ),
                Arguments.of(
                    "approved id and username with wrong role",
                    "auth_wrong_role",
                    "usr_demo_user",
                    "demo-user",
                    "ADMIN",
                    "ACTIVE",
                    TEST_USER_PASSWORD_HASH,
                ),
                Arguments.of(
                    "approved id and username with wrong status",
                    "auth_wrong_status",
                    "usr_demo_user",
                    "demo-user",
                    "USER",
                    "LOCKED",
                    TEST_USER_PASSWORD_HASH,
                ),
                Arguments.of(
                    "approved id and username with wrong hash",
                    "auth_wrong_hash",
                    "usr_demo_user",
                    "demo-user",
                    "USER",
                    "ACTIVE",
                    TEST_ADMIN_PASSWORD_HASH,
                ),
            )
    }
}
