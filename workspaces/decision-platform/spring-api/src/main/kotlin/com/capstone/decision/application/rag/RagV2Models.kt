package com.capstone.decision.application.rag

import tools.jackson.databind.JsonNode
import java.time.Instant

data class RagV2CorpusStatus(
    val state: String,
    val publicCorpusVersion: String,
    val privateOverlayState: String,
    val progressPercent: Int,
    val failureCode: String?,
    /**
     * 생성형 답변의 오늘 상한과 남은 횟수. 자동 저술이 꺼져 있으면 셋 다 `null`이고, 그때 화면은
     * 검색 전용으로 동작한다. 화면이 "오늘 몇 번 더 물어볼 수 있는가"를 스스로 알 수 있어야
     * 상한에 닿았을 때 답이 비어 보이는 대신 이유를 말할 수 있다.
     *
     * 중첩 객체가 아니라 평평한 세 필드인 이유는 root OpenAPI의 승인된 전이 사슬 때문이다. 새
     * component schema를 만들면 exact-61 투영에 남아 사슬이 깨진다. `RagV2CorpusStatus`는 그
     * 투영에서 통째로 제거되는 스키마라 필드를 늘리는 것은 사슬을 건드리지 않는다.
     */
    val generationDailyCap: Int? = null,
    val generationUsedToday: Int? = null,
    val generationRemaining: Int? = null,
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
    val contractId: String = "s4-rag-v2-import-ticket-v2",
    val schemaVersion: Int = 2,
    val ticketId: String,
    val sourceScope: String = "OWNER_PRIVATE",
    val embeddingProfileId: String,
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

/** MCP에는 질문·본문 없이 이미 검증된 retrieval failure code만 노출한다. */
class RagV2McpSearchUnavailableException(
    failureCode: String,
) : RuntimeException("S4_9_MCP_RAG_SEARCH_$failureCode")
