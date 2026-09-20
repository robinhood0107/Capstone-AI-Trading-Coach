package com.capstone.decision.application.rag

import java.time.Instant

/** 세계 뉴스는 설명과 조회에만 쓰이며 Decision/Signal/Risk/Order 권한이 없다. */
data class WorldNewsItem(
    val documentId: String,
    val documentVersionId: String,
    val sourceId: String,
    val provider: String,
    val providerDocumentId: String?,
    val canonicalUrl: String,
    val republicationOfDocumentId: String?,
    val identityStatus: String,
    val title: String?,
    val boundedQuote: String?,
    val boundedPassage: String?,
    val language: String,
    val publishedAt: Instant?,
    val publicationStatus: String,
    val providerObservedAt: Instant,
    val firstSeenAt: Instant,
    val availableAt: Instant,
    val rightsProfile: String,
    val externalLlmAllowed: Boolean,
    val lookupAllowed: Boolean,
    val ragRetrievalAllowed: Boolean,
    val promptUntrusted: Boolean,
    val collectionStatus: String,
    val contentSha256: String,
    val versionSha256: String,
)

data class WorldNewsCollectionStatus(
    val provider: String,
    val collectionStatus: String,
    val startedAt: Instant,
    val completedAt: Instant?,
    val observedThrough: Instant?,
    val itemCount: Int,
    val errorCode: String?,
)

data class WorldNewsPage(
    val items: List<WorldNewsItem>,
    val collections: List<WorldNewsCollectionStatus>,
    val asOf: Instant,
    val decisionAuthority: String = "NONE",
    val signalAuthority: String = "NONE",
    val orderAuthority: String = "NONE",
)

interface WorldNewsRepository {
    fun lookup(
        query: String,
        asOf: Instant,
        limit: Int,
    ): List<WorldNewsItem>

    fun collectionStatuses(): List<WorldNewsCollectionStatus>
}

class WorldNewsUnavailableException : RuntimeException("World-news lookup is unavailable.")

class WorldNewsValidationException : RuntimeException("World-news lookup request is invalid.")
