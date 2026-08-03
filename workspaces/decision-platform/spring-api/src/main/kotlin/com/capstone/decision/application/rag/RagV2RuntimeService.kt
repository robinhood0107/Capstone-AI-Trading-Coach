package com.capstone.decision.application.rag

import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.sql.ResultSet
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.UUID

@Service
class RagV2RuntimeService(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val cursorPort: RagHistoryCursorPort,
    private val cryptoPort: RagHistoryCryptoPort,
    private val evaluationPort: RagV2EvaluationPort,
    private val objectMapper: ObjectMapper,
) {
    /**
     * owner-private overlay 상태는 DB actor setting과 definer function으로만 읽는다.
     * 원본 파일명, 경로, hash receipt는 status API에 절대 노출하지 않는다.
     */
    @Transactional(readOnly = true)
    fun corpusStatus(ownerUserId: String): RagV2CorpusStatus {
        val jdbc = jdbc()
        setActor(ownerUserId)
        return jdbc
            .query(
                """
                SELECT *
                FROM read_rag_v2_corpus_status(:ownerUserId)
                """.trimIndent(),
                mapOf("ownerUserId" to ownerUserId),
            ) { result, _ ->
                RagV2CorpusStatus(
                    state = result.getString("state"),
                    publicCorpusVersion = result.getString("public_corpus_version"),
                    privateOverlayState = result.getString("private_overlay_state"),
                    progressPercent = result.getInt("progress_percent"),
                    failureCode = result.getString("failure_code"),
                )
            }.single()
    }

    /**
     * 외부 processor consent는 owner/session binding을 검증하는 definer function으로 append-only 기록한다.
     * internal DB identity와 client-visible event identity를 분리해 owner ID나 storage receipt를 API에 노출하지 않는다.
     */
    @Transactional
    fun recordExternalConsent(
        ownerUserId: String,
        command: RagV2ExternalConsentCommand,
    ) {
        val jdbc = jdbc()
        setActor(ownerUserId)
        jdbc.queryForObject(
            """
            SELECT record_rag_v2_immutable_consent_v2(
              :ownerUserId,
              :internalConsentEventId,
              :publicConsentEventId,
              :action,
              :disclosureDigest,
              :policyDigest,
              :processorSetDigest
            )
            """.trimIndent(),
            mapOf(
                "ownerUserId" to ownerUserId,
                "internalConsentEventId" to id("cns_v2"),
                "publicConsentEventId" to id("rce"),
                "action" to command.action,
                "disclosureDigest" to command.disclosureDigest,
                "policyDigest" to command.policyDigest,
                "processorSetDigest" to command.processorSetDigest,
            ),
            OffsetDateTime::class.java,
        )
    }

    /**
     * consent가 없으면 fabricated digest 없이 conflict로 fail-closed하며, 다른 owner event는 DB function이 읽지 못하게 한다.
     */
    @Transactional(readOnly = true)
    fun effectiveConsent(ownerUserId: String): RagV2EffectiveConsent {
        val jdbc = jdbc()
        setActor(ownerUserId)
        val stored =
            jdbc
                .query(
                    """
                    SELECT *
                    FROM read_rag_v2_immutable_effective_consent(:ownerUserId)
                    """.trimIndent(),
                    mapOf("ownerUserId" to ownerUserId),
                ) { result, _ ->
                    RagV2StoredEffectiveConsent(
                        consentEventId = result.getString("consent_event_id"),
                        action = result.getString("action"),
                        policyDigest = result.getString("policy_digest"),
                        processorSetDigest = result.getString("processor_set_digest"),
                    )
                }.singleOrNull()
                ?: throw RagV2ExternalConsentRequiredException()
        val state =
            when (stored.action) {
                "GRANT" -> RagV2ConsentState(effective = true, state = "GRANTED")
                "REVOKE" -> RagV2ConsentState(effective = false, state = "REVOKED")
                else -> throw RagGuardHistoryUnavailableException()
            }
        return RagV2EffectiveConsent(
            consentEventId = stored.consentEventId,
            effective = state.effective,
            policyDigest = stored.policyDigest,
            processorSetDigest = stored.processorSetDigest,
            state = state.state,
        )
    }

    /**
     * raw ticket capability는 caller에게 한 번만 반환하고 persistence에는 V25 function이 만든 hash만 보존한다.
     */
    @Transactional
    fun issueImportTicket(ownerUserId: String): RagV2ImportTicket {
        val jdbc = jdbc()
        val ticketId = id("rti")
        setActor(ownerUserId)
        val expiresAt =
            jdbc
                .queryForObject(
                    """
                    SELECT issue_rag_v2_immutable_import_ticket(
                      :ownerUserId,
                      :ticketId,
                      'OWNER_IMPORT',
                      'RAG_V2_OWNER_DOCUMENT_V1'
                    )
                    """.trimIndent(),
                    mapOf("ownerUserId" to ownerUserId, "ticketId" to ticketId),
                    OffsetDateTime::class.java,
                )?.toInstant() ?: throw RagGuardHistoryUnavailableException()
        return RagV2ImportTicket(
            ticketId = ticketId,
            issuedAt = expiresAt.minusSeconds(300),
            expiresAt = expiresAt,
        )
    }

    /**
     * v2는 full bundle이 준비되기 전 OA/private chunk를 빼고 답하지 않는다.
     * client가 corpus/profile/topK를 고르는 표면도 parser 단계에서 닫혀 있다.
     */
    @Transactional
    fun ask(
        ownerUserId: String,
        requestId: String,
        command: RagAskCommand,
    ): RagV2Answer {
        require(command.question.isNotBlank())
        val status = corpusStatus(ownerUserId)
        if (status.state != "FULL_READY") {
            throw RagV2CorpusNotReadyException()
        }
        val scope = issueRetrievalScope(ownerUserId, requestId, command.topics)
        val evaluation =
            evaluationPort.evaluate(
                command,
                RagV2EvaluationContext(requestId = requestId, ownerScopeClaim = scope.scopeClaimId),
            )
        requireBgeOnlyBoundary(evaluation, scope)
        if (evaluation.generationStatus != RagGenerationStatus.RETRIEVAL_ONLY) {
            return RagV2Answer(
                requestId = requestId,
                answerId = null,
                generationStatus = evaluation.generationStatus,
                answer = null,
                citationCoverage = evaluation.citationCoverage,
                citations = emptyList(),
                retrievalFailure = evaluation.retrievalFailure,
                guardrailFlags = evaluation.guardrailFlags,
            )
        }

        // transaction_timestamp를 AAD와 DB row에 같은 값으로 사용해 ciphertext row transplant를 막는다.
        val createdAt = databaseNow()
        val identity = RagHistoryIdentity(id("rag"), ownerUserId, createdAt)
        val encrypted =
            cryptoPort.encrypt(
                identity = identity,
                question = command.question,
                // v2 BGE는 answer generator가 아니다. AES-GCM으로 빈 값을 암호화해 history shape만 보존한다.
                answer = "",
            )
        val canonicalCitations = persistRetrievalOnlyHistory(identity, requestId, command, scope, evaluation, encrypted)
        return RagV2Answer(
            requestId = requestId,
            answerId = identity.answerId,
            generationStatus = RagGenerationStatus.RETRIEVAL_ONLY,
            answer = null,
            citationCoverage = 1.0,
            citations = canonicalCitations,
            retrievalFailure = false,
            guardrailFlags = emptyList(),
        )
    }

    @Transactional(readOnly = true)
    fun listHistory(
        ownerUserId: String,
        cursor: String?,
        limit: Int,
    ): RagV2HistoryPage {
        val point = cursor?.let { cursorPort.decode(ownerUserId, it) }
        val jdbc = jdbc()
        setActor(ownerUserId)
        val rows =
            jdbc.query(
                """
                SELECT *
                FROM read_rag_v2_history_metadata(
                  :ownerUserId,
                  :cursorCreatedAt,
                  :cursorAnswerId,
                  :limit
                )
                """.trimIndent(),
                mapOf(
                    "ownerUserId" to ownerUserId,
                    "cursorCreatedAt" to
                        point?.createdAt?.let {
                            OffsetDateTime.ofInstant(it, ZoneOffset.UTC)
                        },
                    "cursorAnswerId" to point?.answerId,
                    "limit" to limit + 1,
                ),
            ) { result, _ ->
                RagV2HistoryMetadata(
                    answerId = result.getString("answer_id"),
                    createdAt = result.instant("created_at"),
                    expiresAt = result.instant("expires_at"),
                    generationStatus = RagGenerationStatus.valueOf(result.getString("generation_status")),
                )
            }
        val items = rows.take(limit)
        val nextCursor =
            rows
                .drop(limit)
                .firstOrNull()
                ?.let { cursorPort.encode(ownerUserId, RagHistoryCursorPoint(it.createdAt, it.answerId)) }
        return RagV2HistoryPage(items = items, nextCursor = nextCursor)
    }

    @Transactional(readOnly = true)
    fun getHistory(
        ownerUserId: String,
        answerId: String,
    ): RagV2HistoryDetail {
        val jdbc = jdbc()
        setActor(ownerUserId)
        return jdbc
            .query(
                """
                SELECT *
                FROM read_rag_v2_history_detail(:ownerUserId, :answerId)
                """.trimIndent(),
                mapOf("ownerUserId" to ownerUserId, "answerId" to answerId),
            ) { result, _ ->
                result.toHistoryDetail(ownerUserId)
            }.singleOrNull()
            ?: throw RagHistoryNotFoundException()
    }

    @Transactional
    fun deleteHistory(
        ownerUserId: String,
        answerId: String,
    ) {
        val jdbc = jdbc()
        setActor(ownerUserId)
        jdbc.queryForObject(
            "SELECT delete_owned_rag_v2_history(:ownerUserId, :answerId)",
            mapOf("ownerUserId" to ownerUserId, "answerId" to answerId),
            Any::class.java,
        )
    }

    private fun ResultSet.toHistoryDetail(ownerUserId: String): RagV2HistoryDetail {
        val identity =
            RagHistoryIdentity(
                answerId = getString("answer_id"),
                ownerUserId = ownerUserId,
                createdAt = instant("created_at"),
            )
        val encrypted =
            RagEncryptedHistoryPayload(
                kekVersion = getString("kek_version"),
                wrapNonce = getBytes("wrap_nonce"),
                wrappedDek = getBytes("wrapped_dek"),
                wrapTag = getBytes("wrap_tag"),
                question =
                    RagEncryptedFieldPayload(
                        nonce = getBytes("question_nonce"),
                        ciphertext = getBytes("question_ciphertext"),
                        tag = getBytes("question_tag"),
                    ),
                answer =
                    RagEncryptedFieldPayload(
                        nonce = getBytes("answer_nonce"),
                        ciphertext = getBytes("answer_ciphertext"),
                        tag = getBytes("answer_tag"),
                    ),
            )
        val decrypted = cryptoPort.decrypt(identity, encrypted)
        return RagV2HistoryDetail(
            answerId = identity.answerId,
            question = decrypted.question,
            // retrieval-only row는 intentional empty AES-GCM payload이며 LLM answer가 없다는 contract를 유지한다.
            answer = decrypted.answer.takeIf { getString("generation_status") == RagGenerationStatus.ANSWERED.name },
            generationStatus = RagGenerationStatus.valueOf(getString("generation_status")),
            citations = citations(getString("citations")),
            createdAt = identity.createdAt,
            expiresAt = instant("expires_at"),
        )
    }

    /**
     * claim은 decision_app만 발급한다. Python에는 owner ID가 아니라 opaque claim만 전달되며 DB가 현재
     * immutable pointer/profile/topic을 capture한다.
     */
    private fun issueRetrievalScope(
        ownerUserId: String,
        requestId: String,
        topics: List<String>,
    ): RagV2RetrievalScope {
        val jdbc = jdbc()
        setActor(ownerUserId)
        val topicsJson = objectMapper.writeValueAsString(topics)
        return jdbc
            .query(
                """
                SELECT *
                FROM issue_rag_v2_retrieval_scope(
                  :ownerUserId,
                  :requestId,
                  ARRAY(SELECT jsonb_array_elements_text(CAST(:topicsJson AS jsonb)))
                )
                """.trimIndent(),
                mapOf(
                    "ownerUserId" to ownerUserId,
                    "requestId" to requestId,
                    "topicsJson" to topicsJson,
                ),
            ) { result, _ ->
                RagV2RetrievalScope(
                    scopeClaimId = result.getString("scope_claim_id"),
                    exact30GenerationId = result.getString("exact30_generation_id"),
                    oa112GenerationId = result.getString("oa112_generation_id"),
                    ownerGenerationId = result.getString("owner_private_generation_id"),
                    embeddingProfileId = result.getString("embedding_profile_id"),
                    policyVersion = result.getLong("policy_version"),
                )
            }.singleOrNull()
            ?: throw RagGuardHistoryUnavailableException()
    }

    /**
     * gRPC adapter도 검증하지만 persistence 직전 같은 low-authority invariants를 다시 확인한다.
     * 특히 v2 path는 Vertex/Voyage/OpenAI 생성 또는 provider fallback을 절대 허용하지 않는다.
     */
    private fun requireBgeOnlyBoundary(
        evaluation: RagV2EvaluationResult,
        scope: RagV2RetrievalScope,
    ) {
        require(evaluation.providerPhysicalAttempts == 0)
        require(evaluation.geminiPhysicalCalls == 0)
        require(evaluation.openAiPhysicalCalls == 0)
        require(evaluation.voyagePhysicalCalls == 0)
        require(!evaluation.externalProviderCandidate)
        require(evaluation.citationCoverage in 0.0..1.0)
        require(evaluation.guardrailFlags.size <= MAX_GUARDRAIL_FLAGS)
        require(evaluation.guardrailFlags.distinct().size == evaluation.guardrailFlags.size)
        require(evaluation.guardrailFlags.all(FLAG::matches))
        require(evaluation.citations.size <= MAX_CITATIONS)
        when (evaluation.generationStatus) {
            RagGenerationStatus.RETRIEVAL_ONLY -> {
                require(evaluation.answer == null)
                require(evaluation.citations.isNotEmpty())
                require(evaluation.citationCoverage == 1.0)
                require(!evaluation.retrievalFailure)
                require(evaluation.guardrailFlags.isEmpty())
                require(evaluation.failureCode.isEmpty())
                require(evaluation.exact30GenerationId == scope.exact30GenerationId)
                require(evaluation.oa112GenerationId == scope.oa112GenerationId)
                require(evaluation.ownerGenerationId == scope.ownerGenerationId)
                require(evaluation.embeddingProfileId == scope.embeddingProfileId)
                require(evaluation.policyVersion == scope.policyVersion)
                evaluation.citations.forEachIndexed { index, citation ->
                    require(citation.citationId == "cit_${index + 1}")
                    require(CHUNK_ID.matches(citation.chunkRevisionId))
                    require(citation.generationId in setOf(scope.exact30GenerationId, scope.oa112GenerationId, scope.ownerGenerationId))
                }
            }
            RagGenerationStatus.RETRIEVAL_FAILURE ->
                require(
                    evaluation.answer == null &&
                        evaluation.citations.isEmpty() &&
                        evaluation.citationCoverage == 0.0 &&
                        evaluation.retrievalFailure &&
                        FAILURE_CODE.matches(evaluation.failureCode),
                )
            RagGenerationStatus.BLOCKED_SENSITIVE,
            RagGenerationStatus.BLOCKED_ADVICE,
            RagGenerationStatus.GENERATION_UNAVAILABLE,
            ->
                require(
                    evaluation.answer == null &&
                        evaluation.citations.isEmpty() &&
                        evaluation.citationCoverage == 0.0 &&
                        !evaluation.retrievalFailure &&
                        FAILURE_CODE.matches(evaluation.failureCode),
                )
            // BGE-only v2 cannot return an LLM-generated answer before the separately gated Vertex path exists.
            RagGenerationStatus.ANSWERED -> throw RagGuardHistoryUnavailableException()
        }
    }

    /**
     * V35 receives citation identities only, resolves safe title/document metadata itself, and atomically writes
     * the encrypted row. Canonical raw text, local path, and gRPC display data are never SQL inputs here.
     */
    private fun persistRetrievalOnlyHistory(
        identity: RagHistoryIdentity,
        requestId: String,
        command: RagAskCommand,
        scope: RagV2RetrievalScope,
        evaluation: RagV2EvaluationResult,
        encrypted: RagEncryptedHistoryPayload,
    ): List<JsonNode> {
        val receipt =
            objectMapper.writeValueAsString(
                evaluation.citations.mapIndexed { index, citation ->
                    linkedMapOf(
                        "ordinal" to index + 1,
                        "citationId" to citation.citationId,
                        "sourceId" to citation.sourceId,
                        "sourceRevisionId" to citation.sourceRevisionId,
                        "chunkRevisionId" to citation.chunkRevisionId,
                        "generationId" to citation.generationId,
                        "citationKind" to citation.citationKind,
                    )
                },
            )
        val canonical =
            jdbc().queryForObject(
                """
                SELECT persist_rag_v2_immutable_retrieval_history(
                  :ownerUserId, :answerId, :requestId, :answerMode, :sessionId, :scopeClaimId,
                  1.0, ARRAY[]::text[], :kekVersion,
                  :wrapNonce, :wrappedDek, :wrapTag,
                  :questionNonce, :questionCiphertext, :questionTag,
                  :answerNonce, :answerCiphertext, :answerTag,
                  :createdAt, CAST(:citations AS jsonb)
                )
                """.trimIndent(),
                mapOf(
                    "ownerUserId" to identity.ownerUserId,
                    "answerId" to identity.answerId,
                    "requestId" to requestId,
                    "answerMode" to command.answerMode.name,
                    "sessionId" to requestId,
                    "scopeClaimId" to scope.scopeClaimId,
                    "kekVersion" to encrypted.kekVersion,
                    "wrapNonce" to encrypted.wrapNonce,
                    "wrappedDek" to encrypted.wrappedDek,
                    "wrapTag" to encrypted.wrapTag,
                    "questionNonce" to encrypted.question.nonce,
                    "questionCiphertext" to encrypted.question.ciphertext,
                    "questionTag" to encrypted.question.tag,
                    "answerNonce" to encrypted.answer.nonce,
                    "answerCiphertext" to encrypted.answer.ciphertext,
                    "answerTag" to encrypted.answer.tag,
                    "createdAt" to OffsetDateTime.ofInstant(identity.createdAt, ZoneOffset.UTC),
                    "citations" to receipt,
                ),
                String::class.java,
            ) ?: throw RagGuardHistoryUnavailableException()
        return citations(canonical)
    }

    private fun databaseNow(): java.time.Instant =
        jdbc()
            .queryForObject(
                "SELECT transaction_timestamp()",
                emptyMap<String, Any>(),
                OffsetDateTime::class.java,
            )?.toInstant()
            ?: throw RagGuardHistoryUnavailableException()

    private fun citations(value: String): List<JsonNode> =
        objectMapper
            .readTree(value)
            .values()
            .asSequence()
            .toList()

    private fun setActor(ownerUserId: String) {
        jdbc().queryForObject(
            "SELECT set_config('app.actor_user_id', :ownerUserId, true)",
            mapOf("ownerUserId" to ownerUserId),
            String::class.java,
        )
    }

    private fun ResultSet.instant(column: String) = getObject(column, OffsetDateTime::class.java).toInstant()

    private fun id(prefix: String): String = "${prefix}_${UUID.randomUUID().toString().replace("-", "")}"

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: throw RagGuardHistoryUnavailableException()

    private data class RagV2StoredEffectiveConsent(
        val consentEventId: String,
        val action: String,
        val policyDigest: String,
        val processorSetDigest: String,
    )

    private data class RagV2ConsentState(
        val effective: Boolean,
        val state: String,
    )

    private companion object {
        val FLAG = Regex("^[A-Z0-9_]{1,64}$")
        val FAILURE_CODE = Regex("^[A-Z0-9_]{1,96}$")
        val CHUNK_ID = Regex("^rag_v2_chk_[0-9a-f]{32}$")
        const val MAX_CITATIONS = 5
        const val MAX_GUARDRAIL_FLAGS = 8
    }
}
