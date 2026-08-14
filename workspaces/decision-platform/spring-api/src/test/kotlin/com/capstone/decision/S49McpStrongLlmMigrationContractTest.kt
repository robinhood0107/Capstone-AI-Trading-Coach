package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class S49McpStrongLlmMigrationContractTest {
    private val directory = Path.of("src/main/resources/db/migration")
    private val path = directory.resolve("V66__s4_9_mcp_strong_llm_boundary.sql")

    @Test
    fun `V66 is the single next free S4 9 migration`() {
        val versions =
            Files.list(directory).use { paths ->
                paths
                    .map { it.fileName.toString() }
                    .filter { it.matches(Regex("V[0-9]+__.*\\.sql")) }
                    .map { Regex("^V([0-9]+)__").find(it)!!.groupValues[1].toInt() }
                    .toList()
            }

        assertThat(versions.max()).isEqualTo(66)
        assertThat(versions.count { it == 66 }).isEqualTo(1)
    }

    @Test
    fun `V66 stores only hashes encrypted history and content free usage behind definer functions`() {
        val sql = Files.readString(path)

        assertThat(sql).contains(
            "s4_9_mcp_oauth_authorization_codes",
            "s4_9_mcp_oauth_refresh_tokens",
            "s4_9_strong_llm_usage_ledger",
            "s4_9_web_evidence_metadata",
            "s4_9_answer_validation_receipts",
            "s4_9_saved_answer_history",
            "persist_s4_9_strong_llm_history",
            "revoke_s4_9_mcp_refresh_token_family",
            "guardrail_flags = ARRAY['MODEL_KNOWLEDGE_ONLY']::text[]",
            "FORCE ROW LEVEL SECURITY",
            "SECURITY DEFINER",
            "session_user <> 'decision_app'",
            "REVOKE ALL PRIVILEGES ON TABLE",
            "raw_request_stored boolean NOT NULL DEFAULT false CHECK (NOT raw_request_stored)",
            "raw_response_stored boolean NOT NULL DEFAULT false CHECK (NOT raw_response_stored)",
        )
        assertThat(sql).doesNotContain(
            "access_token text",
            "refresh_token text",
            "authorization_code text",
            "raw_web_body text",
            "model_response text",
            "GRANT SELECT ON",
            "http_get",
            "http_post",
            "COPY PROGRAM",
        )
    }
}
