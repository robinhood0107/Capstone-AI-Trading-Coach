package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class WorldNewsMigrationContractTest {
    private val path = Path.of("src/main/resources/db/migration/V157__world_news_v2_lookup_rag.sql")
    private val migration by lazy { Files.readString(path) }

    @Test
    fun `world-news uses the next migration and keeps append-only least privilege`() {
        val versions =
            Files.list(path.parent).use { files ->
                files
                    .filter { it.fileName.toString().matches(Regex("""V[0-9]+__.+\.sql""")) }
                    .map { Regex("""^V([0-9]+)__""").find(it.fileName.toString())!!.groupValues[1].toInt() }
                    .sorted()
                    .toList()
            }
        assertThat(versions.count { it == 157 }).isEqualTo(1)
        assertThat(versions[versions.indexOf(157) - 1]).isEqualTo(156)
        assertThat(migration).contains(
            "world_news_document_versions_v2",
            "world_news_observations_v2",
            "world_news_collection_runs_v2",
            "first_seen_at",
            "provider_observed_at",
            "available_at",
            "search_authorized_world_news_rag_v2",
            "canonicalize_rag_v2_immutable_retrieval_citations_pre_world_news_v157",
            "REVOKE ALL ON TABLE",
        )
        assertThat(migration).doesNotContain(
            "raw_provider_body",
            "response_header",
            "credential text",
            "GRANT SELECT ON",
            "GRANT INSERT ON",
        )
    }
}
