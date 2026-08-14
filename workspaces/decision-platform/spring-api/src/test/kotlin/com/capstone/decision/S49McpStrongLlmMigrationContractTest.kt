package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class S49McpStrongLlmMigrationContractTest {
    private val directory = Path.of("src/main/resources/db/migration")
    private val path = directory.resolve("V66__s4_9_mcp_strong_llm_boundary.sql")
    private val repairPath = directory.resolve("V67__s4_9_mcp_public_scope_forward_repair.sql")

    @Test
    fun `V66 base and V67 public scope repair are forward only`() {
        val versions =
            Files.list(directory).use { paths ->
                paths
                    .map { it.fileName.toString() }
                    .filter { it.matches(Regex("V[0-9]+__.*\\.sql")) }
                    .map { Regex("^V([0-9]+)__").find(it)!!.groupValues[1].toInt() }
                    .toList()
            }

        assertThat(versions.max()).isEqualTo(67)
        assertThat(versions.count { it == 66 }).isEqualTo(1)
        assertThat(versions.count { it == 67 }).isEqualTo(1)
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

    @Test
    fun `V67 binds MCP OAuth owner authority before retrieval and supports fifteen minute contexts`() {
        val sql = Files.readString(repairPath)

        assertThat(sql).contains(
            "owner_scope_authorized boolean NOT NULL DEFAULT true",
            "issue_s4_9_mcp_retrieval_scope",
            "p_include_owner boolean",
            "interval '15 minutes'",
            "IF NOT claim_row.owner_scope_authorized",
            "claim_row.owner_embedding_profile_id",
            "REVOKE ALL PRIVILEGES ON TABLE public.rag_v2_retrieval_scope_claims FROM decision_app",
        )
        assertThat(sql).doesNotContain("DROP TABLE", "TRUNCATE TABLE", "DELETE FROM")
    }
}
