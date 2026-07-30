package com.capstone.decision.application.rag

import java.time.Instant

data class RagSourceRegistryEntry(
    val sourceId: String,
    val title: String,
    val sourceType: String,
    val tier: String,
    val accessLevel: String,
    val licenseDecision: String,
    val externalProcessingAllowed: Boolean,
    val initialProcessing: String,
    val retentionMode: String,
    val retentionDays: Int,
    val retentionOwner: String,
    val canonicalUrl: String,
    val attribution: String?,
    val ingestStatus: String,
    val createdAt: Instant,
    val retiredAt: Instant?,
    val lastCheckedAt: Instant?,
    val latestCheckResult: String?,
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
