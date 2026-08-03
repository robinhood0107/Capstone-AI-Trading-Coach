package com.capstone.decision

import org.flywaydb.core.Flyway
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.TestInstance
import org.junit.jupiter.api.assertThrows
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import java.sql.Connection
import java.sql.DriverManager
import java.sql.SQLException
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

// V25는 actual PostgreSQL role/RLS/FK graph에서만 CAS와 hard-delete 순서를 신뢰할 수 있다.
@Testcontainers
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class RagV2ImmutableBundleMigrationIntegrationTest {
    @BeforeAll
    fun migrate() {
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
    }

    @BeforeEach
    fun resetV25State() {
        adminConnection().use { connection ->
            connection.createStatement().use { statement ->
                statement.execute(
                    """
                    truncate table
                      rag_v2_immutable_owner_document_deletion_tombstones,
                      rag_v2_immutable_deletion_receipts,
                      rag_v2_immutable_activation_receipts,
                      rag_v2_immutable_import_tickets,
                      rag_v2_immutable_consent_events,
                      rag_v2_immutable_owner_bundle_pointers,
                      rag_v2_immutable_bundles,
                      rag_v2_immutable_public_bundle_pointers,
                      rag_v2_immutable_embedding_receipts,
                      rag_v2_immutable_chunk_receipts,
                      rag_v2_immutable_source_receipts,
                      rag_v2_immutable_materialization_runs,
                      rag_v2_immutable_embedding_cache,
                      rag_v2_immutable_generation_embeddings,
                      rag_v2_immutable_generation_memberships,
                      rag_v2_immutable_component_generations,
                      rag_v2_immutable_chunks,
                      rag_v2_immutable_source_revisions
                    cascade
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_public_bundle_pointers (
                      state_id, state, exact30_generation_id, oa112_generation_id,
                      embedding_profile_id, pointer_version
                    ) values ('default', 'NOT_MATERIALIZED', null, null, null, 1)
                    """.trimIndent(),
                )
            }
        }
    }

    @Test
    fun `ticket is owner policy bound expires in five minutes and is consumed once`() {
        val ticketId = "rti_11111111111111111111111111111111"
        val secondTicketId = "rti_22222222222222222222222222222222"
        seedOwnerImportRun("usr_demo_user", OWNER_IMPORT_GENERATION, OWNER_IMPORT_RUN)
        seedOwnerImportRun("usr_demo_user", OWNER_SECOND_IMPORT_GENERATION, OWNER_SECOND_IMPORT_RUN)
        seedOwnerImportRun("usr_demo_admin", OTHER_OWNER_IMPORT_GENERATION, OTHER_OWNER_IMPORT_RUN)
        issueTicket("usr_demo_user", ticketId, "OWNER_IMPORT")
        issueTicket("usr_demo_user", secondTicketId, "OWNER_IMPORT")

        adminConnection().use { connection ->
            assertEquals(
                "00:05:00",
                queryString(
                    connection,
                    """
                    select to_char(expires_at - issued_at, 'HH24:MI:SS')
                    from rag_v2_immutable_import_tickets
                    where ticket_hash = encode(digest('$ticketId', 'sha256'), 'hex')
                    """.trimIndent(),
                ),
            )
            assertFalse(
                queryString(
                    connection,
                    """
                    select exists (
                      select 1
                      from information_schema.columns
                      where table_schema = 'public'
                        and table_name = 'rag_v2_immutable_import_tickets'
                        and column_name = 'ticket_id'
                    )
                    """.trimIndent(),
                ).toBoolean(),
            )
            assertFalse(hasTablePrivilege(connection, "decision_app", "rag_v2_immutable_import_tickets", "SELECT"))
            assertFalse(hasTablePrivilege(connection, "decision_rag_writer", "rag_v2_immutable_import_tickets", "UPDATE"))
            assertFalse(hasTablePrivilege(connection, "decision_rag_admin", "rag_v2_immutable_import_tickets", "SELECT"))
            assertTrue(
                hasFunctionPrivilege(
                    connection,
                    "decision_app",
                    "issue_rag_v2_immutable_import_ticket(text,text,text,text)",
                ),
            )
            assertTrue(
                hasFunctionPrivilege(
                    connection,
                    "decision_rag_writer",
                    "consume_rag_v2_immutable_import_ticket(text,text,text,text,text)",
                ),
            )
            assertFalse(
                hasFunctionPrivilege(
                    connection,
                    "decision_app",
                    "delete_owner_rag_v2_document(text,text,text,text)",
                ),
            )
        }

        assertTrue(consumeTicket("usr_demo_user", ticketId, "OWNER_IMPORT", OWNER_IMPORT_RUN))
        assertFalse(consumeTicket("usr_demo_user", ticketId, "OWNER_IMPORT", OWNER_SECOND_IMPORT_RUN))
        assertFalse(consumeTicket("usr_demo_admin", secondTicketId, "OWNER_IMPORT", OTHER_OWNER_IMPORT_RUN))
        assertTrue(consumeTicket("usr_demo_user", secondTicketId, "OWNER_IMPORT", OWNER_SECOND_IMPORT_RUN))

        assertPermissionDenied("decision_app", APP_PASSWORD, "select * from rag_v2_immutable_import_tickets")
        assertPermissionDenied("decision_rag_writer", RAG_WRITER_PASSWORD, "select * from rag_v2_immutable_import_tickets")
    }

    @Test
    fun `consent is recorded only for the bound active owner`() {
        val grantEventId = "cns_v2_11111111111111111111111111111111"
        val revokeEventId = "cns_v2_22222222222222222222222222222222"
        val disclosureDigest = "a".repeat(64)

        recordConsent("usr_demo_user", grantEventId, "GRANT", disclosureDigest)
        recordConsent("usr_demo_user", revokeEventId, "REVOKE", "b".repeat(64))

        adminConnection().use { connection ->
            assertEquals(
                "REVOKE",
                queryString(
                    connection,
                    """
                    select action
                    from rag_v2_immutable_consent_events
                    where owner_user_id = 'usr_demo_user'
                    order by created_at desc, consent_event_id desc
                    limit 1
                    """.trimIndent(),
                ),
            )
            assertEquals(
                "2",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_consent_events where owner_user_id = 'usr_demo_user'",
                ),
            )
        }

        val crossOwnerAttempt =
            assertThrows<SQLException> {
                DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { connection ->
                    connection.autoCommit = false
                    try {
                        connection.prepareStatement("select set_config('app.actor_user_id', ?, false)").use { statement ->
                            statement.setString(1, "usr_demo_user")
                            statement.execute()
                        }
                        callSingleRow(
                            connection,
                            """
                            select record_rag_v2_immutable_consent(
                              'usr_demo_admin', 'cns_v2_33333333333333333333333333333333', 'GRANT', repeat('c', 64)
                            )
                            """.trimIndent(),
                        )
                    } finally {
                        connection.rollback()
                    }
                }
            }
        assertEquals("22023", crossOwnerAttempt.sqlState)
        assertPermissionDenied("decision_app", APP_PASSWORD, "select * from rag_v2_immutable_consent_events")
    }

    @Test
    fun `replacement bundle CAS activates before owner document hard delete and retains no target rows`() {
        seedEvaluatedPublicComponents()
        val publicVersion =
            callLong(
                "decision_rag_admin",
                RAG_ADMIN_PASSWORD,
                """
                select activate_rag_v2_immutable_public_base(
                  '$EXACT_GENERATION', '$OA_GENERATION', 1, '$PUBLIC_ACTIVATION_RECEIPT'
                )
                """.trimIndent(),
            )
        assertEquals(2L, publicVersion)

        seedOwnerDeletionFixtures()
        val oldBundleVersion =
            callLong(
                "decision_rag_admin",
                RAG_ADMIN_PASSWORD,
                """
                select activate_rag_v2_immutable_owner_bundle(
                  'usr_demo_user', '$OLD_BUNDLE', null, 0, '$OLD_OWNER_ACTIVATION_RECEIPT', 'OWNER_BUNDLE'
                )
                """.trimIndent(),
            )
        assertEquals(1L, oldBundleVersion)

        val invalidReplacement =
            assertThrows<SQLException> {
                callBoolean(
                    "decision_rag_admin",
                    RAG_ADMIN_PASSWORD,
                    """
                    select delete_rag_v2_immutable_owner_document(
                      'usr_demo_user', '$TARGET_DOCUMENT', '$BAD_BUNDLE', '$OLD_BUNDLE', 1,
                      '$BAD_ACTIVATION_RECEIPT', '$BAD_DELETION_RECEIPT', repeat('a', 64)
                    )
                    """.trimIndent(),
                )
            }
        assertEquals("23514", invalidReplacement.sqlState)
        adminConnection().use { connection ->
            assertEquals(
                OLD_BUNDLE,
                queryString(
                    connection,
                    "select active_bundle_id from rag_v2_immutable_owner_bundle_pointers where owner_user_id = 'usr_demo_user'",
                ),
            )
            assertEquals(
                "1",
                queryString(connection, "select count(*) from rag_v2_immutable_source_revisions where document_id = '$TARGET_DOCUMENT'"),
            )
        }

        assertTrue(
            callBoolean(
                "decision_rag_admin",
                RAG_ADMIN_PASSWORD,
                """
                select delete_rag_v2_immutable_owner_document(
                  'usr_demo_user', '$TARGET_DOCUMENT', '$REPLACEMENT_BUNDLE', '$OLD_BUNDLE', 1,
                  '$DELETE_ACTIVATION_RECEIPT', '$DELETE_RECEIPT', repeat('b', 64)
                )
                """.trimIndent(),
            ),
        )
        adminConnection().use { connection ->
            assertEquals(
                REPLACEMENT_BUNDLE,
                queryString(
                    connection,
                    "select active_bundle_id from rag_v2_immutable_owner_bundle_pointers where owner_user_id = 'usr_demo_user'",
                ),
            )
            assertEquals(
                "2",
                queryString(
                    connection,
                    "select bundle_version::text from rag_v2_immutable_owner_bundle_pointers where owner_user_id = 'usr_demo_user'",
                ),
            )
            assertEquals(
                "0",
                queryString(connection, "select count(*) from rag_v2_immutable_source_revisions where document_id = '$TARGET_DOCUMENT'"),
            )
            assertEquals("0", queryString(connection, "select count(*) from rag_v2_immutable_chunks where owner_user_id = 'usr_demo_user'"))
            assertEquals(
                "0",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_generation_embeddings where owner_user_id = 'usr_demo_user'",
                ),
            )
            assertEquals(
                "0",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_component_generations where component_generation_id = '$OLD_OWNER_GENERATION'",
                ),
            )
            assertEquals(
                "1",
                queryString(
                    connection,
                    "select deleted_source_revision_count::text from rag_v2_immutable_deletion_receipts where deletion_receipt_id = '$DELETE_RECEIPT'",
                ),
            )
            assertEquals(
                "1",
                queryString(
                    connection,
                    "select deleted_chunk_count::text from rag_v2_immutable_deletion_receipts where deletion_receipt_id = '$DELETE_RECEIPT'",
                ),
            )
            assertEquals(
                "1",
                queryString(
                    connection,
                    "select deleted_embedding_count::text from rag_v2_immutable_deletion_receipts where deletion_receipt_id = '$DELETE_RECEIPT'",
                ),
            )
            assertEquals(
                "REPLACED_ACTIVE",
                queryString(
                    connection,
                    "select deletion_kind from rag_v2_immutable_deletion_receipts where deletion_receipt_id = '$DELETE_RECEIPT'",
                ),
            )
            assertEquals(
                "0",
                queryString(connection, "select count(*) from rag_v2_immutable_embedding_cache where cache_id = '$DELETE_CACHE'"),
            )
            assertEquals(
                "0",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_source_receipts where receipt_id = '$DELETE_SOURCE_RECEIPT' and source_revision_id is not null",
                ),
            )
            assertEquals(
                "0",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_chunk_receipts where receipt_id = '$DELETE_CHUNK_RECEIPT' and (source_revision_id is not null or chunk_id is not null)",
                ),
            )
            assertEquals(
                "0",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_embedding_receipts where receipt_id = '$DELETE_EMBEDDING_RECEIPT' and (component_generation_id is not null or chunk_id is not null)",
                ),
            )
            assertEquals(
                "0",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_materialization_runs where materialization_run_id = '$DELETE_OWNER_RUN' and component_generation_id is not null",
                ),
            )
        }
    }

    @Test
    fun `replacement deletion rejects a bundle that omits another owner document`() {
        seedEvaluatedPublicComponents()
        callLong(
            "decision_rag_admin",
            RAG_ADMIN_PASSWORD,
            """
            select activate_rag_v2_immutable_public_base(
              '$EXACT_GENERATION', '$OA_GENERATION', 1, '$PUBLIC_ACTIVATION_RECEIPT'
            )
            """.trimIndent(),
        )
        seedOwnerDeletionFixtures()
        seedSurvivingOwnerDocument()
        callLong(
            "decision_rag_admin",
            RAG_ADMIN_PASSWORD,
            """
            select activate_rag_v2_immutable_owner_bundle(
              'usr_demo_user', '$OLD_BUNDLE', null, 0, '$OLD_OWNER_ACTIVATION_RECEIPT', 'OWNER_BUNDLE'
            )
            """.trimIndent(),
        )

        val failure =
            assertThrows<SQLException> {
                callBoolean(
                    "decision_rag_admin",
                    RAG_ADMIN_PASSWORD,
                    """
                    select delete_rag_v2_immutable_owner_document(
                      'usr_demo_user', '$TARGET_DOCUMENT', '$REPLACEMENT_BUNDLE', '$OLD_BUNDLE', 1,
                      '$DELETE_ACTIVATION_RECEIPT', '$DELETE_RECEIPT', repeat('a', 64)
                    )
                    """.trimIndent(),
                )
            }
        assertEquals("23514", failure.sqlState)

        adminConnection().use { connection ->
            assertEquals(
                OLD_BUNDLE,
                queryString(
                    connection,
                    "select active_bundle_id from rag_v2_immutable_owner_bundle_pointers where owner_user_id = 'usr_demo_user'",
                ),
            )
            assertEquals(
                "1",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_source_revisions where document_id = '$SURVIVING_DOCUMENT'",
                ),
            )
            assertEquals(
                "1",
                queryString(
                    connection,
                    """
                    select count(*)
                    from rag_v2_immutable_generation_memberships
                    where component_generation_id = '$OLD_OWNER_GENERATION'
                      and source_revision_id = '$SURVIVING_SOURCE'
                    """.trimIndent(),
                ),
            )
        }

        seedReplacementWithSurvivingDocument()
        assertTrue(
            callBoolean(
                "decision_rag_admin",
                RAG_ADMIN_PASSWORD,
                """
                select delete_rag_v2_immutable_owner_document(
                  'usr_demo_user', '$TARGET_DOCUMENT', '$REPLACEMENT_BUNDLE', '$OLD_BUNDLE', 1,
                  '$DELETE_ACTIVATION_RECEIPT', '$DELETE_RECEIPT', repeat('b', 64)
                )
                """.trimIndent(),
            ),
        )
        adminConnection().use { connection ->
            assertEquals(
                REPLACEMENT_BUNDLE,
                queryString(
                    connection,
                    "select active_bundle_id from rag_v2_immutable_owner_bundle_pointers where owner_user_id = 'usr_demo_user'",
                ),
            )
            assertEquals(
                "1",
                queryString(
                    connection,
                    """
                    select count(*)
                    from rag_v2_immutable_generation_memberships
                    where component_generation_id = '$REPLACEMENT_OWNER_GENERATION'
                      and source_revision_id = '$SURVIVING_SOURCE'
                      and chunk_id = '$SURVIVING_CHUNK'
                    """.trimIndent(),
                ),
            )
            assertEquals(
                "0",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_source_revisions where document_id = '$TARGET_DOCUMENT'",
                ),
            )
        }
    }

    @Test
    fun `public activation rejects more than twenty eight reserves without promotion`() {
        seedEvaluatedPublicComponents()
        adminConnection().use { connection ->
            connection.createStatement().use { statement ->
                statement.execute(
                    """
                    insert into rag_v2_immutable_source_revisions (
                      source_revision_id, document_id, source_id, owner_user_id, source_scope, oa_track_id,
                      reserve_source, source_revision_sha256, raw_content_sha256, normalized_document_ir_sha256, canonical_text_sha256,
                      document_ir, canonical_text, sanitized_display_name, source_locator, canonical_https_url,
                      license_evidence_sha256, access_evidence_sha256, mime_type,
                      machine_fetch_allowed, local_processing_allowed, external_embedding_allowed,
                      external_generation_allowed, external_processing_eligible, parser_version, tokenizer_version
                    )
                    select
                      format('srv_reserve_%s', lpad(item::text, 3, '0')),
                      format('doc_reserve_%s', lpad(item::text, 11, '0')),
                      format('src_reserve_%s', lpad(item::text, 3, '0')),
                      null, 'OA112',
                      (array[
                        'MICRO_GAME_INFO_MARKET_DESIGN', 'MACRO_MONETARY_INTERNATIONAL',
                        'PROBABILITY_STATISTICS_OPTIMIZATION', 'ECONOMETRICS_CAUSAL_EVENT_STUDY',
                        'TIME_SERIES_REGIME_VOLATILITY', 'ACCOUNTING_CORPORATE_FINANCE_VALUATION',
                        'ASSET_PRICING_FACTOR_PORTFOLIO', 'FIXED_INCOME_RATES_CREDIT',
                        'DERIVATIVES_STOCHASTIC_NUMERICS', 'MARKET_MICROSTRUCTURE_EXECUTION_LIQUIDITY',
                        'RISK_STRESS_BACKTEST_MODEL_RISK', 'BEHAVIORAL_EFFICIENCY_ANOMALY_CROWDING',
                        'FINANCIAL_ML_PIT_DATA_PROVENANCE', 'CROSS_MARKET_COMMODITIES_POLICY_KOREA'
                      ])[1 + ((item - 1) % 14)],
                      true, lpad((item + 3000)::text, 64, '0'), repeat('a', 64),
                      encode(digest('reserve fixture ' || item, 'sha256'), 'hex'),
                      encode(digest('reserve fixture ' || item, 'sha256'), 'hex'),
                      jsonb_build_object(
                        'blocks', jsonb_build_array(jsonb_build_object(
                          'blockType', 'PARAGRAPH', 'locator', jsonb_build_object('section', 'fixture'),
                          'readingOrder', 0, 'ocrConfidence', null, 'text', 'reserve fixture ' || item
                        )),
                        'contractId', 'rag-document-ir-v1', 'documentIrVersion', 1,
                        'extractionMode', 'NATIVE', 'languageTags', jsonb_build_array('en'), 'mimeType', 'text/plain',
                        'normalizedContentSha256', encode(digest('reserve fixture ' || item, 'sha256'), 'hex'),
                        'parserEvidence', jsonb_build_object(
                          'ocr', jsonb_build_object('backend', 'NOT_USED', 'backendVersion', null, 'modelSha256', null),
                          'parserArtifactSha256', repeat('d', 64), 'parserBackend', 'capstone-safe-local-document-parser', 'parserVersion', 'fixture-v1'
                        ),
                        'rawContentSha256', repeat('a', 64),
                        'safetyClassification', jsonb_build_object(
                          'externalLlmEligible', true, 'piiDetected', false, 'promptInjectionDetected', false, 'secretDetected', false
                        ),
                        'sourceId', format('src_reserve_%s', lpad(item::text, 3, '0')),
                        'sourceRevisionId', format('srv_reserve_%s', lpad(item::text, 3, '0'))
                      ),
                      'reserve fixture ' || item, null,
                      jsonb_build_object('section', 'reserve-' || item), 'https://example.org/oa-reserve/' || item,
                      repeat('d', 64), repeat('e', 64), 'text/plain', true, true, true, true, true, 'fixture-v1', 'fixture-tokenizer-v1'
                    from generate_series(1, 29) as item
                    """.trimIndent(),
                )
            }
        }

        val failure =
            assertThrows<SQLException> {
                callLong(
                    "decision_rag_admin",
                    RAG_ADMIN_PASSWORD,
                    """
                    select activate_rag_v2_immutable_public_base(
                      '$EXACT_GENERATION', '$OA_GENERATION', 1, '$PUBLIC_ACTIVATION_RECEIPT'
                    )
                    """.trimIndent(),
                )
            }
        assertEquals("23514", failure.sqlState)
        adminConnection().use { connection ->
            assertEquals(
                "NOT_MATERIALIZED",
                queryString(connection, "select state from rag_v2_immutable_public_bundle_pointers where state_id = 'default'"),
            )
        }
    }

    @Test
    fun `concurrent owner activation accepts one CAS winner and leaves one typed conflict`() {
        seedEvaluatedPublicComponents()
        callLong(
            "decision_rag_admin",
            RAG_ADMIN_PASSWORD,
            """
            select activate_rag_v2_immutable_public_base(
              '$EXACT_GENERATION', '$OA_GENERATION', 1, '$PUBLIC_ACTIVATION_RECEIPT'
            )
            """.trimIndent(),
        )
        seedOwnerDeletionFixtures()
        callLong(
            "decision_rag_admin",
            RAG_ADMIN_PASSWORD,
            """
            select activate_rag_v2_immutable_owner_bundle(
              'usr_demo_user', '$OLD_BUNDLE', null, 0, '$OLD_OWNER_ACTIVATION_RECEIPT', 'OWNER_BUNDLE'
            )
            """.trimIndent(),
        )

        val outcomes =
            listOf(
                REPLACEMENT_BUNDLE to RACE_ACTIVATION_RECEIPT,
                RACE_BUNDLE to RACE_SECOND_ACTIVATION_RECEIPT,
            ).map { (bundleId, receiptId) ->
                CompletableFuture.supplyAsync {
                    try {
                        callLong(
                            "decision_rag_admin",
                            RAG_ADMIN_PASSWORD,
                            """
                            select activate_rag_v2_immutable_owner_bundle(
                              'usr_demo_user', '$bundleId', '$OLD_BUNDLE', 1, '$receiptId', 'OWNER_BUNDLE'
                            )
                            """.trimIndent(),
                        )
                        "SUCCESS"
                    } catch (error: SQLException) {
                        error.sqlState.orEmpty()
                    }
                }
            }.map { it.get(10, TimeUnit.SECONDS) }

        assertEquals(1, outcomes.count { it == "SUCCESS" })
        assertEquals(1, outcomes.count { it == "40001" })
        adminConnection().use { connection ->
            assertTrue(
                queryString(
                    connection,
                    "select active_bundle_id from rag_v2_immutable_owner_bundle_pointers where owner_user_id = 'usr_demo_user'",
                ) in setOf(REPLACEMENT_BUNDLE, RACE_BUNDLE),
            )
        }
    }

    @Test
    fun unreferencedStagingDeleteTerminalizesResumeAndTombstonesOwnerDocument() {
        seedOwnerImportRun(
            "usr_demo_user",
            OWNER_IMPORT_GENERATION,
            OWNER_IMPORT_RUN,
            STAGING_DOCUMENT,
        )
        seedOwnerStagingDocumentArtifacts()
        issueTicket("usr_demo_user", STAGING_TICKET, "OWNER_IMPORT")

        assertTrue(
            callBoolean(
                "decision_rag_admin",
                RAG_ADMIN_PASSWORD,
                """
                select delete_rag_v2_immutable_owner_document(
                  'usr_demo_user', '$STAGING_DOCUMENT',
                  null::text, null::text, null::bigint, null::text,
                  '$STAGING_DELETE_RECEIPT', repeat('c', 64)
                )
                """.trimIndent(),
            ),
        )
        assertFalse(consumeTicket("usr_demo_user", STAGING_TICKET, "OWNER_IMPORT", OWNER_IMPORT_RUN))

        adminConnection().use { connection ->
            assertEquals(
                "0",
                queryString(connection, "select count(*) from rag_v2_immutable_source_revisions where document_id = '$STAGING_DOCUMENT'"),
            )
            assertEquals("0", queryString(connection, "select count(*) from rag_v2_immutable_chunks where chunk_id = '$STAGING_CHUNK'"))
            assertEquals(
                "0",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_generation_embeddings where component_generation_id = '$OWNER_IMPORT_GENERATION'",
                ),
            )
            assertEquals(
                "0",
                queryString(connection, "select count(*) from rag_v2_immutable_embedding_cache where cache_id = '$STAGING_CACHE'"),
            )
            assertEquals(
                "0",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_component_generations where component_generation_id = '$OWNER_IMPORT_GENERATION'",
                ),
            )
            assertEquals(
                "FAILED",
                queryString(
                    connection,
                    "select state from rag_v2_immutable_materialization_runs where materialization_run_id = '$OWNER_IMPORT_RUN'",
                ),
            )
            assertEquals(
                "OWNER_DELETED",
                queryString(
                    connection,
                    "select failure_code from rag_v2_immutable_materialization_runs where materialization_run_id = '$OWNER_IMPORT_RUN'",
                ),
            )
            assertEquals(
                "0",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_materialization_runs where materialization_run_id = '$OWNER_IMPORT_RUN' and component_generation_id is not null",
                ),
            )
            assertEquals(
                "UNREFERENCED_STAGING",
                queryString(
                    connection,
                    "select deletion_kind from rag_v2_immutable_deletion_receipts where deletion_receipt_id = '$STAGING_DELETE_RECEIPT'",
                ),
            )
            assertEquals(
                "1",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_owner_document_deletion_tombstones where owner_user_id = 'usr_demo_user' and document_id = '$STAGING_DOCUMENT'",
                ),
            )
            val resurrection =
                assertThrows<SQLException> {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            insert into rag_v2_immutable_materialization_runs (
                              materialization_run_id, owner_user_id, component_generation_id, component_scope, document_id, state
                            ) values (
                              'rgr_run_ffffffffffffffffffffffffffffffff', 'usr_demo_user', null,
                              'OWNER_PRIVATE', '$STAGING_DOCUMENT', 'OPEN'
                            )
                            """.trimIndent(),
                        )
                    }
                }
            assertEquals("23514", resurrection.sqlState)
        }
    }

    @Test
    fun `unmaterialized owner run deletion tombstones before the first source can be created`() {
        seedOwnerImportRun(
            "usr_demo_user",
            OWNER_IMPORT_GENERATION,
            OWNER_IMPORT_RUN,
            STAGING_DOCUMENT,
        )
        issueTicket("usr_demo_user", STAGING_TICKET, "OWNER_IMPORT")

        assertTrue(
            callBoolean(
                "decision_rag_admin",
                RAG_ADMIN_PASSWORD,
                """
                select delete_rag_v2_immutable_owner_document(
                  'usr_demo_user', '$STAGING_DOCUMENT',
                  null::text, null::text, null::bigint, null::text,
                  '$UNMATERIALIZED_DELETE_RECEIPT', repeat('e', 64)
                )
                """.trimIndent(),
            ),
        )
        assertFalse(consumeTicket("usr_demo_user", STAGING_TICKET, "OWNER_IMPORT", OWNER_IMPORT_RUN))

        adminConnection().use { connection ->
            assertEquals(
                "FAILED",
                queryString(
                    connection,
                    "select state from rag_v2_immutable_materialization_runs where materialization_run_id = '$OWNER_IMPORT_RUN'",
                ),
            )
            assertEquals(
                "OWNER_DELETED",
                queryString(
                    connection,
                    "select failure_code from rag_v2_immutable_materialization_runs where materialization_run_id = '$OWNER_IMPORT_RUN'",
                ),
            )
            assertEquals(
                "0",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_materialization_runs where materialization_run_id = '$OWNER_IMPORT_RUN' and component_generation_id is not null",
                ),
            )
            assertEquals(
                "UNMATERIALIZED_RUN",
                queryString(
                    connection,
                    "select deletion_kind from rag_v2_immutable_deletion_receipts where deletion_receipt_id = '$UNMATERIALIZED_DELETE_RECEIPT'",
                ),
            )
            assertEquals(
                "1",
                queryString(
                    connection,
                    "select affected_materialization_run_count::text from rag_v2_immutable_deletion_receipts where deletion_receipt_id = '$UNMATERIALIZED_DELETE_RECEIPT'",
                ),
            )
            assertEquals(
                "1",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_owner_document_deletion_tombstones where owner_user_id = 'usr_demo_user' and document_id = '$STAGING_DOCUMENT'",
                ),
            )
        }

        val staleFirstSource = assertThrows<SQLException> { seedOwnerStagingDocumentArtifacts() }
        assertEquals("23514", staleFirstSource.sqlState)

        assertFalse(
            callBoolean(
                "decision_rag_admin",
                RAG_ADMIN_PASSWORD,
                """
                select delete_rag_v2_immutable_owner_document(
                  'usr_demo_user', '$ABSENT_DOCUMENT',
                  null::text, null::text, null::bigint, null::text,
                  '$ABSENT_DELETE_RECEIPT', repeat('f', 64)
                )
                """.trimIndent(),
            ),
        )
        adminConnection().use { connection ->
            assertEquals(
                "0",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_owner_document_deletion_tombstones where owner_user_id = 'usr_demo_user' and document_id = '$ABSENT_DOCUMENT'",
                ),
            )
        }
    }

    @Test
    fun `owner document deletion serializes a concurrent stale resume until tombstone rejects it`() {
        seedOwnerImportRun(
            "usr_demo_user",
            OWNER_IMPORT_GENERATION,
            OWNER_IMPORT_RUN,
            STAGING_DOCUMENT,
        )
        seedOwnerStagingDocumentArtifacts()

        DriverManager.getConnection(postgres.jdbcUrl, "decision_rag_admin", RAG_ADMIN_PASSWORD).use { deletingConnection ->
            deletingConnection.autoCommit = false
            var deleteCommitted = false
            try {
                assertTrue(
                    callBoolean(
                        deletingConnection,
                        """
                        select delete_rag_v2_immutable_owner_document(
                          'usr_demo_user', '$STAGING_DOCUMENT',
                          null::text, null::text, null::bigint, null::text,
                          '$STAGING_DELETE_RECEIPT', repeat('d', 64)
                        )
                        """.trimIndent(),
                    ),
                )

                val writerStarted = CountDownLatch(1)
                val writerBackendId = CompletableFuture<Int>()
                val staleResume =
                    CompletableFuture.supplyAsync {
                        adminConnection().use { writerConnection ->
                            writerConnection.autoCommit = false
                            try {
                                writerBackendId.complete(queryString(writerConnection, "select pg_backend_pid()").toInt())
                                writerStarted.countDown()
                                writerConnection.createStatement().use { statement ->
                                    statement.execute(
                                        """
                                        insert into rag_v2_immutable_materialization_runs (
                                          materialization_run_id, owner_user_id, component_generation_id, component_scope, document_id, state
                                        ) values (
                                          '$STALE_RESUME_RUN', 'usr_demo_user', null, 'OWNER_PRIVATE', '$STAGING_DOCUMENT', 'OPEN'
                                        )
                                        """.trimIndent(),
                                    )
                                }
                                writerConnection.commit()
                                "SUCCESS"
                            } catch (error: SQLException) {
                                writerConnection.rollback()
                                error.sqlState.orEmpty()
                            }
                        }
                    }

                assertTrue(writerStarted.await(5, TimeUnit.SECONDS), "concurrent stale resume did not begin")
                awaitAdvisoryLockWait(writerBackendId.get(5, TimeUnit.SECONDS))
                deletingConnection.commit()
                deleteCommitted = true

                assertEquals("23514", staleResume.get(10, TimeUnit.SECONDS))
            } finally {
                if (!deleteCommitted) {
                    deletingConnection.rollback()
                }
            }
        }

        adminConnection().use { connection ->
            assertEquals(
                "0",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_materialization_runs where materialization_run_id = '$STALE_RESUME_RUN'",
                ),
            )
            assertEquals(
                "0",
                queryString(connection, "select count(*) from rag_v2_immutable_source_revisions where document_id = '$STAGING_DOCUMENT'"),
            )
            assertEquals(
                "1",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_owner_document_deletion_tombstones where owner_user_id = 'usr_demo_user' and document_id = '$STAGING_DOCUMENT'",
                ),
            )
        }
    }

    @Test
    fun `public activation rejects missing immutable OA source cards`() {
        seedEvaluatedPublicComponents(includeOaSourceCards = false)

        val failure =
            assertThrows<SQLException> {
                callLong(
                    "decision_rag_admin",
                    RAG_ADMIN_PASSWORD,
                    """
                    select activate_rag_v2_immutable_public_base(
                      '$EXACT_GENERATION', '$OA_GENERATION', 1, '$PUBLIC_ACTIVATION_RECEIPT'
                    )
                    """.trimIndent(),
                )
            }

        assertEquals("23514", failure.sqlState)
        adminConnection().use { connection ->
            assertEquals(
                "NOT_MATERIALIZED",
                queryString(connection, "select state from rag_v2_immutable_public_bundle_pointers where state_id = 'default'"),
            )
        }
    }

    @Test
    fun `public activation rejects OA source card provenance mismatch and non public URL`() {
        seedEvaluatedPublicComponents(mismatchFirstOaSourceCardUrl = true)

        val activationFailure =
            assertThrows<SQLException> {
                callLong(
                    "decision_rag_admin",
                    RAG_ADMIN_PASSWORD,
                    """
                    select activate_rag_v2_immutable_public_base(
                      '$EXACT_GENERATION', '$OA_GENERATION', 1, '$PUBLIC_ACTIVATION_RECEIPT'
                    )
                    """.trimIndent(),
                )
            }
        assertEquals("23514", activationFailure.sqlState)

        adminConnection().use { connection ->
            assertEquals(
                "false",
                queryString(
                    connection,
                    """
                    select public.rag_v2_immutable_oa_source_card_v4_is_valid(
                      jsonb_set(
                        jsonb_set(
                          source_card,
                          '{canonicalUrl}',
                          to_jsonb('https://127.0.0.1/private.pdf'::text)
                        ),
                        '{canonicalUrlSha256}',
                        to_jsonb(encode(digest('https://127.0.0.1/private.pdf', 'sha256'), 'hex'))
                      )
                    )::text
                    from rag_v2_immutable_oa_source_cards
                    where source_revision_id = 'srv_oa_001'
                    """.trimIndent(),
                ),
            )
        }
    }

    @Test
    fun publicBaseRefreshInvalidatesActiveOwnerBundleUnderForceRls() {
        seedEvaluatedPublicComponents()
        assertEquals(
            2L,
            callLong(
                "decision_rag_admin",
                RAG_ADMIN_PASSWORD,
                """
                select activate_rag_v2_immutable_public_base(
                  '$EXACT_GENERATION', '$OA_GENERATION', 1, '$PUBLIC_ACTIVATION_RECEIPT'
                )
                """.trimIndent(),
            ),
        )
        seedOwnerDeletionFixtures()
        assertEquals(
            1L,
            callLong(
                "decision_rag_admin",
                RAG_ADMIN_PASSWORD,
                """
                select activate_rag_v2_immutable_owner_bundle(
                  'usr_demo_user', '$OLD_BUNDLE', null, 0, '$OLD_OWNER_ACTIVATION_RECEIPT', 'OWNER_BUNDLE'
                )
                """.trimIndent(),
            ),
        )
        seedRefreshPublicComponents()

        assertEquals(
            3L,
            callLong(
                "decision_rag_admin",
                RAG_ADMIN_PASSWORD,
                """
                select activate_rag_v2_immutable_public_base(
                  '$REFRESH_EXACT_GENERATION', '$REFRESH_OA_GENERATION', 2, '$PUBLIC_REFRESH_ACTIVATION_RECEIPT'
                )
                """.trimIndent(),
            ),
        )
        adminConnection().use { connection ->
            assertEquals(
                "BUILDING",
                queryString(connection, "select state from rag_v2_immutable_owner_bundle_pointers where owner_user_id = 'usr_demo_user'"),
            )
            assertEquals(
                "0",
                queryString(
                    connection,
                    "select count(*) from rag_v2_immutable_owner_bundle_pointers where owner_user_id = 'usr_demo_user' and active_bundle_id is not null",
                ),
            )
            assertEquals(
                "2",
                queryString(
                    connection,
                    "select bundle_version::text from rag_v2_immutable_owner_bundle_pointers where owner_user_id = 'usr_demo_user'",
                ),
            )
            assertEquals(
                "SUPERSEDED",
                queryString(connection, "select state from rag_v2_immutable_bundles where bundle_id = '$OLD_BUNDLE'"),
            )
            assertEquals(
                "1",
                queryString(
                    connection,
                    "select invalidated_owner_bundle_count::text from rag_v2_immutable_activation_receipts where activation_receipt_id = '$PUBLIC_REFRESH_ACTIVATION_RECEIPT'",
                ),
            )
        }
        val directWrite =
            assertThrows<SQLException> {
                DriverManager.getConnection(postgres.jdbcUrl, "decision_rag_admin", RAG_ADMIN_PASSWORD).use { connection ->
                    connection.autoCommit = false
                    connection.createStatement().use { statement ->
                        statement.execute("select set_config('app.rag_admin_maintenance', 'public_base_activation', true)")
                        statement.execute(
                            """
                            update rag_v2_immutable_owner_bundle_pointers
                            set state = 'READY', active_bundle_id = '$OLD_BUNDLE'
                            where owner_user_id = 'usr_demo_user'
                            """.trimIndent(),
                        )
                    }
                }
            }
        assertEquals("42501", directWrite.sqlState)
    }

    @Test
    fun compositeOwnerGraphRejectsCrossOwnerChunkAndBundleReferences() {
        seedEvaluatedPublicComponents()
        seedOwnerDeletionFixtures()

        adminConnection().use { connection ->
            val crossOwnerChunk =
                assertThrows<SQLException> {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            insert into rag_v2_immutable_chunks (
                              chunk_id, source_revision_id, owner_user_id, source_scope, chunk_ordinal,
                              heading_path, locator, canonical_text, canonical_text_sha256, token_count, contains_table
                            ) values (
                              'rag_v2_chk_99999999999999999999999999999999', '$TARGET_SOURCE', 'usr_demo_admin', 'OWNER_PRIVATE', 2,
                              array['foreign'], jsonb_build_object('section', 'foreign'), 'foreign chunk',
                              encode(digest('foreign chunk', 'sha256'), 'hex'), 400, false
                            )
                            """.trimIndent(),
                        )
                    }
                }
            assertEquals("23503", crossOwnerChunk.sqlState)

            val crossOwnerBundle =
                assertThrows<SQLException> {
                    connection.createStatement().use { statement ->
                        statement.execute(
                            """
                            insert into rag_v2_immutable_bundles (
                              bundle_id, owner_user_id, exact30_generation_id, oa112_generation_id,
                              owner_private_generation_id, embedding_profile_id, state, evaluation_status,
                              bundle_hash, evaluated_at
                            ) values (
                              'rgb_99999999999999999999999999999999', 'usr_demo_admin',
                              '$EXACT_GENERATION', '$OA_GENERATION', '$OLD_OWNER_GENERATION',
                              'bge_m3_local_1024_v1', 'EVALUATED', 'PASSED', repeat('9', 64), clock_timestamp()
                            )
                            """.trimIndent(),
                        )
                    }
                }
            assertEquals("23503", crossOwnerBundle.sqlState)
        }
    }

    private fun issueTicket(
        ownerUserId: String,
        ticketId: String,
        operation: String,
    ) {
        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { connection ->
            connection.autoCommit = false
            try {
                connection.prepareStatement("select set_config('app.actor_user_id', ?, false)").use { statement ->
                    statement.setString(1, ownerUserId)
                    statement.execute()
                }
                callSingleRow(
                    connection,
                    """
                    select issue_rag_v2_immutable_import_ticket(
                      '$ownerUserId', '$ticketId', '$operation', 'RAG_V2_OWNER_DOCUMENT_V1'
                    )
                    """.trimIndent(),
                )
                connection.commit()
            } catch (error: Throwable) {
                connection.rollback()
                throw error
            }
        }
    }

    private fun recordConsent(
        ownerUserId: String,
        consentEventId: String,
        action: String,
        disclosureDigest: String,
    ) {
        DriverManager.getConnection(postgres.jdbcUrl, "decision_app", APP_PASSWORD).use { connection ->
            connection.autoCommit = false
            try {
                connection.prepareStatement("select set_config('app.actor_user_id', ?, false)").use { statement ->
                    statement.setString(1, ownerUserId)
                    statement.execute()
                }
                callSingleRow(
                    connection,
                    """
                    select record_rag_v2_immutable_consent(
                      '$ownerUserId', '$consentEventId', '$action', '$disclosureDigest'
                    )
                    """.trimIndent(),
                )
                connection.commit()
            } catch (error: Throwable) {
                connection.rollback()
                throw error
            }
        }
    }

    private fun consumeTicket(
        ownerUserId: String,
        ticketId: String,
        operation: String,
        runId: String,
    ): Boolean =
        callBoolean(
            "decision_rag_writer",
            RAG_WRITER_PASSWORD,
            """
            select consume_rag_v2_immutable_import_ticket(
              '$ownerUserId', '$ticketId', '$operation', 'RAG_V2_OWNER_DOCUMENT_V1', '$runId'
            )
            """.trimIndent(),
        )

    private fun seedOwnerImportRun(
        ownerUserId: String,
        componentGenerationId: String,
        runId: String,
        documentId: String = "doc_owner_import0001",
    ) {
        adminConnection().use { connection ->
            connection.createStatement().use { statement ->
                statement.execute(
                    """
                    insert into rag_v2_immutable_component_generations (
                      component_generation_id, owner_user_id, component_scope, embedding_profile_id,
                      state, evaluation_status, expected_source_count, expected_chunk_count,
                      actual_source_count, actual_chunk_count, generation_hash, manifest_hash
                    ) values (
                      '$componentGenerationId', '$ownerUserId', 'OWNER_PRIVATE', 'bge_m3_local_1024_v1',
                      'STAGING', 'PENDING', 0, 0, 0, 0, repeat('a', 64), repeat('b', 64)
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_materialization_runs (
                      materialization_run_id, owner_user_id, component_generation_id, component_scope, document_id, state
                    ) values ('$runId', '$ownerUserId', '$componentGenerationId', 'OWNER_PRIVATE', '$documentId', 'OPEN')
                    """.trimIndent(),
                )
            }
        }
    }

    private fun seedEvaluatedPublicComponents(
        includeOaSourceCards: Boolean = true,
        mismatchFirstOaSourceCardUrl: Boolean = false,
    ) {
        check(includeOaSourceCards || !mismatchFirstOaSourceCardUrl)
        val cardCanonicalUrl =
            if (mismatchFirstOaSourceCardUrl) {
                "case when source.source_revision_id = 'srv_oa_001' then 'https://mismatch.example.org/oa/001' else source.canonical_https_url end"
            } else {
                "source.canonical_https_url"
            }
        adminConnection().use { connection ->
            connection.createStatement().use { statement ->
                statement.execute(
                    """
                    insert into rag_v2_immutable_component_generations (
                      component_generation_id, owner_user_id, component_scope, embedding_profile_id,
                      state, evaluation_status, expected_source_count, expected_chunk_count,
                      actual_source_count, actual_chunk_count, generation_hash, manifest_hash, evaluated_at
                    ) values
                      ('$EXACT_GENERATION', null, 'EXACT30', 'bge_m3_local_1024_v1', 'EVALUATED', 'PASSED', 30, 30, 30, 30, repeat('1', 64), repeat('2', 64), clock_timestamp()),
                      ('$OA_GENERATION', null, 'OA112', 'bge_m3_local_1024_v1', 'EVALUATED', 'PASSED', 112, 112, 112, 112, repeat('3', 64), repeat('4', 64), clock_timestamp())
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_source_revisions (
                      source_revision_id, document_id, source_id, owner_user_id, source_scope, oa_track_id,
                      reserve_source, source_revision_sha256, raw_content_sha256, normalized_document_ir_sha256, canonical_text_sha256,
                      document_ir, canonical_text, sanitized_display_name, source_locator, canonical_https_url,
                      license_evidence_sha256, access_evidence_sha256, mime_type,
                      machine_fetch_allowed, local_processing_allowed, external_embedding_allowed,
                      external_generation_allowed, external_processing_eligible, parser_version, tokenizer_version
                    )
                    select
                      format('srv_exact_%s', lpad(item::text, 3, '0')),
                      format('doc_exact_%s', lpad(item::text, 11, '0')),
                      format('src_exact_%s', lpad(item::text, 3, '0')),
                      null, 'EXACT30', null, false, lpad(item::text, 64, '0'), repeat('a', 64),
                      encode(digest('exact fixture ' || item, 'sha256'), 'hex'),
                      encode(digest('exact fixture ' || item, 'sha256'), 'hex'),
                      jsonb_build_object(
                        'blocks', jsonb_build_array(jsonb_build_object(
                          'blockType', 'PARAGRAPH', 'locator', jsonb_build_object('section', 'fixture'),
                          'readingOrder', 0, 'ocrConfidence', null, 'text', 'exact fixture ' || item
                        )),
                        'contractId', 'rag-document-ir-v1', 'documentIrVersion', 1,
                        'extractionMode', 'NATIVE', 'languageTags', jsonb_build_array('en'), 'mimeType', 'text/plain',
                        'normalizedContentSha256', encode(digest('exact fixture ' || item, 'sha256'), 'hex'),
                        'parserEvidence', jsonb_build_object(
                          'ocr', jsonb_build_object('backend', 'NOT_USED', 'backendVersion', null, 'modelSha256', null),
                          'parserArtifactSha256', repeat('d', 64), 'parserBackend', 'capstone-safe-local-document-parser', 'parserVersion', 'fixture-v1'
                        ),
                        'rawContentSha256', repeat('a', 64),
                        'safetyClassification', jsonb_build_object(
                          'externalLlmEligible', true, 'piiDetected', false, 'promptInjectionDetected', false, 'secretDetected', false
                        ),
                        'sourceId', format('src_exact_%s', lpad(item::text, 3, '0')),
                        'sourceRevisionId', format('srv_exact_%s', lpad(item::text, 3, '0'))
                      ),
                      'exact fixture ' || item, null,
                      jsonb_build_object('section', 'exact-' || item), 'https://example.org/exact/' || item,
                      null, null, 'text/plain', true, true, true, true, true, 'fixture-v1', 'fixture-tokenizer-v1'
                    from generate_series(1, 30) as item
                    union all
                    select
                      format('srv_oa_%s', lpad(item::text, 3, '0')),
                      format('doc_oa_%s', lpad(item::text, 11, '0')),
                      format('src_oa_%s', lpad(item::text, 3, '0')),
                      null, 'OA112',
                      (array[
                        'MICRO_GAME_INFO_MARKET_DESIGN', 'MACRO_MONETARY_INTERNATIONAL',
                        'PROBABILITY_STATISTICS_OPTIMIZATION', 'ECONOMETRICS_CAUSAL_EVENT_STUDY',
                        'TIME_SERIES_REGIME_VOLATILITY', 'ACCOUNTING_CORPORATE_FINANCE_VALUATION',
                        'ASSET_PRICING_FACTOR_PORTFOLIO', 'FIXED_INCOME_RATES_CREDIT',
                        'DERIVATIVES_STOCHASTIC_NUMERICS', 'MARKET_MICROSTRUCTURE_EXECUTION_LIQUIDITY',
                        'RISK_STRESS_BACKTEST_MODEL_RISK', 'BEHAVIORAL_EFFICIENCY_ANOMALY_CROWDING',
                        'FINANCIAL_ML_PIT_DATA_PROVENANCE', 'CROSS_MARKET_COMMODITIES_POLICY_KOREA'
                      ])[1 + ((item - 1) % 14)],
                      false, lpad((item + 1000)::text, 64, '0'), repeat('b', 64),
                      encode(digest('oa fixture ' || item, 'sha256'), 'hex'),
                      encode(digest('oa fixture ' || item, 'sha256'), 'hex'),
                      jsonb_build_object(
                        'blocks', jsonb_build_array(jsonb_build_object(
                          'blockType', 'PARAGRAPH', 'locator', jsonb_build_object('section', 'fixture'),
                          'readingOrder', 0, 'ocrConfidence', null, 'text', 'oa fixture ' || item
                        )),
                        'contractId', 'rag-document-ir-v1', 'documentIrVersion', 1,
                        'extractionMode', 'NATIVE', 'languageTags', jsonb_build_array('en'), 'mimeType', 'text/plain',
                        'normalizedContentSha256', encode(digest('oa fixture ' || item, 'sha256'), 'hex'),
                        'parserEvidence', jsonb_build_object(
                          'ocr', jsonb_build_object('backend', 'NOT_USED', 'backendVersion', null, 'modelSha256', null),
                          'parserArtifactSha256', repeat('d', 64), 'parserBackend', 'capstone-safe-local-document-parser', 'parserVersion', 'fixture-v1'
                        ),
                        'rawContentSha256', repeat('b', 64),
                        'safetyClassification', jsonb_build_object(
                          'externalLlmEligible', true, 'piiDetected', false, 'promptInjectionDetected', false, 'secretDetected', false
                        ),
                        'sourceId', format('src_oa_%s', lpad(item::text, 3, '0')),
                        'sourceRevisionId', format('srv_oa_%s', lpad(item::text, 3, '0'))
                      ),
                      'oa fixture ' || item, null,
                      jsonb_build_object('section', 'oa-' || item), 'https://example.org/oa/' || item,
                      repeat('d', 64), repeat('e', 64), 'text/plain', true, true, true, true, true, 'fixture-v1', 'fixture-tokenizer-v1'
                    from generate_series(1, 112) as item
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_chunks (
                      chunk_id, source_revision_id, owner_user_id, source_scope, chunk_ordinal,
                      heading_path, locator, canonical_text, canonical_text_sha256, token_count, contains_table
                    )
                    select
                      'rag_v2_chk_' || lpad(row_number() over (order by source_revision_id)::text, 32, '0'),
                      source_revision_id, null, source_scope, 1, array['fixture'],
                      jsonb_build_object('section', 'fixture'), 'chunk ' || source_revision_id,
                      encode(digest('chunk ' || source_revision_id, 'sha256'), 'hex'), 400, false
                    from rag_v2_immutable_source_revisions
                    where source_scope in ('EXACT30', 'OA112')
                    """.trimIndent(),
                )
                if (includeOaSourceCards) {
                    statement.execute(
                        """
                        insert into rag_v2_immutable_oa_source_cards (
                          source_revision_id, source_scope, source_id, source_card, source_card_sha256,
                          active_oa112_eligible, title, authors, canonical_https_url, canonical_https_url_sha256,
                          identifier_scheme, identifier_value, revision, revision_date, raw_content_sha256,
                          mime_type, license_evidence_sha256, access_evidence_sha256, access_checked_at,
                          access_verification_state, machine_fetch_allowed, local_processing_allowed,
                          external_embedding_allowed, external_generation_allowed
                        )
                        select
                          source.source_revision_id, source.source_scope, source.source_id,
                          card.source_card, encode(digest(card.source_card::text, 'sha256'), 'hex'),
                          true, 'OA fixture ' || source.source_id, array['Fixture author ' || source.source_id],
                          $cardCanonicalUrl, encode(digest($cardCanonicalUrl, 'sha256'), 'hex'),
                          'DOI', '10.0000/' || source.source_id, 'fixture-r1', date '2026-08-03', source.raw_content_sha256,
                          source.mime_type, source.license_evidence_sha256, source.access_evidence_sha256, '2026-08-03T00:00:00Z',
                          'VERIFIED', source.machine_fetch_allowed, source.local_processing_allowed,
                          source.external_embedding_allowed, source.external_generation_allowed
                        from rag_v2_immutable_source_revisions as source
                        cross join lateral (
                          select jsonb_build_object(
                            'accessEvidence', jsonb_build_object(
                              'accessCheckedAt', '2026-08-03T00:00:00Z',
                              'accessEvidenceDigest', source.access_evidence_sha256,
                              'verificationState', 'VERIFIED'
                            ),
                            'activeOa112Eligible', true,
                            'authors', jsonb_build_array('Fixture author ' || source.source_id),
                            'canonicalUrl', $cardCanonicalUrl,
                            'canonicalUrlSha256', encode(digest($cardCanonicalUrl, 'sha256'), 'hex'),
                            'contractId', 'rag-source-card-v4',
                            'identifier', jsonb_build_object('scheme', 'DOI', 'value', '10.0000/' || source.source_id),
                            'licenseEvidenceDigest', source.license_evidence_sha256,
                            'mimeType', source.mime_type,
                            'permissions', jsonb_build_object(
                              'machineFetchAllowed', source.machine_fetch_allowed,
                              'localProcessingAllowed', source.local_processing_allowed,
                              'externalEmbeddingAllowed', source.external_embedding_allowed,
                              'externalGenerationAllowed', source.external_generation_allowed
                            ),
                            'rawContentSha256', source.raw_content_sha256,
                            'revision', 'fixture-r1',
                            'revisionDate', '2026-08-03',
                            'schemaVersion', 4,
                            'sourceId', source.source_id,
                            'sourceKind', 'OPEN_ACCESS_DOCUMENT',
                            'title', 'OA fixture ' || source.source_id
                          ) as source_card
                        ) as card
                        where source.source_scope = 'OA112'
                        """.trimIndent(),
                    )
                }
                statement.execute(
                    """
                    insert into rag_v2_immutable_generation_memberships (
                      component_generation_id, chunk_id, source_revision_id, owner_user_id, component_scope, ordinal
                    )
                    select
                      case source_scope when 'EXACT30' then '$EXACT_GENERATION' else '$OA_GENERATION' end,
                      chunk_id, source_revision_id, null, source_scope,
                      row_number() over (partition by source_scope order by source_revision_id)
                    from rag_v2_immutable_chunks
                    where source_scope in ('EXACT30', 'OA112')
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_generation_embeddings (
                      component_generation_id, chunk_id, owner_user_id, component_scope, embedding_profile_id,
                      embedding_input_hash, context_set_hash, embedding
                    )
                    select
                      membership.component_generation_id, membership.chunk_id, null, membership.component_scope,
                      'bge_m3_local_1024_v1', repeat('c', 64), null,
                      (array[1::real] || array_fill(0::real, array[1023]))::vector
                    from rag_v2_immutable_generation_memberships as membership
                    """.trimIndent(),
                )
            }
        }
    }

    private fun seedRefreshPublicComponents() {
        adminConnection().use { connection ->
            connection.createStatement().use { statement ->
                statement.execute(
                    """
                    insert into rag_v2_immutable_component_generations (
                      component_generation_id, owner_user_id, component_scope, embedding_profile_id,
                      state, evaluation_status, expected_source_count, expected_chunk_count,
                      actual_source_count, actual_chunk_count, generation_hash, manifest_hash, evaluated_at
                    ) values
                      ('$REFRESH_EXACT_GENERATION', null, 'EXACT30', 'bge_m3_local_1024_v1', 'EVALUATED', 'PASSED', 30, 30, 30, 30, repeat('6', 64), repeat('7', 64), clock_timestamp()),
                      ('$REFRESH_OA_GENERATION', null, 'OA112', 'bge_m3_local_1024_v1', 'EVALUATED', 'PASSED', 112, 112, 112, 112, repeat('7', 64), repeat('8', 64), clock_timestamp())
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_generation_memberships (
                      component_generation_id, chunk_id, source_revision_id, owner_user_id, component_scope, ordinal
                    )
                    select
                      case source_scope when 'EXACT30' then '$REFRESH_EXACT_GENERATION' else '$REFRESH_OA_GENERATION' end,
                      chunk_id, source_revision_id, null, source_scope,
                      row_number() over (partition by source_scope order by source_revision_id)
                    from rag_v2_immutable_chunks
                    where source_scope in ('EXACT30', 'OA112')
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_generation_embeddings (
                      component_generation_id, chunk_id, owner_user_id, component_scope, embedding_profile_id,
                      embedding_input_hash, context_set_hash, embedding
                    )
                    select
                      membership.component_generation_id, membership.chunk_id, null, membership.component_scope,
                      'bge_m3_local_1024_v1', repeat('c', 64), null,
                      (array[1::real] || array_fill(0::real, array[1023]))::vector
                    from rag_v2_immutable_generation_memberships as membership
                    where membership.component_generation_id in ('$REFRESH_EXACT_GENERATION', '$REFRESH_OA_GENERATION')
                    """.trimIndent(),
                )
            }
        }
    }

    private fun seedOwnerStagingDocumentArtifacts() {
        adminConnection().use { connection ->
            connection.createStatement().use { statement ->
                statement.execute(
                    """
                    insert into rag_v2_immutable_source_revisions (
                      source_revision_id, document_id, source_id, owner_user_id, source_scope, oa_track_id,
                      reserve_source, source_revision_sha256, raw_content_sha256, normalized_document_ir_sha256, canonical_text_sha256,
                      document_ir, canonical_text, sanitized_display_name, source_locator, canonical_https_url,
                      license_evidence_sha256, access_evidence_sha256, mime_type,
                      machine_fetch_allowed, local_processing_allowed, external_embedding_allowed,
                      external_generation_allowed, external_processing_eligible, parser_version, tokenizer_version
                    ) values (
                      '$STAGING_SOURCE', '$STAGING_DOCUMENT', 'src_owner_staging', 'usr_demo_user', 'OWNER_PRIVATE', null,
                      false, repeat('8', 64), repeat('a', 64), encode(digest('staging fixture', 'sha256'), 'hex'),
                      encode(digest('staging fixture', 'sha256'), 'hex'),
                      jsonb_build_object(
                        'blocks', jsonb_build_array(jsonb_build_object(
                          'blockType', 'PARAGRAPH', 'locator', jsonb_build_object('section', 'staging'),
                          'readingOrder', 0, 'ocrConfidence', null, 'text', 'staging fixture'
                        )),
                        'contractId', 'rag-document-ir-v1', 'documentIrVersion', 1,
                        'extractionMode', 'NATIVE', 'languageTags', jsonb_build_array('en'), 'mimeType', 'text/plain',
                        'normalizedContentSha256', encode(digest('staging fixture', 'sha256'), 'hex'),
                        'parserEvidence', jsonb_build_object(
                          'ocr', jsonb_build_object('backend', 'NOT_USED', 'backendVersion', null, 'modelSha256', null),
                          'parserArtifactSha256', repeat('d', 64), 'parserBackend', 'capstone-safe-local-document-parser', 'parserVersion', 'fixture-v1'
                        ),
                        'rawContentSha256', repeat('a', 64),
                        'safetyClassification', jsonb_build_object(
                          'externalLlmEligible', false, 'piiDetected', false, 'promptInjectionDetected', false, 'secretDetected', false
                        ),
                        'sourceId', 'src_owner_staging', 'sourceRevisionId', '$STAGING_SOURCE'
                      ),
                      'staging fixture', 'staging-fixture.txt',
                      jsonb_build_object('section', 'staging'), null, null, null, 'text/plain', false, true, false, false, false,
                      'fixture-v1', 'fixture-tokenizer-v1'
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_chunks (
                      chunk_id, source_revision_id, owner_user_id, source_scope, chunk_ordinal,
                      heading_path, locator, canonical_text, canonical_text_sha256, token_count, contains_table
                    ) values (
                      '$STAGING_CHUNK', '$STAGING_SOURCE', 'usr_demo_user', 'OWNER_PRIVATE', 1,
                      array['staging'], jsonb_build_object('section', 'staging'), 'staging chunk',
                      encode(digest('staging chunk', 'sha256'), 'hex'), 400, false
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_generation_memberships (
                      component_generation_id, chunk_id, source_revision_id, owner_user_id, component_scope, ordinal
                    ) values ('$OWNER_IMPORT_GENERATION', '$STAGING_CHUNK', '$STAGING_SOURCE', 'usr_demo_user', 'OWNER_PRIVATE', 1)
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_generation_embeddings (
                      component_generation_id, chunk_id, owner_user_id, component_scope, embedding_profile_id,
                      embedding_input_hash, context_set_hash, embedding
                    ) values (
                      '$OWNER_IMPORT_GENERATION', '$STAGING_CHUNK', 'usr_demo_user', 'OWNER_PRIVATE',
                      'bge_m3_local_1024_v1', repeat('c', 64), null,
                      (array[1::real] || array_fill(0::real, array[1023]))::vector
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_embedding_cache (
                      cache_id, owner_user_id, source_revision_id, chunk_id, source_scope,
                      embedding_profile_id, embedding_input_hash, context_set_hash, embedding
                    ) values (
                      '$STAGING_CACHE', 'usr_demo_user', '$STAGING_SOURCE', '$STAGING_CHUNK', 'OWNER_PRIVATE',
                      'bge_m3_local_1024_v1', repeat('c', 64), null,
                      (array[1::real] || array_fill(0::real, array[1023]))::vector
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_source_receipts (
                      receipt_id, materialization_run_id, owner_user_id, source_scope, source_revision_id,
                      raw_content_sha256, canonical_text_sha256, reuse_state
                    ) values (
                      '$STAGING_SOURCE_RECEIPT', '$OWNER_IMPORT_RUN', 'usr_demo_user', 'OWNER_PRIVATE', '$STAGING_SOURCE',
                      repeat('a', 64), encode(digest('staging fixture', 'sha256'), 'hex'), 'NEW'
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_chunk_receipts (
                      receipt_id, materialization_run_id, owner_user_id, source_scope, source_revision_id, chunk_id,
                      canonical_text_sha256, reuse_state
                    ) values (
                      '$STAGING_CHUNK_RECEIPT', '$OWNER_IMPORT_RUN', 'usr_demo_user', 'OWNER_PRIVATE', '$STAGING_SOURCE', '$STAGING_CHUNK',
                      encode(digest('staging chunk', 'sha256'), 'hex'), 'NEW'
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_embedding_receipts (
                      receipt_id, materialization_run_id, owner_user_id, source_scope, component_generation_id, chunk_id,
                      embedding_profile_id, embedding_input_hash, context_set_hash, reuse_state
                    ) values (
                      '$STAGING_EMBEDDING_RECEIPT', '$OWNER_IMPORT_RUN', 'usr_demo_user', 'OWNER_PRIVATE',
                      '$OWNER_IMPORT_GENERATION', '$STAGING_CHUNK',
                      'bge_m3_local_1024_v1', repeat('c', 64), null, 'NEW'
                    )
                    """.trimIndent(),
                )
            }
        }
    }

    private fun seedOwnerDeletionFixtures() {
        adminConnection().use { connection ->
            connection.createStatement().use { statement ->
                statement.execute(
                    """
                    insert into rag_v2_immutable_component_generations (
                      component_generation_id, owner_user_id, component_scope, embedding_profile_id,
                      state, evaluation_status, expected_source_count, expected_chunk_count,
                      actual_source_count, actual_chunk_count, generation_hash, manifest_hash, evaluated_at
                    ) values
                      ('$OLD_OWNER_GENERATION', 'usr_demo_user', 'OWNER_PRIVATE', 'bge_m3_local_1024_v1', 'EVALUATED', 'PASSED', 1, 1, 1, 1, repeat('5', 64), repeat('6', 64), clock_timestamp()),
                      ('$REPLACEMENT_OWNER_GENERATION', 'usr_demo_user', 'OWNER_PRIVATE', 'bge_m3_local_1024_v1', 'EVALUATED', 'PASSED', 0, 0, 0, 0, repeat('7', 64), repeat('8', 64), clock_timestamp()),
                      ('$RACE_OWNER_GENERATION', 'usr_demo_user', 'OWNER_PRIVATE', 'bge_m3_local_1024_v1', 'EVALUATED', 'PASSED', 0, 0, 0, 0, repeat('8', 64), repeat('9', 64), clock_timestamp())
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_source_revisions (
                      source_revision_id, document_id, source_id, owner_user_id, source_scope, oa_track_id,
                      reserve_source, source_revision_sha256, raw_content_sha256, normalized_document_ir_sha256, canonical_text_sha256,
                      document_ir, canonical_text, sanitized_display_name, source_locator, canonical_https_url,
                      license_evidence_sha256, access_evidence_sha256, mime_type,
                      machine_fetch_allowed, local_processing_allowed, external_embedding_allowed,
                      external_generation_allowed, external_processing_eligible, parser_version, tokenizer_version
                    ) values (
                      '$TARGET_SOURCE', '$TARGET_DOCUMENT', 'src_owner_target', 'usr_demo_user', 'OWNER_PRIVATE', null,
                      false, repeat('9', 64), repeat('a', 64), encode(digest('owner fixture', 'sha256'), 'hex'),
                      encode(digest('owner fixture', 'sha256'), 'hex'),
                      jsonb_build_object(
                        'blocks', jsonb_build_array(jsonb_build_object(
                          'blockType', 'PARAGRAPH', 'locator', jsonb_build_object('section', 'fixture'),
                          'readingOrder', 0, 'ocrConfidence', null, 'text', 'owner fixture'
                        )),
                        'contractId', 'rag-document-ir-v1', 'documentIrVersion', 1,
                        'extractionMode', 'NATIVE', 'languageTags', jsonb_build_array('en'), 'mimeType', 'text/plain',
                        'normalizedContentSha256', encode(digest('owner fixture', 'sha256'), 'hex'),
                        'parserEvidence', jsonb_build_object(
                          'ocr', jsonb_build_object('backend', 'NOT_USED', 'backendVersion', null, 'modelSha256', null),
                          'parserArtifactSha256', repeat('d', 64), 'parserBackend', 'capstone-safe-local-document-parser', 'parserVersion', 'fixture-v1'
                        ),
                        'rawContentSha256', repeat('a', 64),
                        'safetyClassification', jsonb_build_object(
                          'externalLlmEligible', false, 'piiDetected', false, 'promptInjectionDetected', false, 'secretDetected', false
                        ),
                        'sourceId', 'src_owner_target', 'sourceRevisionId', '$TARGET_SOURCE'
                      ),
                      'owner fixture', 'owner-fixture.txt',
                      jsonb_build_object('section', 'owner'), null, null, null, 'text/plain', false, true, false, false, false,
                      'fixture-v1', 'fixture-tokenizer-v1'
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_chunks (
                      chunk_id, source_revision_id, owner_user_id, source_scope, chunk_ordinal,
                      heading_path, locator, canonical_text, canonical_text_sha256, token_count, contains_table
                    ) values (
                      '$TARGET_CHUNK', '$TARGET_SOURCE', 'usr_demo_user', 'OWNER_PRIVATE', 1,
                      array['owner'], jsonb_build_object('section', 'owner'), 'owner chunk', encode(digest('owner chunk', 'sha256'), 'hex'), 400, false
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_generation_memberships (
                      component_generation_id, chunk_id, source_revision_id, owner_user_id, component_scope, ordinal
                    ) values ('$OLD_OWNER_GENERATION', '$TARGET_CHUNK', '$TARGET_SOURCE', 'usr_demo_user', 'OWNER_PRIVATE', 1)
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_generation_embeddings (
                      component_generation_id, chunk_id, owner_user_id, component_scope, embedding_profile_id,
                      embedding_input_hash, context_set_hash, embedding
                    ) values (
                      '$OLD_OWNER_GENERATION', '$TARGET_CHUNK', 'usr_demo_user', 'OWNER_PRIVATE',
                      'bge_m3_local_1024_v1', repeat('c', 64), null,
                      (array[1::real] || array_fill(0::real, array[1023]))::vector
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_materialization_runs (
                      materialization_run_id, owner_user_id, component_generation_id, component_scope, document_id, state, completed_at
                    ) values (
                      '$DELETE_OWNER_RUN', 'usr_demo_user', '$OLD_OWNER_GENERATION', 'OWNER_PRIVATE', '$TARGET_DOCUMENT', 'EVALUATED', clock_timestamp()
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_embedding_cache (
                      cache_id, owner_user_id, source_revision_id, chunk_id, source_scope,
                      embedding_profile_id, embedding_input_hash, context_set_hash, embedding
                    ) values (
                      '$DELETE_CACHE', 'usr_demo_user', '$TARGET_SOURCE', '$TARGET_CHUNK', 'OWNER_PRIVATE',
                      'bge_m3_local_1024_v1', repeat('c', 64), null,
                      (array[1::real] || array_fill(0::real, array[1023]))::vector
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_source_receipts (
                      receipt_id, materialization_run_id, owner_user_id, source_scope, source_revision_id,
                      raw_content_sha256, canonical_text_sha256, reuse_state
                    ) values (
                      '$DELETE_SOURCE_RECEIPT', '$DELETE_OWNER_RUN', 'usr_demo_user', 'OWNER_PRIVATE', '$TARGET_SOURCE',
                      repeat('a', 64), encode(digest('owner fixture', 'sha256'), 'hex'), 'NEW'
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_chunk_receipts (
                      receipt_id, materialization_run_id, owner_user_id, source_scope, source_revision_id, chunk_id,
                      canonical_text_sha256, reuse_state
                    ) values (
                      '$DELETE_CHUNK_RECEIPT', '$DELETE_OWNER_RUN', 'usr_demo_user', 'OWNER_PRIVATE', '$TARGET_SOURCE', '$TARGET_CHUNK',
                      encode(digest('owner chunk', 'sha256'), 'hex'), 'NEW'
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_embedding_receipts (
                      receipt_id, materialization_run_id, owner_user_id, source_scope, component_generation_id, chunk_id,
                      embedding_profile_id, embedding_input_hash, context_set_hash, reuse_state
                    ) values (
                      '$DELETE_EMBEDDING_RECEIPT', '$DELETE_OWNER_RUN', 'usr_demo_user', 'OWNER_PRIVATE', '$OLD_OWNER_GENERATION', '$TARGET_CHUNK',
                      'bge_m3_local_1024_v1', repeat('c', 64), null, 'NEW'
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_bundles (
                      bundle_id, owner_user_id, exact30_generation_id, oa112_generation_id,
                      owner_private_generation_id, embedding_profile_id, state, evaluation_status,
                      bundle_hash, evaluated_at
                    ) values
                      ('$OLD_BUNDLE', 'usr_demo_user', '$EXACT_GENERATION', '$OA_GENERATION', '$OLD_OWNER_GENERATION', 'bge_m3_local_1024_v1', 'EVALUATED', 'PASSED', repeat('d', 64), clock_timestamp()),
                      ('$BAD_BUNDLE', 'usr_demo_user', '$EXACT_GENERATION', '$OA_GENERATION', '$OLD_OWNER_GENERATION', 'bge_m3_local_1024_v1', 'EVALUATED', 'PASSED', repeat('e', 64), clock_timestamp()),
                      ('$REPLACEMENT_BUNDLE', 'usr_demo_user', '$EXACT_GENERATION', '$OA_GENERATION', '$REPLACEMENT_OWNER_GENERATION', 'bge_m3_local_1024_v1', 'EVALUATED', 'PASSED', repeat('f', 64), clock_timestamp()),
                      ('$RACE_BUNDLE', 'usr_demo_user', '$EXACT_GENERATION', '$OA_GENERATION', '$RACE_OWNER_GENERATION', 'bge_m3_local_1024_v1', 'EVALUATED', 'PASSED', repeat('1', 64), clock_timestamp())
                    """.trimIndent(),
                )
            }
        }
    }

    private fun seedSurvivingOwnerDocument() {
        adminConnection().use { connection ->
            connection.createStatement().use { statement ->
                statement.execute(
                    """
                    update rag_v2_immutable_component_generations
                    set expected_source_count = 2,
                        expected_chunk_count = 2,
                        actual_source_count = 2,
                        actual_chunk_count = 2
                    where component_generation_id = '$OLD_OWNER_GENERATION'
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_source_revisions (
                      source_revision_id, document_id, source_id, owner_user_id, source_scope, oa_track_id,
                      reserve_source, source_revision_sha256, raw_content_sha256, normalized_document_ir_sha256, canonical_text_sha256,
                      document_ir, canonical_text, sanitized_display_name, source_locator, canonical_https_url,
                      license_evidence_sha256, access_evidence_sha256, mime_type,
                      machine_fetch_allowed, local_processing_allowed, external_embedding_allowed,
                      external_generation_allowed, external_processing_eligible, parser_version, tokenizer_version
                    ) values (
                      '$SURVIVING_SOURCE', '$SURVIVING_DOCUMENT', 'src_owner_survivor', 'usr_demo_user', 'OWNER_PRIVATE', null,
                      false, repeat('7', 64), repeat('b', 64), encode(digest('surviving fixture', 'sha256'), 'hex'),
                      encode(digest('surviving fixture', 'sha256'), 'hex'),
                      jsonb_build_object(
                        'blocks', jsonb_build_array(jsonb_build_object(
                          'blockType', 'PARAGRAPH', 'locator', jsonb_build_object('section', 'survivor'),
                          'readingOrder', 0, 'ocrConfidence', null, 'text', 'surviving fixture'
                        )),
                        'contractId', 'rag-document-ir-v1', 'documentIrVersion', 1,
                        'extractionMode', 'NATIVE', 'languageTags', jsonb_build_array('en'), 'mimeType', 'text/plain',
                        'normalizedContentSha256', encode(digest('surviving fixture', 'sha256'), 'hex'),
                        'parserEvidence', jsonb_build_object(
                          'ocr', jsonb_build_object('backend', 'NOT_USED', 'backendVersion', null, 'modelSha256', null),
                          'parserArtifactSha256', repeat('d', 64), 'parserBackend', 'capstone-safe-local-document-parser', 'parserVersion', 'fixture-v1'
                        ),
                        'rawContentSha256', repeat('b', 64),
                        'safetyClassification', jsonb_build_object(
                          'externalLlmEligible', false, 'piiDetected', false, 'promptInjectionDetected', false, 'secretDetected', false
                        ),
                        'sourceId', 'src_owner_survivor', 'sourceRevisionId', '$SURVIVING_SOURCE'
                      ),
                      'surviving fixture', 'surviving-fixture.txt',
                      jsonb_build_object('section', 'survivor'), null, null, null, 'text/plain', false, true, false, false, false,
                      'fixture-v1', 'fixture-tokenizer-v1'
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_chunks (
                      chunk_id, source_revision_id, owner_user_id, source_scope, chunk_ordinal,
                      heading_path, locator, canonical_text, canonical_text_sha256, token_count, contains_table
                    ) values (
                      '$SURVIVING_CHUNK', '$SURVIVING_SOURCE', 'usr_demo_user', 'OWNER_PRIVATE', 1,
                      array['survivor'], jsonb_build_object('section', 'survivor'), 'surviving chunk',
                      encode(digest('surviving chunk', 'sha256'), 'hex'), 400, false
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_generation_memberships (
                      component_generation_id, chunk_id, source_revision_id, owner_user_id, component_scope, ordinal
                    ) values ('$OLD_OWNER_GENERATION', '$SURVIVING_CHUNK', '$SURVIVING_SOURCE', 'usr_demo_user', 'OWNER_PRIVATE', 2)
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_generation_embeddings (
                      component_generation_id, chunk_id, owner_user_id, component_scope, embedding_profile_id,
                      embedding_input_hash, context_set_hash, embedding
                    ) values (
                      '$OLD_OWNER_GENERATION', '$SURVIVING_CHUNK', 'usr_demo_user', 'OWNER_PRIVATE',
                      'bge_m3_local_1024_v1', repeat('c', 64), null,
                      (array[1::real] || array_fill(0::real, array[1023]))::vector
                    )
                    """.trimIndent(),
                )
            }
        }
    }

    private fun seedReplacementWithSurvivingDocument() {
        adminConnection().use { connection ->
            connection.createStatement().use { statement ->
                statement.execute(
                    """
                    update rag_v2_immutable_component_generations
                    set expected_source_count = 1,
                        expected_chunk_count = 1,
                        actual_source_count = 1,
                        actual_chunk_count = 1
                    where component_generation_id = '$REPLACEMENT_OWNER_GENERATION'
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_generation_memberships (
                      component_generation_id, chunk_id, source_revision_id, owner_user_id, component_scope, ordinal
                    ) values (
                      '$REPLACEMENT_OWNER_GENERATION', '$SURVIVING_CHUNK', '$SURVIVING_SOURCE',
                      'usr_demo_user', 'OWNER_PRIVATE', 1
                    )
                    """.trimIndent(),
                )
                statement.execute(
                    """
                    insert into rag_v2_immutable_generation_embeddings (
                      component_generation_id, chunk_id, owner_user_id, component_scope, embedding_profile_id,
                      embedding_input_hash, context_set_hash, embedding
                    ) values (
                      '$REPLACEMENT_OWNER_GENERATION', '$SURVIVING_CHUNK', 'usr_demo_user', 'OWNER_PRIVATE',
                      'bge_m3_local_1024_v1', repeat('c', 64), null,
                      (array[1::real] || array_fill(0::real, array[1023]))::vector
                    )
                    """.trimIndent(),
                )
            }
        }
    }

    private fun callLong(
        role: String,
        password: String,
        sql: String,
    ): Long =
        DriverManager.getConnection(postgres.jdbcUrl, role, password).use { connection ->
            callSingleRow(connection, sql).toLong()
        }

    private fun callBoolean(
        role: String,
        password: String,
        sql: String,
    ): Boolean =
        DriverManager.getConnection(postgres.jdbcUrl, role, password).use { connection ->
            callBoolean(connection, sql)
        }

    private fun callBoolean(
        connection: Connection,
        sql: String,
    ): Boolean =
        connection.createStatement().use { statement ->
            statement.executeQuery(sql).use { result ->
                check(result.next())
                result.getBoolean(1)
            }
        }

    private fun callSingleRow(
        connection: Connection,
        sql: String,
    ): String =
        connection.createStatement().use { statement ->
            statement.executeQuery(sql).use { result ->
                check(result.next())
                return result.getString(1)
            }
        }

    private fun queryString(
        connection: Connection,
        sql: String,
    ): String = callSingleRow(connection, sql)

    private fun hasTablePrivilege(
        connection: Connection,
        role: String,
        table: String,
        privilege: String,
    ): Boolean =
        connection.prepareStatement("select has_table_privilege(?, 'public.' || ?, ?)").use { statement ->
            statement.setString(1, role)
            statement.setString(2, table)
            statement.setString(3, privilege)
            statement.executeQuery().use { result ->
                check(result.next())
                result.getBoolean(1)
            }
        }

    private fun hasFunctionPrivilege(
        connection: Connection,
        role: String,
        signature: String,
    ): Boolean =
        connection.prepareStatement("select has_function_privilege(?, ?, 'EXECUTE')").use { statement ->
            statement.setString(1, role)
            statement.setString(2, signature)
            statement.executeQuery().use { result ->
                check(result.next())
                result.getBoolean(1)
            }
        }

    private fun assertPermissionDenied(
        role: String,
        password: String,
        sql: String,
    ) {
        val exception =
            assertThrows<SQLException> {
                DriverManager.getConnection(postgres.jdbcUrl, role, password).use { connection ->
                    connection.createStatement().use { statement -> statement.execute(sql) }
                }
            }
        assertEquals("42501", exception.sqlState)
    }

    private fun awaitAdvisoryLockWait(backendPid: Int) {
        repeat(100) {
            adminConnection().use { connection ->
                if (queryString(connection, "select coalesce(wait_event_type, '') from pg_stat_activity where pid = $backendPid") ==
                    "Lock"
                ) {
                    return
                }
            }
            Thread.sleep(25)
        }
        error("concurrent stale resume did not wait for the owner-document advisory lock")
    }

    private fun adminConnection(): Connection = DriverManager.getConnection(postgres.jdbcUrl, postgres.username, postgres.password)

    companion object {
        private const val APP_PASSWORD = "app-test"
        private const val RAG_WRITER_PASSWORD = "rag-writer-test"
        private const val RAG_ADMIN_PASSWORD = "rag-admin-test"
        private const val FLYWAY_PASSWORD = "flyway-test"
        private const val EXACT_GENERATION = "rgr_11111111111111111111111111111111"
        private const val OA_GENERATION = "rgr_22222222222222222222222222222222"
        private const val REFRESH_EXACT_GENERATION = "rgr_66666666666666666666666666666666"
        private const val REFRESH_OA_GENERATION = "rgr_77777777777777777777777777777777"
        private const val OLD_OWNER_GENERATION = "rgr_33333333333333333333333333333333"
        private const val REPLACEMENT_OWNER_GENERATION = "rgr_44444444444444444444444444444444"
        private const val RACE_OWNER_GENERATION = "rgr_55555555555555555555555555555555"
        private const val OLD_BUNDLE = "rgb_11111111111111111111111111111111"
        private const val BAD_BUNDLE = "rgb_22222222222222222222222222222222"
        private const val REPLACEMENT_BUNDLE = "rgb_33333333333333333333333333333333"
        private const val RACE_BUNDLE = "rgb_44444444444444444444444444444444"
        private const val OWNER_IMPORT_GENERATION = "rgr_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        private const val OWNER_SECOND_IMPORT_GENERATION = "rgr_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        private const val OTHER_OWNER_IMPORT_GENERATION = "rgr_cccccccccccccccccccccccccccccccc"
        private const val OWNER_IMPORT_RUN = "rgr_run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        private const val OWNER_SECOND_IMPORT_RUN = "rgr_run_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        private const val OTHER_OWNER_IMPORT_RUN = "rgr_run_cccccccccccccccccccccccccccccccc"
        private const val TARGET_SOURCE = "srv_owner_target"
        private const val TARGET_DOCUMENT = "doc_owner_target00001"
        private const val TARGET_CHUNK = "rag_v2_chk_ffffffffffffffffffffffffffffffff"
        private const val SURVIVING_SOURCE = "srv_owner_survivor"
        private const val SURVIVING_DOCUMENT = "doc_owner_survivor01"
        private const val SURVIVING_CHUNK = "rag_v2_chk_abababababababababababababababab"
        private const val DELETE_OWNER_RUN = "rgr_run_dddddddddddddddddddddddddddddddd"
        private const val DELETE_CACHE = "rgr_cache_dddddddddddddddddddddddddddddddd"
        private const val DELETE_SOURCE_RECEIPT = "rgr_src_dddddddddddddddddddddddddddddddd"
        private const val DELETE_CHUNK_RECEIPT = "rgr_chk_dddddddddddddddddddddddddddddddd"
        private const val DELETE_EMBEDDING_RECEIPT = "rgr_emb_dddddddddddddddddddddddddddddddd"
        private const val STAGING_SOURCE = "srv_owner_staging"
        private const val STAGING_DOCUMENT = "doc_owner_staging001"
        private const val ABSENT_DOCUMENT = "doc_owner_absent0001"
        private const val STAGING_CHUNK = "rag_v2_chk_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        private const val STAGING_CACHE = "rgr_cache_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        private const val STAGING_SOURCE_RECEIPT = "rgr_src_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        private const val STAGING_CHUNK_RECEIPT = "rgr_chk_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        private const val STAGING_EMBEDDING_RECEIPT = "rgr_emb_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        private const val STAGING_TICKET = "rti_33333333333333333333333333333333"
        private const val STALE_RESUME_RUN = "rgr_run_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        private const val PUBLIC_ACTIVATION_RECEIPT = "rgr_act_11111111111111111111111111111111"
        private const val PUBLIC_REFRESH_ACTIVATION_RECEIPT = "rgr_act_77777777777777777777777777777777"
        private const val OLD_OWNER_ACTIVATION_RECEIPT = "rgr_act_22222222222222222222222222222222"
        private const val BAD_ACTIVATION_RECEIPT = "rgr_act_33333333333333333333333333333333"
        private const val DELETE_ACTIVATION_RECEIPT = "rgr_act_44444444444444444444444444444444"
        private const val RACE_ACTIVATION_RECEIPT = "rgr_act_55555555555555555555555555555555"
        private const val RACE_SECOND_ACTIVATION_RECEIPT = "rgr_act_66666666666666666666666666666666"
        private const val BAD_DELETION_RECEIPT = "rgr_del_11111111111111111111111111111111"
        private const val DELETE_RECEIPT = "rgr_del_22222222222222222222222222222222"
        private const val STAGING_DELETE_RECEIPT = "rgr_del_33333333333333333333333333333333"
        private const val UNMATERIALIZED_DELETE_RECEIPT = "rgr_del_44444444444444444444444444444444"
        private const val ABSENT_DELETE_RECEIPT = "rgr_del_55555555555555555555555555555555"
        private val postgresImage =
            DockerImageName
                .parse(
                    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb",
                ).asCompatibleSubstituteFor("postgres")

        @Container
        @JvmStatic
        val postgres: PostgreSQLContainer =
            PostgreSQLContainer(postgresImage)
                .withDatabaseName("decision_rag_v2_immutable")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")
    }
}
