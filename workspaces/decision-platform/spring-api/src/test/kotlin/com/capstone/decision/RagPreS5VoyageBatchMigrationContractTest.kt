package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagPreS5VoyageBatchMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val baseMigrationPath = migrationDirectory.resolve("V54__pre_s5_voyage_resumable_document_batches.sql")
    private val batchMigrationPath = migrationDirectory.resolve("V55__pre_s5_voyage_batch_forward_repair.sql")
    private val migrationPath = migrationDirectory.resolve("V56__pre_s5_voyage_evaluation_claim_binding.sql")
    private val migration by lazy {
        Files.readString(baseMigrationPath) + Files.readString(batchMigrationPath) + Files.readString(migrationPath)
    }

    @Test
    fun `Voyage batch forward repair uses the dynamic next free Flyway version`() {
        val versions =
            Files.list(migrationDirectory).use { paths ->
                paths
                    .filter { it.fileName.toString().matches(Regex("V[0-9]+__.*\\.sql")) }
                    .map { migrationVersion(it) }
                    .toList()
            }
        assertThat(migrationVersion(migrationPath)).isEqualTo(versions.max())
        assertThat(versions.count { it == 54 }).isEqualTo(1)
        assertThat(versions.count { it == 55 }).isEqualTo(1)
        assertThat(versions.count { it == 56 }).isEqualTo(1)
    }

    @Test
    fun `V54 preserves the first applied migration bytes`() {
        val digest =
            java.security.MessageDigest
                .getInstance("SHA-256")
                .digest(Files.readAllBytes(baseMigrationPath))
                .joinToString("") { byte -> "%02x".format(byte) }
        assertThat(digest).isEqualTo("23b439b3bffdaf9fb33afba4aa099902efc7bcfa6b31fdabbe503a490b31a83e")
    }

    @Test
    fun `V55 preserves the first applied forward repair bytes`() {
        val digest =
            java.security.MessageDigest
                .getInstance("SHA-256")
                .digest(Files.readAllBytes(batchMigrationPath))
                .joinToString("") { byte -> "%02x".format(byte) }
        assertThat(digest).isEqualTo("259a86d1fdbc4af8e14407cc3eaeed2f7d1971cba0a26b2438d0b6ce60fb29d0")
    }

    @Test
    fun `Voyage batch ledger is resumable least privilege and content free`() {
        assertThat(migration).contains(
            "rag_v2_immutable_voyage_document_batch_plans",
            "rag_v2_immutable_voyage_document_batches",
            "rag_v2_immutable_voyage_document_batch_vectors",
            "rag_v2_immutable_voyage_document_batch_attempts",
            "rag_v2_immutable_voyage_evaluation_batch_attempts",
            "rag_v2_immutable_voyage_evaluation_batch_vectors",
            "stage_rag_v2_immutable_voyage_document_batch",
            "claim_rag_v2_immutable_voyage_document_batch_attempt",
            "mark_rag_v2_immutable_voyage_document_batch_unknown_billing",
            "commit_and_stage_rag_v2_immutable_voyage_document_batch",
            "load_rag_v2_immutable_voyage_document_batch_vectors",
            "reserve_rag_v2_immutable_voyage_document_batch_usage",
            "reserve_rag_v2_immutable_voyage_evaluation_batch_usage",
            "claim_rag_v2_immutable_voyage_evaluation_batch_attempt",
            "mark_rag_v2_immutable_voyage_evaluation_batch_unknown_billing",
            "commit_and_stage_rag_v2_immutable_voyage_evaluation_batch",
            "load_rag_v2_immutable_voyage_evaluation_batch_vectors",
            "record_rag_v2_bge_public_execution_supersession",
            "TERMINALLY_SUPERSEDED_NO_FURTHER_BGE_RUN",
            "SECURITY DEFINER",
            "FORCE ROW LEVEL SECURITY",
            "session_user <> 'decision_rag_writer'",
            "provider_physical_call_count = 1",
            "expected_token_count BETWEEN 1 AND 110000",
            "expected_chunk_count BETWEEN 1 AND 672",
            "expected_response_bytes = 262144 + expected_chunk_count * 24576",
            "reservation.byte_cap >= (payload_batch ->> 'estimatedResponseBytes')::integer",
            "p_byte_cap NOT BETWEEN 1 AND 16777216",
            "byte_cap BETWEEN 1 AND 16777216",
            "FOREIGN KEY (batch_plan_sha256, batch_id)",
            "REFERENCES rag_v2_immutable_voyage_document_batches(batch_plan_sha256, batch_id)",
            "WHEN 'EXACT30' THEN 1",
            "WHEN 'OA112' THEN 1",
            "expected_query_count := CASE generation_scope WHEN 'EXACT30' THEN 1 WHEN 'OA112' THEN 1",
            "existing_batch.batch_count <> (payload_batch ->> 'batchCount')::integer",
            "existing_batch.expected_group_count <> (payload_batch ->> 'groupCount')::integer",
            "aggregate_token_count <> (payload_plan ->> 'tokenCount')::integer",
            "observed_source_count <> (payload_plan ->> 'sourceCount')::integer",
            "distinct_ordinal_count <> complete_count",
            "Pre-S5 Voyage document batch completion conflicts",
            "terminal ambiguous attempt",
            "evaluation claim conflicts with its reservation",
            "reservation.scope_claim_sha256 <> p_scope_claim_sha256",
            "reservation.evaluation_component_scope <> p_component_scope",
            "reservation.query_sha256 <> p_query_manifest_sha256",
            "reservation.packet_sha256 <> p_packet_sha256",
        )
        assertThat(migration).doesNotContain(
            "GRANT SELECT ON",
            "GRANT INSERT ON",
            "raw_provider_response",
            "credential",
            "api_key",
        )
    }

    private fun migrationVersion(path: Path): Int =
        Regex("^V([0-9]+)__")
            .find(path.fileName.toString())
            ?.groupValues
            ?.get(1)
            ?.toInt()
            ?: error("Flyway migration version is missing from ${path.fileName}.")
}
