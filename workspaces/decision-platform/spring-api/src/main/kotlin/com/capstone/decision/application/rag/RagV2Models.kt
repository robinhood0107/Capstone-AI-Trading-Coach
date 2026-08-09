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
    // retrieval-only history encrypts an empty internal payload but must not represent it as an LLM answer.
    val answer: String?,
    val generationStatus: RagGenerationStatus,
    val citations: List<JsonNode>,
    val createdAt: Instant,
    val expiresAt: Instant,
)

data class RagV2ExternalConsentCommand(
    val action: String,
    val disclosureDigest: String,
    val policyDigest: String,
    val processorSetDigest: String,
)

data class RagV2EffectiveConsent(
    val contractId: String = "s4-rag-v2-effective-consent-v1",
    val schemaVersion: Int = 1,
    val consentEventId: String,
    val effective: Boolean,
    val policyDigest: String,
    val processorSetDigest: String,
    val state: String,
)

data class RagV2ImportTicket(
    val contractId: String = "s4-rag-v2-import-ticket-v1",
    val schemaVersion: Int = 1,
    val ticketId: String,
    val sourceScope: String = "OWNER_PRIVATE",
    val issuedAt: Instant,
    val expiresAt: Instant,
    val ttlSeconds: Int = 300,
    val singleUse: Boolean = true,
    val ownerBound: Boolean = true,
    val ownerRawCopyAllowed: Boolean = false,
)

/**
 * owner document hard-delete capability는 short-lived opaque ticket으로만 local control plane에 전달한다.
 * owner identity는 응답에 포함하지 않으며, document binding과 consumption은 DB security-definer boundary가 강제한다.
 */
data class RagV2DeleteTicket(
    val contractId: String = "s4-rag-v2-delete-ticket-v1",
    val schemaVersion: Int = 1,
    val ticketId: String,
    val sourceScope: String = "OWNER_PRIVATE",
    val documentId: String,
    val issuedAt: Instant,
    val expiresAt: Instant,
    val ttlSeconds: Int = 300,
    val singleUse: Boolean = true,
    val ownerBound: Boolean = true,
    val documentBound: Boolean = true,
    val ownerRawCopyAllowed: Boolean = false,
)

class RagV2CorpusNotReadyException : RuntimeException("RAG v2 full corpus bundle is not ready.")

class RagV2ExternalConsentRequiredException : RuntimeException("External AI RAG v2 consent is required.")
