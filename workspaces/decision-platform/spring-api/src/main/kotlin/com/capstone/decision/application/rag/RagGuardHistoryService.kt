package com.capstone.decision.application.rag

import org.springframework.beans.factory.ObjectProvider
import org.springframework.stereotype.Service
import java.time.Clock
import java.util.UUID

@Service
class RagGuardHistoryService(
    private val evaluationPort: RagEvaluationPort,
    private val persistencePort: RagGuardHistoryPersistencePort,
    private val cryptoPort: RagHistoryCryptoPort,
    private val rateLimitPort: RagRateLimitPort,
    private val cursorPort: RagHistoryCursorPort,
    private val idempotencyPort: RagIdempotencyPort,
    private val retrievalScopePort: RagRetrievalScopePort,
    private val policy: RagGuardHistoryPolicy,
    private val clockProvider: ObjectProvider<Clock>,
) {
    /**
     * raw idempotency key는 HMAC 직후 보존하지 않으며 fixture-only 평가 결과를 암호화 저장한 뒤에만 반환한다.
     */
    fun ask(
        ownerUserId: String,
        requestId: String,
        rawIdempotencyKey: String,
        command: RagAskCommand,
    ): RagAnswerProjection {
        // consent read를 rate/idempotency/gRPC보다 먼저 수행해 비인가 상태의 후속 효과를 만들지 않는다.
        val consent = persistencePort.effectiveConsent(ownerUserId)
        rateLimitPort.acquire(ownerUserId)
        val idempotency =
            idempotencyPort.identity(
                ownerUserId = ownerUserId,
                rawKey = rawIdempotencyKey,
                command = command,
            )
        return when (
            val claim =
                persistencePort.claim(
                    ownerUserId = ownerUserId,
                    idempotency = idempotency,
                    claimTtlSeconds = policy.claimTtlSeconds.toInt(),
                )
        ) {
            RagClaimDecision.Claimed ->
                evaluateAndPersist(
                    ownerUserId = ownerUserId,
                    requestId = requestId,
                    idempotency = idempotency,
                    command = command,
                    consent = consent,
                )
            is RagClaimDecision.Replay ->
                historyProjection(ownerUserId, requestId, claim.answerId)
                    ?: throw RagIdempotencyResultUnavailableException()
            RagClaimDecision.Conflict -> throw RagIdempotencyConflictException()
            RagClaimDecision.InProgress -> throw RagIdempotencyInProgressException()
            RagClaimDecision.ResultUnavailable,
            RagClaimDecision.FailedBeforeProvider,
            RagClaimDecision.UnknownAfterProvider,
            -> throw RagIdempotencyResultUnavailableException()
        }
    }

    fun listHistory(
        ownerUserId: String,
        cursor: String?,
        limit: Int,
    ): RagHistoryPage {
        require(limit in 1..50)
        val point = cursor?.let { cursorPort.decode(ownerUserId, it) }
        val rows = persistencePort.listHistory(ownerUserId, point, limit + 1)
        val visible = rows.take(limit)
        val next =
            rows
                .getOrNull(limit - 1)
                ?.takeIf { rows.size > limit }
                ?.let { row ->
                    cursorPort.encode(
                        ownerUserId,
                        RagHistoryCursorPoint(row.createdAt, row.answerId),
                    )
                }
        return RagHistoryPage(visible, next)
    }

    fun getHistory(
        ownerUserId: String,
        answerId: String,
    ): RagHistoryDetail {
        val stored =
            persistencePort.findHistory(ownerUserId, answerId)
                ?: throw RagHistoryNotFoundException()
        val citations = persistencePort.findCitations(ownerUserId, answerId)
        if (!hasCompleteCitationSet(stored, citations)) {
            throw RagHistoryCorruptedException()
        }
        val decrypted = cryptoPort.decrypt(stored.identity, stored.encrypted)
        return RagHistoryDetail(
            answerId = stored.identity.answerId,
            createdAt = stored.identity.createdAt,
            expiresAt = stored.expiresAt,
            answerMode = stored.answerMode,
            generationStatus = stored.generationStatus,
            question = decrypted.question,
            answer =
                decrypted.answer.takeIf {
                    stored.generationStatus == RagGenerationStatus.ANSWERED
                },
            citations = citations.map { it.toPublic() },
            helpful = stored.helpful,
        )
    }

    fun deleteHistory(
        ownerUserId: String,
        answerId: String,
    ) {
        persistencePort.deleteHistory(ownerUserId, answerId)
    }

    fun feedback(
        ownerUserId: String,
        answerId: String,
        helpful: Boolean,
    ) {
        if (!persistencePort.upsertFeedback(ownerUserId, answerId, helpful)) {
            throw RagHistoryNotFoundException()
        }
    }

    fun recordConsent(
        ownerUserId: String,
        action: String,
        policyVersion: String,
    ): RagConsentEvent =
        persistencePort.recordConsent(
            ownerUserId = ownerUserId,
            consentEventId = id("cns"),
            action = action,
            policyVersion = policyVersion,
        )

    fun effectiveConsent(ownerUserId: String): RagEffectiveConsent = persistencePort.effectiveConsent(ownerUserId)

    private fun evaluateAndPersist(
        ownerUserId: String,
        requestId: String,
        idempotency: RagIdempotencyIdentity,
        command: RagAskCommand,
        consent: RagEffectiveConsent,
    ): RagAnswerProjection {
        val evaluation =
            try {
                val scope = retrievalScopePort.issue(ownerUserId, requestId, command.topics)
                evaluationPort
                    .evaluate(
                        command,
                        RagEvaluationContext(
                            requestId = requestId,
                            ownerScopeClaim = scope.scopeClaimId,
                            consentGranted = consent.granted,
                            consentPolicyVersion = consent.policyVersion ?: "NONE",
                            policyId = scope.policyId,
                            policyVersion = scope.policyVersion,
                            activeGenerationId = scope.activeGenerationId,
                            embeddingProfileId = scope.embeddingProfileId,
                        ),
                    ).also(::requireFixtureBoundary)
                    .also { result ->
                        retrievalScopePort.requireAuthorized(
                            ownerUserId = ownerUserId,
                            sessionId = requestId,
                            scope = scope,
                            citations = result.citations,
                        )
                    }
            } catch (exception: RuntimeException) {
                runCatching { persistencePort.failBeforeProvider(ownerUserId, idempotency) }
                throw RagGuardHistoryUnavailableException(exception)
            }
        val createdAt = clockProvider.getIfAvailable { Clock.systemUTC() }.instant()
        val historyIdentity =
            RagHistoryIdentity(
                answerId = id("rag_ans"),
                ownerUserId = ownerUserId,
                createdAt = createdAt,
            )
        val encrypted =
            try {
                cryptoPort.encrypt(
                    identity = historyIdentity,
                    question = command.question,
                    answer = evaluation.answer.orEmpty(),
                )
            } catch (exception: RuntimeException) {
                runCatching { persistencePort.failBeforeProvider(ownerUserId, idempotency) }
                throw RagGuardHistoryUnavailableException(exception)
            }
        try {
            persistencePort.complete(
                RagAnswerCompletion(
                    identity = historyIdentity,
                    idempotency = idempotency,
                    answerMode = command.answerMode,
                    evaluation = evaluation,
                    encrypted = encrypted,
                ),
            )
        } catch (exception: RuntimeException) {
            if (evaluation.providerPhysicalAttempts > 0) {
                runCatching {
                    persistencePort.markUnknownAfterProvider(ownerUserId, idempotency)
                }
                throw RagHistoryPersistFailedException()
            }
            runCatching { persistencePort.failBeforeProvider(ownerUserId, idempotency) }
            throw RagGuardHistoryUnavailableException(exception)
        }
        return evaluation.toProjection(requestId, historyIdentity.answerId)
    }

    private fun historyProjection(
        ownerUserId: String,
        requestId: String,
        answerId: String,
    ): RagAnswerProjection? {
        val stored = persistencePort.findHistory(ownerUserId, answerId) ?: return null
        val citations = persistencePort.findCitations(ownerUserId, answerId)
        if (!hasCompleteCitationSet(stored, citations)) {
            return null
        }
        val decrypted = cryptoPort.decrypt(stored.identity, stored.encrypted)
        return RagAnswerProjection(
            requestId = requestId,
            answerId = stored.identity.answerId,
            generationStatus = stored.generationStatus,
            answer =
                decrypted.answer.takeIf {
                    stored.generationStatus == RagGenerationStatus.ANSWERED
                },
            citationCoverage = stored.citationCoverage,
            retrievalFailure = stored.retrievalFailure,
            citations = citations.map { it.toPublic() },
            guardrailFlags = stored.guardrailFlags,
        )
    }

    private fun requireFixtureBoundary(evaluation: RagEvaluationResult) {
        require(evaluation.providerPhysicalAttempts == 0)
        require(evaluation.geminiPhysicalCalls == 0)
        require(evaluation.openAiPhysicalCalls == 0)
        require(evaluation.voyagePhysicalCalls == 0)
        require(!evaluation.externalProviderCandidate)
        require(evaluation.citations.size <= 5)
        require(
            evaluation.answer
                ?.toByteArray(Charsets.UTF_8)
                ?.size
                ?.let { it <= 8_192 } != false,
        )
        require(evaluation.citationCoverage in 0.0..1.0)
        require(evaluation.guardrailFlags.size <= 8)
        require(evaluation.guardrailFlags.toSet().size == evaluation.guardrailFlags.size)
        require(evaluation.guardrailFlags.all { FLAG.matches(it) })
        when (evaluation.generationStatus) {
            RagGenerationStatus.ANSWERED -> {
                require(!evaluation.answer.isNullOrBlank())
                require(evaluation.citations.isNotEmpty())
                require(evaluation.citationCoverage == 1.0)
                require(!evaluation.retrievalFailure)
            }
            RagGenerationStatus.RETRIEVAL_FAILURE -> {
                require(evaluation.answer == null)
                require(evaluation.citations.isEmpty())
                require(evaluation.citationCoverage == 0.0)
                require(evaluation.retrievalFailure)
            }
            else -> {
                require(evaluation.answer == null)
                require(evaluation.citations.isEmpty())
                require(evaluation.citationCoverage == 0.0)
                require(!evaluation.retrievalFailure)
            }
        }
        evaluation.citations.forEachIndexed { index, citation ->
            require(citation.citationId == "cit_${index + 1}")
            require(CHUNK_ID.matches(citation.chunkRevisionId))
        }
    }

    private fun RagEvaluationResult.toProjection(
        requestId: String,
        answerId: String,
    ): RagAnswerProjection =
        RagAnswerProjection(
            requestId = requestId,
            answerId = answerId,
            generationStatus = generationStatus,
            answer = answer,
            citationCoverage = citationCoverage,
            retrievalFailure = retrievalFailure,
            citations = citations.map { it.toPublic() },
            guardrailFlags = guardrailFlags,
        )

    private fun RagCitation.toPublic(): RagPublicCitation =
        RagPublicCitation(
            citationId = citationId,
            sourceId = sourceId,
            title = title,
            sectionTitle = sectionTitle,
            canonicalUrl = canonicalUrl,
        )

    private fun hasCompleteCitationSet(
        stored: RagStoredEncryptedHistory,
        citations: List<RagCitation>,
    ): Boolean {
        if (
            stored.citationCount !in 0..5 ||
            citations.size != stored.citationCount ||
            citations.map { it.citationId } !=
            (1..stored.citationCount).map { "cit_$it" }
        ) {
            return false
        }
        return when (stored.generationStatus) {
            RagGenerationStatus.ANSWERED ->
                stored.citationCount > 0 &&
                    stored.citationCoverage == 1.0 &&
                    !stored.retrievalFailure
            RagGenerationStatus.RETRIEVAL_FAILURE ->
                stored.citationCount == 0 &&
                    stored.citationCoverage == 0.0 &&
                    stored.retrievalFailure
            else ->
                stored.citationCount == 0 &&
                    stored.citationCoverage == 0.0 &&
                    !stored.retrievalFailure
        }
    }

    private fun id(prefix: String): String = "${prefix}_${UUID.randomUUID().toString().replace("-", "")}"

    private companion object {
        val FLAG = Regex("^[A-Z0-9_]{1,64}$")
        val CHUNK_ID = Regex("^rag_chk_[0-9a-f]{32}$")
    }
}
