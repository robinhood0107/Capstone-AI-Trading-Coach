package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class PreS5RetrievalScopeTtlForwardRepairMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath =
        migrationDirectory.resolve("V64__pre_s5_retrieval_scope_ttl_forward_repair.sql")

    @Test
    fun `V64 extends only the v3 provider preparation scope to five minutes`() {
        assertThat(migrationPath).exists()
        val migration = Files.readString(migrationPath)

        assertThat(migration).contains(
            "DROP CONSTRAINT rag_v2_retrieval_scope_expiry_check",
            "expires_at = created_at + interval '2 minutes'",
            "expires_at = created_at + interval '5 minutes'",
            "CREATE FUNCTION public.issue_rag_v2_retrieval_scope_v3",
            "UPDATE public.rag_v2_retrieval_scope_claims",
            "GRANT EXECUTE ON FUNCTION public.issue_rag_v2_retrieval_scope_v3(text, text, text[])",
        )
        assertThat(migration).doesNotContain("http_get", "http_post", "COPY PROGRAM")
    }

    @Test
    fun `V64 is the next free migration without rewriting historical SQL`() {
        val versions =
            Files.list(migrationDirectory).use { paths ->
                paths
                    .map { it.fileName.toString() }
                    .filter { it.matches(Regex("""V[0-9]+__.+\.sql""")) }
                    .map { Regex("""^V([0-9]+)__""").find(it)!!.groupValues[1].toInt() }
                    .sorted()
                    .toList()
            }

        assertThat(versions.last()).isEqualTo(64)
        assertThat(versions.takeLast(2)).containsExactly(63, 64)
    }
}
