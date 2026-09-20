package com.capstone.decision.application.rag

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset

class WorldNewsServiceTest {
    private val now = Instant.parse("2026-09-08T04:00:00Z")

    @Test
    fun `missing publication remains a visible retrieval state`() {
        val item = newsItem()
        val service = WorldNewsService(FixedRepository(listOf(item)), Clock.fixed(now, ZoneOffset.UTC))

        val result = service.lookup("공급망", 20)

        assertThat(result.items).containsExactly(item)
        assertThat(result.items.single().publishedAt).isNull()
        assertThat(result.items.single().publicationStatus).isEqualTo("MISSING")
        assertThat(result.asOf).isEqualTo(now)
        assertThat(result.decisionAuthority).isEqualTo("NONE")
        assertThat(result.orderAuthority).isEqualTo("NONE")
    }

    @Test
    fun `invalid query and repository failure are not normal empty`() {
        val service = WorldNewsService(FixedRepository(emptyList()), Clock.fixed(now, ZoneOffset.UTC))
        assertThrows<WorldNewsValidationException> { service.lookup("x".repeat(201), 20) }
        assertThrows<WorldNewsValidationException> { service.lookup("ok", 0) }
        assertThrows<WorldNewsUnavailableException> {
            WorldNewsService(FailingRepository(), Clock.fixed(now, ZoneOffset.UTC)).lookup("ok", 20)
        }
    }

    private fun newsItem() =
        WorldNewsItem(
            documentId = "news_doc_" + "1".repeat(32),
            documentVersionId = "news_ver_" + "2".repeat(32),
            sourceId = "src_gdelt_world_news",
            provider = "GDELT_GQG",
            providerDocumentId = null,
            canonicalUrl = "https://example.com/news",
            republicationOfDocumentId = null,
            identityStatus = "VERIFIED",
            title = "세계 공급망",
            boundedQuote = "병목이 완화되고 있다.",
            boundedPassage = null,
            language = "ko",
            publishedAt = null,
            publicationStatus = "MISSING",
            providerObservedAt = Instant.parse("2026-09-08T03:00:00Z"),
            firstSeenAt = Instant.parse("2026-09-08T03:01:00Z"),
            availableAt = Instant.parse("2026-09-08T03:02:00Z"),
            rightsProfile = "GDELT_METADATA_QUOTE",
            externalLlmAllowed = false,
            lookupAllowed = true,
            ragRetrievalAllowed = true,
            promptUntrusted = true,
            collectionStatus = "COMPLETE",
            contentSha256 = "a".repeat(64),
            versionSha256 = "b".repeat(64),
        )

    private class FixedRepository(
        private val items: List<WorldNewsItem>,
    ) : WorldNewsRepository {
        override fun lookup(
            query: String,
            asOf: Instant,
            limit: Int,
        ) = items

        override fun collectionStatuses() = emptyList<WorldNewsCollectionStatus>()
    }

    private class FailingRepository : WorldNewsRepository {
        override fun lookup(
            query: String,
            asOf: Instant,
            limit: Int,
        ): List<WorldNewsItem> = error("db")

        override fun collectionStatuses(): List<WorldNewsCollectionStatus> = error("db")
    }
}
