package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagPreS5VoyageBatchMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath = migrationDirectory.resolve("V54__pre_s5_voyage_resumable_document_batches.sql")
    private val migration by lazy { Files.readString(migrationPath) }

    @Test
    fun `Voyage batch ledger uses the dynamic next free Flyway version`() {
        val versions =
            Files.list(migrationDirectory).use { paths ->
                paths
                    .filter { it.fileName.toString().matches(Regex("V[0-9]+__.*\\.sql")) }
                    .map { migrationVersion(it) }
                    .toList()
            }
        assertThat(migrationVersion(migrationPath)).isEqualTo(versions.max())
        assertThat(versions.count { it == 54 }).isEqualTo(1)
    }

    @Test
    fun `Voyage batch ledger is resumable least privilege and content free`() {
        assertThat(migration).contains(
            "rag_v2_immutable_voyage_document_batch_plans",
            "rag_v2_immutable_voyage_document_batches",
            "rag_v2_immutable_voyage_document_batch_vectors",
            "stage_rag_v2_immutable_voyage_document_batch",
            "load_rag_v2_immutable_voyage_document_batch_vectors",
            "record_rag_v2_bge_public_execution_supersession",
            "TERMINALLY_SUPERSEDED_NO_FURTHER_BGE_RUN",
            "SECURITY DEFINER",
            "FORCE ROW LEVEL SECURITY",
            "session_user <> 'decision_rag_writer'",
            "provider_physical_call_count = 1",
            "expected_token_count BETWEEN 1 AND 110000",
            "WHEN 'EXACT30' THEN 1",
            "WHEN 'OA112' THEN 1",
            "expected_query_count := CASE generation_scope WHEN 'EXACT30' THEN 1 WHEN 'OA112' THEN 1",
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
