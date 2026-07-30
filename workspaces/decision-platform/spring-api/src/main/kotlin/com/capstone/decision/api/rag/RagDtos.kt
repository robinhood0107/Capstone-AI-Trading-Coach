package com.capstone.decision.api.rag

import com.capstone.decision.application.rag.RagSourceRegistryEntry
import io.swagger.v3.oas.annotations.media.Schema
import java.time.Instant

data class RagSourceListResponse(
    @field:Schema(description = "인증된 subject에게 노출 가능한 RAG source registry metadata")
    val items: List<RagSourceResponse>,
)

data class RagSourceResponse(
    val sourceId: String,
    val title: String,
    val institution: String,
    val topic: String,
    val attribution: String,
    val canonicalUrl: String,
    val lastCheckedAt: Instant?,
)

fun RagSourceRegistryEntry.toResponse(): RagSourceResponse =
    RagSourceResponse(
        sourceId = sourceId,
        title = title,
        institution = institution,
        topic = topic,
        attribution = attribution,
        canonicalUrl = canonicalUrl,
        lastCheckedAt = lastCheckedAt,
    )
