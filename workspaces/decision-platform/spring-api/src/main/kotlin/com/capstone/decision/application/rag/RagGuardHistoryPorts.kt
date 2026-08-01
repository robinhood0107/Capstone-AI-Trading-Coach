package com.capstone.decision.application.rag

interface RagEvaluationPort {
    /**
     * Spring이 발급한 opaque owner scope와 active generation을 함께 보내고 Python 결과를 단일 회 평가한다.
     */
    fun evaluate(
        command: RagAskCommand,
        context: RagEvaluationContext,
    ): RagEvaluationResult
}

interface RagRetrievalScopePort {
    /**
     * 인증된 owner와 opaque request session에 대해 짧은 수명의 DB retrieval claim을 발급한다.
     */
    fun issue(
        ownerUserId: String,
        sessionId: String,
        topics: List<String>,
    ): RagRetrievalScope

    /**
     * Python이 반환한 citation이 발급한 claim의 topic과 현재 active generation에 계속 속하는지 저장 전 재검증한다.
     */
    fun requireAuthorized(
        ownerUserId: String,
        sessionId: String,
        scope: RagRetrievalScope,
        citations: List<RagCitation>,
    )
}

interface RagIdempotencyPort {
    /**
     * raw key는 이 경계 안에서만 소비하고 저장 가능한 두 purpose-separated HMAC만 반환한다.
     */
    fun identity(
        ownerUserId: String,
        rawKey: String,
        command: RagAskCommand,
    ): RagIdempotencyIdentity
}

interface RagGuardHistoryPolicy {
    val claimTtlSeconds: Long
}

interface RagHistoryCryptoPort {
    fun encrypt(
        identity: RagHistoryIdentity,
        question: String,
        answer: String,
    ): RagEncryptedHistoryPayload

    fun decrypt(
        identity: RagHistoryIdentity,
        encrypted: RagEncryptedHistoryPayload,
    ): RagDecryptedHistoryPayload
}

interface RagGuardHistoryPersistencePort {
    fun claim(
        ownerUserId: String,
        idempotency: RagIdempotencyIdentity,
        claimTtlSeconds: Int,
    ): RagClaimDecision

    fun complete(completion: RagAnswerCompletion)

    fun failBeforeProvider(
        ownerUserId: String,
        idempotency: RagIdempotencyIdentity,
    )

    fun markUnknownAfterProvider(
        ownerUserId: String,
        idempotency: RagIdempotencyIdentity,
    )

    fun findHistory(
        ownerUserId: String,
        answerId: String,
    ): RagStoredEncryptedHistory?

    fun findCitations(
        ownerUserId: String,
        answerId: String,
    ): List<RagCitation>

    fun listHistory(
        ownerUserId: String,
        cursor: RagHistoryCursorPoint?,
        limit: Int,
    ): List<RagHistoryMetadata>

    fun deleteHistory(
        ownerUserId: String,
        answerId: String,
    )

    fun upsertFeedback(
        ownerUserId: String,
        answerId: String,
        helpful: Boolean,
    ): Boolean

    fun recordConsent(
        ownerUserId: String,
        consentEventId: String,
        action: String,
        policyVersion: String,
    ): RagConsentEvent

    fun effectiveConsent(ownerUserId: String): RagEffectiveConsent

    fun purgeExpired(limit: Int): RagPurgeResult
}

interface RagRateLimitPort {
    fun acquire(ownerUserId: String)
}

interface RagHistoryCursorPort {
    fun encode(
        ownerUserId: String,
        point: RagHistoryCursorPoint,
    ): String

    fun decode(
        ownerUserId: String,
        cursor: String,
    ): RagHistoryCursorPoint
}
