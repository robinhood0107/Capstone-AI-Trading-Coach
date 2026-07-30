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

fun RagSourceRegistryEntry.toResponse(): RagSourceResponse =
    RagSourceResponse(
        sourceId = sourceId,
        title = title,
        sourceType = sourceType,
        tier = tier,
        accessLevel = accessLevel,
        licenseDecision = licenseDecision,
        externalProcessingAllowed = externalProcessingAllowed,
        initialProcessing = initialProcessing,
        retentionMode = retentionMode,
        retentionDays = retentionDays,
        retentionOwner = retentionOwner,
        canonicalUrl = canonicalUrl,
        attribution = attribution,
        ingestStatus = ingestStatus,
        createdAt = createdAt,
        retiredAt = retiredAt,
        lastCheckedAt = lastCheckedAt,
        latestCheckResult = latestCheckResult,
    )

@Schema(name = "S4RagErrorResponse")
data class S4RagErrorResponseSchema(
    val success: Boolean = false,
    val requestId: String = "req_example",
    val data: Nothing? = null,
    val warnings: List<Any> = emptyList(),
    val error: S4RagErrorSchema = S4RagErrorSchema(),
)

data class S4RagErrorSchema(
    val code: String = "VALIDATION_ERROR",
    val message: String = "Request validation failed.",
    val details: Map<String, Any?> = emptyMap(),
)
