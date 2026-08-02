package com.capstone.decision.application.rag

import tools.jackson.databind.JsonNode
import java.time.Instant

data class RagV2CorpusStatus(
    val state: String,
    val publicCorpusVersion: String,
    val privateOverlayState: String,
    val progressPercent: Int,
    val failureCode: String?,
)

data class RagV2Answer(
    val requestId: String,
    val answerId: String?,
    val generationStatus: RagGenerationStatus,
    val answer: String?,
    val citationCoverage: Double,
    val citations: List<JsonNode>,
    val retrievalFailure: Boolean,
    val guardrailFlags: List<String>,
)

data class RagV2HistoryMetadata(
    val answerId: String,
    val createdAt: Instant,
    val expiresAt: Instant,
    val generationStatus: RagGenerationStatus,
)

data class RagV2HistoryPage(
    val items: List<RagV2HistoryMetadata>,
    val nextCursor: String?,
)

data class RagV2HistoryDetail(
    val answerId: String,
    val question: String,
    val answer: String,
    val generationStatus: RagGenerationStatus,
    val citations: List<JsonNode>,
    val createdAt: Instant,
    val expiresAt: Instant,
)

class RagV2CorpusNotReadyException : RuntimeException("RAG v2 full corpus bundle is not ready.")
