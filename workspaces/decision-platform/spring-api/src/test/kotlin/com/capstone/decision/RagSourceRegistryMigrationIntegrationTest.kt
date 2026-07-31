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
                    )
                assertThat(queryStrings(connection, normalizedTableQuery))
                    .containsAll(expectedTables)
                assertThat(queryString(connection, "select max(version::integer) from flyway_schema_history where success"))
                    .isEqualTo("19")

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
                assertThat(
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
                    ),
                ).allMatch { it == "search_path=pg_catalog, public, pg_temp" }
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
            PostgreSQLContainer(postgresImage)
                .withDatabaseName("decision_s4_migration_admin")
                .withUsername("decision")
                .withPassword("decision")
                .withInitScript("db/test-init-calendar-roles.sql")
    }
}
