package com.capstone.decision.application.rag

import java.time.Instant

data class RagSourceRegistryEntry(
    val sourceId: String,
    val title: String,
    val institution: String,
    val topic: String,
    val attribution: String,
    val canonicalUrl: String,
    val lastCheckedAt: Instant?,
)

data class RagSourceRegistryList(
    val items: List<RagSourceRegistryEntry>,
)

class RagValidationException(
    val violations: List<RagFieldViolation>,
) : RuntimeException("Invalid RAG request.")

data class RagFieldViolation(
    val field: String,
    val reason: String,
)

class RagSourceRegistryUnavailableException(
    cause: Throwable? = null,
) : RuntimeException("RAG source registry is unavailable.", cause)
