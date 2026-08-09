package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class VoyageOfficialTokenizerUsageMigrationContractTest {
    private val migrationPath =
        Path.of("src/main/resources/db/migration/V51__s4_7d_voyage_official_tokenizer_usage_attestation.sql")

    @Test
    fun `V51 keeps historical usage functions and binds new Voyage receipts to official tokenizer evidence`() {
        val migration = Files.readString(migrationPath)

        assertThat(migration).contains(
            "ALTER TABLE rag_v2_immutable_voyage_usage_reservations",
            "official_tokenizer_sha256",
            "expected_input_tokens",
            "reserve_rag_v2_immutable_voyage_usage_with_tokenizer",
            "commit_rag_v2_immutable_voyage_usage_with_tokenizer",
            "reserve_rag_v2_immutable_voyage_query_usage_with_tokenizer",
            "commit_rag_v2_immutable_voyage_query_usage_with_tokenizer",
            "p_expected_input_tokens > reservation.token_cap",
            "p_official_tokenizer_sha256 IS NULL",
            "p_expected_input_tokens IS NULL",
            "session_user <> 'decision_rag_writer'",
            "SECURITY DEFINER",
        )
        assertThat(migration).doesNotContain(
            "canonical_text",
            "question text",
            "provider_response",
            "raw_response",
            "GRANT SELECT ON TABLE rag_v2_immutable_voyage_usage_reservations TO decision_rag_writer",
            "GRANT SELECT ON TABLE rag_v2_immutable_voyage_query_usage_reservations TO decision_rag_writer",
        )
    }
}
