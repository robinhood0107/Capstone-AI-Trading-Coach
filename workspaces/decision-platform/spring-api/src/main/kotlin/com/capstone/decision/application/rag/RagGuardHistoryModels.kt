package com.capstone.decision.application.rag

import java.time.Instant

enum class RagAnswerMode {
    CONCISE,
    DETAILED,
}

data class RagAskCommand(
    val question: String,
    val answerMode: RagAnswerMode,
    val relatedSymbols: List<String>,
    val topics: List<String>,
)

data class RagEvaluationContext(
    val requestId: String,
    val ownerScopeClaim: String,
    val consentGranted: Boolean,
    val consentPolicyVersion: String,
    val policyId: String,
    val policyVersion: Long,
    val activeGenerationId: String,
    val embeddingProfileId: String,
)

data class RagRetrievalScope(
    val scopeClaimId: String,
    val policyId: String,
    val policyVersion: Long,
    val activeGenerationId: String,
    val embeddingProfileId: String,
)

enum class RagGenerationStatus {
    ANSWERED,
    RETRIEVAL_ONLY,
    RETRIEVAL_FAILURE,
    BLOCKED_SENSITIVE,
    BLOCKED_ADVICE,
    GENERATION_UNAVAILABLE,
}

data class RagCitation(
    val citationId: String,
    val sourceId: String,
    val sourceRevisionId: String,
    val chunkRevisionId: String,
    val generationId: String,
    val title: String,
    val sectionTitle: String,
    val canonicalUrl: String,
)

data class RagEvaluationResult(
    val generationStatus: RagGenerationStatus,
    val answer: String?,
    val citations: List<RagCitation>,
    val citationCoverage: Double,
    val retrievalFailure: Boolean,
    val guardrailFlags: List<String>,
    val providerPhysicalAttempts: Int,
    val externalProviderCandidate: Boolean,
    val geminiPhysicalCalls: Int = 0,
    val openAiPhysicalCalls: Int = 0,
    val voyagePhysicalCalls: Int = 0,
)

data class RagIdempotencyIdentity(
    val scopeHmac: String,
    val requestFingerprint: String,
)

data class RagHistoryIdentity(
    val answerId: String,
    val ownerUserId: String,
    val createdAt: Instant,
)

data class RagEncryptedFieldPayload(
    val nonce: ByteArray,
    val ciphertext: ByteArray,
    val tag: ByteArray,
)

data class RagEncryptedHistoryPayload(
    val kekVersion: String,
    val wrapNonce: ByteArray,
    val wrappedDek: ByteArray,
    val wrapTag: ByteArray,
    val question: RagEncryptedFieldPayload,
    val answer: RagEncryptedFieldPayload,
)

data class RagDecryptedHistoryPayload(
    val question: String,
    val answer: String,
)

data class RagAnswerProjection(
    val requestId: String,
    val answerId: String,
    val generationStatus: RagGenerationStatus,
    val answer: String?,
    val citationCoverage: Double,
    val retrievalFailure: Boolean,
    val citations: List<RagPublicCitation>,
    val guardrailFlags: List<String>,
)

data class RagPublicCitation(
    val citationId: String,
    val sourceId: String,
    val title: String,
    val sectionTitle: String,
    val canonicalUrl: String,
)

sealed interface RagClaimDecision {
    data object Claimed : RagClaimDecision

    data class Replay(
        val answerId: String,
    ) : RagClaimDecision

    data object Conflict : RagClaimDecision

    data object InProgress : RagClaimDecision

    data object ResultUnavailable : RagClaimDecision

    data object FailedBeforeProvider : RagClaimDecision

    data object UnknownAfterProvider : RagClaimDecision
}

data class RagAnswerCompletion(
    val identity: RagHistoryIdentity,
    val idempotency: RagIdempotencyIdentity,
    val answerMode: RagAnswerMode,
    val evaluation: RagEvaluationResult,
    val encrypted: RagEncryptedHistoryPayload,
)

data class RagStoredEncryptedHistory(
    val identity: RagHistoryIdentity,
    val answerMode: RagAnswerMode,
    val generationStatus: RagGenerationStatus,
    val citationCoverage: Double,
    val retrievalFailure: Boolean,
    val guardrailFlags: List<String>,
    val citationCount: Int,
    val encrypted: RagEncryptedHistoryPayload,
    val expiresAt: Instant,
    val helpful: Boolean?,
)

data class RagHistoryMetadata(
    val answerId: String,
    val createdAt: Instant,
    val expiresAt: Instant,
    val answerMode: RagAnswerMode,
    val generationStatus: RagGenerationStatus,
    val helpful: Boolean?,
)

data class RagHistoryCursorPoint(
    val createdAt: Instant,
    val answerId: String,
)

data class RagHistoryPage(
    val items: List<RagHistoryMetadata>,
    val nextCursor: String?,
)

data class RagHistoryDetail(
    val answerId: String,
    val createdAt: Instant,
    val expiresAt: Instant,
    val answerMode: RagAnswerMode,
    val generationStatus: RagGenerationStatus,
    val question: String,
    val answer: String?,
    val citations: List<RagPublicCitation>,
    val helpful: Boolean?,
)

data class RagConsentEvent(
    val consentEventId: String,
    val consentType: String,
    val action: String,
    val policyVersion: String,
    val createdAt: Instant,
)

data class RagEffectiveConsent(
    val granted: Boolean,
    val policyVersion: String?,
    val recordedAt: Instant?,
)

data class RagPurgeResult(
    val deletedCount: Int,
    val oldestExpiredLagSeconds: Long,
)

class RagHistoryCorruptedException : RuntimeException("RAG history is unavailable.")

class RagIdempotencyConflictException : RuntimeException()

class RagIdempotencyInProgressException : RuntimeException()

class RagIdempotencyResultUnavailableException : RuntimeException()

class RagHistoryPersistFailedException : RuntimeException()

class RagHistoryNotFoundException : RuntimeException()

class RagRateLimitedException : RuntimeException()

class RagGuardHistoryUnavailableException(
    cause: Throwable? = null,
) : RuntimeException("RAG guard/history service is unavailable.", cause)
