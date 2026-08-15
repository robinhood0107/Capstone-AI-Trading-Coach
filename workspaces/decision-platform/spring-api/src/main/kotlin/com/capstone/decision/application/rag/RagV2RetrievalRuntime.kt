package com.capstone.decision.application.rag

/**
 * Spring-issued opaque claim은 Python v2 process가 corpus/profile/owner를 선택하지 못하게 한다.
 * owner ID는 request wire payload에 넣지 않으며 query-role DB function이 claim에서만 해석한다.
 */
data class RagV2EvaluationContext(
    val requestId: String,
    val ownerScopeClaim: String,
    /**
     * Voyage query embedding은 owner의 effective external-processing consent가 있을 때만 가능하다.
     * raw consent event나 owner identity는 loopback wire로 보내지 않고 이 boolean capability만 전달한다.
     */
    val externalQueryConsentGranted: Boolean = false,
)

/**
 * immutable public/owner pointer에 pin된 v2 retrieval receipt다. source raw text와 local path는 없다.
 */
data class RagV2RetrievalScope(
    val scopeClaimId: String,
    val exact30GenerationId: String,
    val oa112GenerationId: String,
    val ownerGenerationId: String?,
    val embeddingProfileId: String,
    val policyVersion: Long,
    val ownerEmbeddingProfileId: String? = null,
)

/**
 * gRPC adapter가 받은 citation identity다. persistence 전 DB definer function이 다시 canonicalize한다.
 */
data class RagV2RetrievedCitation(
    val citationId: String,
    val sourceId: String,
    val sourceRevisionId: String,
    val chunkRevisionId: String,
    val generationId: String,
    val citationKind: String,
    val title: String?,
    val canonicalUrl: String?,
    val documentId: String?,
    val displayName: String?,
    val locator: Map<String, Any>,
    val provenanceResultId: String? = null,
)

/**
 * local BGE result에는 provider attempt가 없고, Voyage profile은 effective consent 아래 query attempt 하나만
 * 기록할 수 있다. Vertex answer generation은 별도 gate다.
 */
data class RagV2EvaluationResult(
    val generationStatus: RagGenerationStatus,
    val answer: String?,
    val citations: List<RagV2RetrievedCitation>,
    val citationCoverage: Double,
    val retrievalFailure: Boolean,
    val guardrailFlags: List<String>,
    val failureCode: String,
    val exact30GenerationId: String,
    val oa112GenerationId: String,
    val ownerGenerationId: String?,
    val embeddingProfileId: String,
    val policyVersion: Long,
    val providerPhysicalAttempts: Int,
    val externalProviderCandidate: Boolean,
    val geminiPhysicalCalls: Int = 0,
    val openAiPhysicalCalls: Int = 0,
    val voyagePhysicalCalls: Int = 0,
)

interface RagV2EvaluationPort {
    /**
     * one-shot loopback evaluation only; retry/provider fallback is intentionally absent.
     */
    fun evaluate(
        command: RagAskCommand,
        context: RagV2EvaluationContext,
    ): RagV2EvaluationResult
}

/** MCP/Strong LLM이 history 저장이나 generation 없이 재사용하는 owner-scoped retrieval projection이다. */
data class RagV2SearchEvidenceResult(
    val scope: RagV2RetrievalScope,
    val citations: List<RagV2RetrievedCitation>,
    val evidence: List<RagV2VertexEvidence>,
)
