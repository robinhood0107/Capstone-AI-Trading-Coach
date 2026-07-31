package com.capstone.decision.application.rag

interface RagEvaluationPort {
    /**
     * S4.4 기본 구현은 local fixture-only이며 provider/network 호출을 만들지 않는다.
     * S4.6의 Python 연결 전까지 반환된 provider physical count는 항상 0이어야 한다.
     */
    fun evaluate(command: RagAskCommand): RagEvaluationResult
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
