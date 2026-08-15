package com.capstone.decision.infrastructure.mcp

import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import java.util.concurrent.atomic.AtomicInteger

class ResearchToolFacadeTest {
    @Test
    fun `empty fallback search is a stable typed failure`() {
        val facade = ResearchToolFacade(PublicWebSearchPort { emptyList() }, PublicWebReaderPort { error("unused") })
        val session = "s49_run_${"c".repeat(32)}"
        facade.openSession(session)

        assertThatThrownBy { facade.search(session, "portfolio diversification") }
            .isInstanceOf(S49SearchUnavailableException::class.java)
            .hasMessage("S4_9_SEARCH_UNAVAILABLE_NO_RESULTS")
    }

    @Test
    fun `Google grounding source is citation-only and never reaches SafeReader`() {
        val reads = AtomicInteger()
        val facade =
            ResearchToolFacade(
                PublicWebSearchPort { emptyList() },
                PublicWebReaderPort {
                    reads.incrementAndGet()
                    error("Google redirect must not be read")
                },
            )
        val session = "s49_run_${"a".repeat(32)}"
        facade.openSession(session)
        facade.registerGoogleGrounding(
            session,
            "google_1",
            "Investor.gov",
            "https://vertexaisearch.cloud.google.com/grounding-api-redirect/example",
            "investor.gov",
        )

        assertThatThrownBy { facade.read(session, "google_1", null) }
            .isInstanceOf(S49WebReadRejectedException::class.java)
            .hasMessage("S4_9_GOOGLE_GROUNDING_AUTOMATED_READ_FORBIDDEN")
        assertThat(reads.get()).isZero()
    }

    @Test
    fun `registered page links become bounded depth provenance nodes`() {
        val facade =
            ResearchToolFacade(
                PublicWebSearchPort {
                    listOf(SearxngSearchResult("Root", "https://example.com/root", "safe"))
                },
                PublicWebReaderPort { url ->
                    BoundedWebDocument(
                        url,
                        "Root",
                        "Safe evidence",
                        "text/html",
                        listOf("https://www.investor.gov/diversification"),
                    )
                },
            )
        val session = "s49_run_${"b".repeat(32)}"
        facade.openSession(session)
        val root = facade.search(session, "portfolio diversification").single()

        val document = facade.read(session, root.resultId, null)

        val discovered = document.discoveredLinks.single()
        assertThat(discovered.sourceType).isEqualTo(ResearchSourceType.DISCOVERED_LINK)
        assertThat(discovered.parentResultId).isEqualTo(root.resultId)
        assertThat(discovered.depth).isEqualTo(1)
    }
}
