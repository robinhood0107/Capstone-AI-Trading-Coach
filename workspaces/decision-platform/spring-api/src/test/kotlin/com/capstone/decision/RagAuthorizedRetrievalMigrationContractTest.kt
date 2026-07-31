package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagAuthorizedRetrievalMigrationContractTest {
    private val migrationPath =
        Path.of(
            "src/main/resources/db/migration/" +
                "V19__s4_3_authorized_hybrid_retrieval.sql",
        )
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `V19 creates opaque owner-session claim and three independently scoped channels`() {
        assertThat(migration).contains("CREATE TABLE rag_retrieval_scope_claims")
        assertThat(migration).contains("CREATE TABLE rag_source_exact_identifiers")
        assertThat(migration).contains("CREATE FUNCTION create_rag_retrieval_scope_claim")
        listOf(
            "search_authorized_rag_exact",
            "search_authorized_rag_lexical",
            "search_authorized_rag_dense",
        ).forEach { function ->
            assertThat(migration).contains("CREATE FUNCTION $function")
            assertThat(migration).contains("ON FUNCTION $function")
        }
        assertThat(migration).contains("decision_app")
        assertThat(migration).contains("decision_rag_query")
        assertThat(migration).contains("active_generation_id")
        assertThat(migration).contains("effective_profile_id")
        assertThat(migration).contains("owner_user_id")
        assertThat(migration).contains("session_id")
        assertThat(migration).contains("expires_at")
    }

    @Test
    fun `V19 fixes channel and output bounds without dynamic SQL`() {
        assertThat(migration).contains("LIMIT 30")
        assertThat(migration).contains("vector(1024)")
        assertThat(migration).contains("vector_dims(p_query_embedding) <> 1024")
        assertThat(migration).contains("<=>")
        assertThat(migration).contains("similarity(")
        assertThat(migration).contains("gin_trgm_ops")
        assertThat(migration).contains("vector_cosine_ops")
        assertThat(migration).contains("FHKST01010100", "'132030', 'SYMBOL'")
        assertThat(migration).contains("RAG exact identifier identity drifted")
        assertThat(migration).doesNotContain("EXECUTE format", "EXECUTE p_", "quote_ident")
    }

    @Test
    fun `V19 independently checks verified project public topic and active membership`() {
        val channelSections =
            listOf(
                "search_authorized_rag_exact",
                "search_authorized_rag_lexical",
                "search_authorized_rag_dense",
            ).map { function ->
                migration
                    .substringAfter("CREATE FUNCTION $function")
                    .substringBefore("ALTER FUNCTION $function")
            }
        listOf(
            "generation.status = 'ACTIVE'",
            "generation.evaluation_status = 'PASSED'",
            "source.source_type = 'PROJECT_SOURCE_CARD'",
            "source.retired_at IS NULL",
            "revision.tier = 'PROJECT'",
            "revision.access_level = 'PUBLIC'",
            "verification.status = 'VERIFIED'",
            "membership.corpus_generation_id = claim.active_generation_id",
            "topic.public_topic = ANY(claim.allowed_topics)",
        ).forEach { invariant ->
            channelSections.forEach { section ->
                assertThat(section).contains(invariant)
            }
        }
    }

    @Test
    fun `V19 gives query role no raw table or DML privileges`() {
        assertThat(migration).contains(
            "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_rag_query",
        )
        assertThat(migration).doesNotContain(
            "GRANT SELECT ON TABLE rag_chunk_revisions TO decision_rag_query",
            "GRANT INSERT",
            "GRANT UPDATE",
            "GRANT DELETE",
            "GRANT CREATE",
        )
        assertThat(migration).contains("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC")
        assertThat(migration).contains("SET search_path = pg_catalog, public, pg_temp")
    }
}
