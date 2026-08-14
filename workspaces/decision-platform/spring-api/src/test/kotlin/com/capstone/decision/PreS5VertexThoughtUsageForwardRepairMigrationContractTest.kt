package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class PreS5VertexThoughtUsageForwardRepairMigrationContractTest {
    private val migrationDirectory = Path.of("src/main/resources/db/migration")
    private val migrationPath = migrationDirectory.resolve("V65__pre_s5_vertex_thought_usage_forward_repair.sql")

    @Test
    fun `V65 aligns Gemini thought usage with immutable cost and output caps`() {
        val migration = Files.readString(migrationPath)

        assertThat(migration).contains(
            "total_token_count - prompt_token_count - candidate_token_count BETWEEN 0 AND 32768",
            "p_total_token_count - p_prompt_token_count > reservation.output_token_cap",
            "(p_total_token_count - p_prompt_token_count)::bigint * reservation.output_microusd_per_token",
            "REVOKE ALL PRIVILEGES ON FUNCTION public.commit_rag_v2_immutable_vertex_usage",
            "GRANT EXECUTE ON FUNCTION public.commit_rag_v2_immutable_vertex_usage",
        )
        assertThat(migration).doesNotContain("http_get", "http_post", "COPY PROGRAM")
    }

    @Test
    fun `V65 remains the preserved pre S5 forward migration`() {
        val versions =
            Files.list(migrationDirectory).use { paths ->
                paths
                    .map { it.fileName.toString() }
                    .filter { it.matches(Regex("""V[0-9]+__.+\.sql""")) }
                    .map { Regex("""^V([0-9]+)__""").find(it)!!.groupValues[1].toInt() }
                    .sorted()
                    .toList()
            }

        assertThat(versions.windowed(3)).contains(listOf(63, 64, 65))
    }
}
