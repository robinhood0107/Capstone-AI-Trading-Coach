package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class RagRpcScopeMigrationContractTest {
    @Test
    fun `V22 issues opaque owner scope and rechecks citations with least privilege`() {
        val migration = Files.readString(repositoryRoot().resolve(MIGRATION))

        assertThat(migration).contains("CREATE FUNCTION issue_rag_rpc_scope")
        assertThat(migration).contains("CREATE FUNCTION recheck_rag_rpc_citations")
        assertThat(migration).contains("SECURITY DEFINER")
        assertThat(migration).contains("SET search_path = pg_catalog, public, pg_temp")
        assertThat(migration).contains("session_user <> 'decision_app'")
        assertThat(migration).contains("claim.owner_user_id = p_owner_user_id")
        assertThat(migration).contains("claim.session_id = p_session_id")
        assertThat(migration).contains("claim.expires_at > statement_timestamp()")
        assertThat(migration).contains("chunk.topic = ANY(claim.allowed_topics)")
        assertThat(migration).contains("generation.status = 'ACTIVE'")
        assertThat(migration).contains("generation.evaluation_status = 'PASSED'")
        assertThat(migration).contains("REVOKE ALL PRIVILEGES")
        assertThat(migration).contains("TO decision_app")
        assertThat(migration).doesNotContain("GRANT SELECT ON TABLE")
        assertThat(migration).doesNotContain("GRANT UPDATE")
        assertThat(migration).doesNotContain("GRANT DELETE")
    }

    private fun repositoryRoot(): Path {
        var current = Path.of(System.getProperty("user.dir")).toAbsolutePath()
        while (!Files.exists(current.resolve("AGENTS.md"))) {
            current = current.parent ?: error("repository root was not found")
        }
        return current
    }

    private companion object {
        const val MIGRATION =
            "workspaces/decision-platform/spring-api/src/main/resources/db/migration/" +
                "V22__s4_6_rag_rpc_scope_projection.sql"
    }
}
