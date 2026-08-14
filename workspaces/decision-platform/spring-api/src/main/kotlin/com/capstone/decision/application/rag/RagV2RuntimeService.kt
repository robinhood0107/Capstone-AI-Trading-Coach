package com.capstone.decision.application.rag

import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.transaction.PlatformTransactionManager
import org.springframework.transaction.annotation.Transactional
import org.springframework.transaction.support.TransactionTemplate
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.sql.ResultSet
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.UUID

/** internal DB projection for the packet-resumable two-minute Vertex retrieval scope. */
internal data class RagV2PreparedScope(
    val scope: RagV2RetrievalScope,
    val expiresAt: java.time.Instant,
)

private data class RagV2AskPreparation(
    val scope: RagV2RetrievalScope,
    val externalQueryConsentGranted: Boolean,
)

private data class RagV2VertexProviderInput(
    val consent: RagV2EffectiveConsent,
    val evidence: List<RagV2VertexEvidence>,
)

/**
 * 갱신된 외부 generation 동의가 없는 구형 경로의 호환 검사용 함수다. S4.9 runtime은 effective consent를
 * 재검증하므로 BGE embedding이라는 이유만으로 owner evidence를 자동 폐기하지 않는다.
 */
internal fun requiresRetrievalOnlyForOwnerBgeEvidence(
    scope: RagV2RetrievalScope,
    citations: List<RagV2RetrievedCitation>,
): Boolean =
    scope.ownerEmbeddingProfileId == "bge_m3_local_1024_v1" &&
        citations.any { it.documentId != null }

@Service
class RagV2RuntimeService(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val cursorPort: RagHistoryCursorPort,
    private val cryptoPort: RagHistoryCryptoPort,
    private val evaluationPort: RagV2EvaluationPort,
    private val vertexEvidencePort: RagV2VertexEvidencePort,
    private val vertexGenerationPort: RagV2VertexGenerationPort,
    private val vertexQuestionFingerprintPort: RagV2VertexQuestionFingerprintPort,
    private val objectMapper: ObjectMapper,
    private val transactionManagerProvider: ObjectProvider<PlatformTransactionManager>,
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

    /** raw ticket과 선택 profile을 함께 반환하고 persistence에는 capability hash만 보존한다. */
    @Transactional
    fun issueImportTicket(
        ownerUserId: String,
        embeddingProfileId: String,
    ): RagV2ImportTicket {
        val jdbc = jdbc()
        val ticketId = id("rti")
        setActor(ownerUserId)
        val expiresAt =
            jdbc
                .queryForObject(
                    """
                    SELECT issue_rag_v2_immutable_import_ticket_v2(
                      :ownerUserId,
                      :ticketId,
                      'OWNER_IMPORT',
                      'RAG_V2_OWNER_DOCUMENT_V2',
                      :embeddingProfileId
                    )
                    """.trimIndent(),
                    mapOf(
                        "ownerUserId" to ownerUserId,
                        "ticketId" to ticketId,
                        "embeddingProfileId" to embeddingProfileId,
                    ),
                    OffsetDateTime::class.java,
                )?.toInstant() ?: throw RagGuardHistoryUnavailableException()
        return RagV2ImportTicket(
            ticketId = ticketId,
            embeddingProfileId = embeddingProfileId,
            issuedAt = expiresAt.minusSeconds(300),
            expiresAt = expiresAt,
        )
    }

    /**
     * hard-delete ticket은 authenticated owner와 one document를 DB transaction에서 bind한다.
     * raw ticket은 이 response 한 번에만 존재하고 persistence에는 V44 security-definer가 만든 hash만 남긴다.
     */
    @Transactional
    fun issueDeleteTicket(
        ownerUserId: String,
        documentId: String,
    ): RagV2DeleteTicket {
        val jdbc = jdbc()
        val ticketId = id("rtd")
        setActor(ownerUserId)
        val expiresAt =
            jdbc
                .queryForObject(
                    """
                    SELECT issue_rag_v2_immutable_owner_delete_ticket(
                      :ownerUserId,
                      :documentId,
                      :ticketId
                    )
                    """.trimIndent(),
                    mapOf(
                        "ownerUserId" to ownerUserId,
                        "documentId" to documentId,
                        "ticketId" to ticketId,
                    ),
                    OffsetDateTime::class.java,
                )?.toInstant() ?: throw RagGuardHistoryUnavailableException()
        return RagV2DeleteTicket(
            ticketId = ticketId,
            documentId = documentId,
            issuedAt = expiresAt.minusSeconds(300),
            expiresAt = expiresAt,
        )
    }

    /**
     * authenticated owner가 same request ID로 실행할 Vertex one-shot packet을 만들기 위한 content-free
     * preparation이다. raw question/evidence를 persistence에 새로 만들지 않고 provider 전용 five-minute scope와
     * purpose-separated HMAC만 반환하며, enabled generator가 없으면 provider preparation도 열지 않는다.
     */
    @Transactional
    fun prepareVertexGeneration(
        ownerUserId: String,
        requestId: String,
        command: RagAskCommand,
    ): RagV2VertexPreparation {
        if (!vertexGenerationPort.isActivationEnabled()) {
            throw RagV2VertexPreparationUnavailableException()
        }
        val status = corpusStatus(ownerUserId)
        if (status.state != "FULL_READY") {
            throw RagV2CorpusNotReadyException()
        }
        val consent = effectiveConsent(ownerUserId)
        if (!consent.effective) {
            throw RagV2ExternalConsentRequiredException()
        }
        val scope = issueRetrievalScope(ownerUserId, requestId, command.topics, providerPreparation = true)
        val preparedScope = readVertexPreparedScope(ownerUserId, requestId, scope.scopeClaimId, command.topics)
        require(preparedScope.scope == scope)
        return RagV2VertexPreparation(
            requestId = requestId,
            scopeClaimId = scope.scopeClaimId,
            questionFingerprintHmac =
                vertexQuestionFingerprintPort.fingerprint(
                    ownerUserId = ownerUserId,
                    command = command,
                ),
            answerMode = command.answerMode,
            embeddingProfileId = scope.embeddingProfileId,
            consentEventId = consent.consentEventId,
            policyDigest = consent.policyDigest,
            processorSetDigest = consent.processorSetDigest,
            expiresAt = preparedScope.expiresAt,
        )
    }

    /**
     * v2는 full bundle이 준비되기 전 OA/private chunk를 빼고 답하지 않는다.
     * client가 corpus/profile/topK를 고르는 표면도 parser 단계에서 닫혀 있다.
     */
    fun ask(
        ownerUserId: String,
        requestId: String,
        command: RagAskCommand,
        vertexScopeClaimId: String? = null,
    ): RagV2Answer {
        val vertexEnabled = vertexGenerationPort.isActivationEnabled()
        if (vertexEnabled && vertexScopeClaimId == null) {
            // packet에 맞는 stable scope가 없으면 gRPC/provider socket까지 진행하지 않는다.
            return vertexUnavailableAnswer(requestId)
        }
        val preparation =
            inDatabaseTransaction {
                require(command.question.isNotBlank())
                val status = corpusStatus(ownerUserId)
                if (status.state != "FULL_READY") {
                    throw RagV2CorpusNotReadyException()
                }
                if (!vertexEnabled && vertexScopeClaimId != null) {
                    throw RagV2VertexPreparationUnavailableException()
                }
                val scope =
                    if (vertexScopeClaimId == null) {
                        issueRetrievalScope(ownerUserId, requestId, command.topics)
                    } else {
                        readVertexPreparedScope(ownerUserId, requestId, vertexScopeClaimId, command.topics).scope
                    }
                // provider 호출 전 DB actor/scope/consent read를 한 짧은 transaction에서 닫는다.
                val externalQueryConsentGranted =
                    if (scope.embeddingProfileId == VOYAGE_PROFILE) {
                        effectiveConsent(ownerUserId)
                            .takeIf { it.effective }
                            ?.let { true }
                            ?: throw RagV2ExternalConsentRequiredException()
                    } else {
                        false
                    }
                RagV2AskPreparation(scope, externalQueryConsentGranted)
            }
        val scope = preparation.scope
        val externalQueryConsentGranted = preparation.externalQueryConsentGranted
        val evaluation =
            evaluationPort.evaluate(
                command,
                RagV2EvaluationContext(
                    requestId = requestId,
                    ownerScopeClaim = scope.scopeClaimId,
                    externalQueryConsentGranted = externalQueryConsentGranted,
                ),
            )
        requireProfileSelectedRetrievalBoundary(
            evaluation,
            scope,
            externalQueryConsentGranted,
        )
        if (evaluation.generationStatus != RagGenerationStatus.RETRIEVAL_ONLY) {
            return terminalAnswer(
                requestId = requestId,
                generationStatus = evaluation.generationStatus,
                citationCoverage = evaluation.citationCoverage,
                retrievalFailure = evaluation.retrievalFailure,
                guardrailFlags = evaluation.guardrailFlags,
            )
        }
        if (vertexEnabled) {
            return generateWithVertex(ownerUserId, requestId, command, scope, evaluation)
        }

        return persistRetrievalOnlyAnswer(ownerUserId, requestId, command, scope, evaluation)
    }

    /**
     * MCP search는 답변 생성·history 저장을 수행하지 않고 현재 owner scope의 Top-5와 canonical evidence만 반환한다.
     * Voyage query가 필요한 profile이면 갱신된 effective consent를 통과한 호출 한 번만 evaluation adapter가 소유한다.
     */
    fun searchEvidence(
        ownerUserId: String,
        requestId: String,
        command: RagAskCommand,
        includeOwner: Boolean = true,
    ): RagV2SearchEvidenceResult {
        val preparation =
            inDatabaseTransaction {
                require(command.question.isNotBlank())
                if (corpusStatus(ownerUserId).state != "FULL_READY") throw RagV2CorpusNotReadyException()
                val scope = issueMcpRetrievalScope(ownerUserId, requestId, command.topics, includeOwner)
                val consentGranted =
                    if (scope.embeddingProfileId == VOYAGE_PROFILE) {
                        effectiveConsent(ownerUserId).effective.also { require(it) }
                    } else {
                        false
                    }
                RagV2AskPreparation(scope, consentGranted)
            }
        val evaluation =
            evaluationPort.evaluate(
                command,
                RagV2EvaluationContext(requestId, preparation.scope.scopeClaimId, preparation.externalQueryConsentGranted),
            )
        requireProfileSelectedRetrievalBoundary(evaluation, preparation.scope, preparation.externalQueryConsentGranted)
        require(evaluation.generationStatus == RagGenerationStatus.RETRIEVAL_ONLY)
        val evidence =
            inDatabaseTransaction {
                vertexEvidencePort.resolve(ownerUserId, requestId, preparation.scope, evaluation.citations)
            }
        return RagV2SearchEvidenceResult(preparation.scope, evaluation.citations, evidence)
    }

    /** MCP context를 재사용할 때마다 active scope/owner generation을 다시 resolve해 delete·pointer drift를 즉시 거부한다. */
    fun requireResearchEvidenceCurrent(
        ownerUserId: String,
        requestId: String,
        scope: RagV2RetrievalScope,
        citations: List<RagV2RetrievedCitation>,
        expectedEvidence: List<RagV2VertexEvidence>,
    ) {
        val current = inDatabaseTransaction { vertexEvidencePort.resolve(ownerUserId, requestId, scope, citations) }
        require(
            current.map { it.citationId to it.canonicalTextSha256 } ==
                expectedEvidence.map { it.citationId to it.canonicalTextSha256 },
        )
    }

    /**
     * disabled Vertex 환경에서도 BGE-only evidence retrieval은 local RAG 기능으로 남는다. 이 경로는 외부 consent,
     * canonical text, provider transport를 전혀 읽지 않고 empty encrypted answer로 retrieval receipt만 보존한다.
     */
    private fun persistRetrievalOnlyAnswer(
        ownerUserId: String,
        requestId: String,
        command: RagAskCommand,
        scope: RagV2RetrievalScope,
        evaluation: RagV2EvaluationResult,
    ): RagV2Answer =
        inDatabaseTransaction {
            setActor(ownerUserId)
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
            val canonicalCitations =
                persistRetrievalOnlyHistory(identity, requestId, command, scope, evaluation, encrypted)
            RagV2Answer(
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

    /**
     * Vertex route는 current activation·owner consent·external eligibility를 모두 통과할 때만 top-5 text를
     * transiently 전달한다. 각 provider failure는 raw cause 없이 typed unavailable로 수렴하며 fallback은 없다.
     */
    private fun generateWithVertex(
        ownerUserId: String,
        requestId: String,
        command: RagAskCommand,
        scope: RagV2RetrievalScope,
        evaluation: RagV2EvaluationResult,
    ): RagV2Answer {
        val providerInput =
            try {
                inDatabaseTransaction {
                    val consent = effectiveConsent(ownerUserId)
                    if (!consent.effective) {
                        throw RagV2ExternalConsentRequiredException()
                    }
                    RagV2VertexProviderInput(
                        consent,
                        vertexEvidencePort.resolve(ownerUserId, requestId, scope, evaluation.citations),
                    )
                }
            } catch (_: RagV2VertexEvidenceUnavailableException) {
                return vertexUnavailableAnswer(requestId)
            }
        val consent = providerInput.consent
        val evidence = providerInput.evidence
        val generation =
            vertexGenerationPort.generate(
                RagV2VertexGenerationCommand(
                    ownerUserId = ownerUserId,
                    requestId = requestId,
                    question = command.question,
                    answerMode = command.answerMode,
                    relatedSymbols = command.relatedSymbols,
                    topics = command.topics,
                    scope = scope,
                    consent = consent,
                    evidence = evidence,
                ),
            )
        requireVertexGenerationBoundary(generation, evidence)
        if (generation.generationStatus != RagGenerationStatus.ANSWERED) {
            return terminalAnswer(
                requestId = requestId,
                generationStatus = generation.generationStatus,
                citationCoverage = 0.0,
                retrievalFailure = false,
                guardrailFlags =
                    listOfNotNull(
                        generation.failureCode.takeIf { it.isNotBlank() },
                        "INSUFFICIENT_EVIDENCE".takeIf {
                            generation.answerBasis == StrongLlmAnswerBasis.INSUFFICIENT_EVIDENCE
                        },
                    ),
            )
        }

        val basis = requireNotNull(generation.answerBasis)
        val citedEvidence = selectedStrongLlmCitations(evaluation.citations, generation.citationIds, basis)
        return inDatabaseTransaction {
            setActor(ownerUserId)
            val createdAt = databaseNow()
            val identity = RagHistoryIdentity(id("rag"), ownerUserId, createdAt)
            val encrypted =
                cryptoPort.encrypt(
                    identity = identity,
                    question = command.question,
                    answer = requireNotNull(generation.answer),
                )
            val canonicalCitations =
                persistStrongLlmAnsweredHistory(
                    identity,
                    requestId,
                    command,
                    scope,
                    basis,
                    generation.citationCoverage,
                    generation.warnings,
                    citedEvidence,
                    encrypted,
                )
            RagV2Answer(
                requestId = requestId,
                answerId = identity.answerId,
                generationStatus = RagGenerationStatus.ANSWERED,
                answer = generation.answer,
                citationCoverage = generation.citationCoverage,
                citations = canonicalCitations,
                retrievalFailure = false,
                guardrailFlags =
                    buildList {
                        if (generation.answerBasis == StrongLlmAnswerBasis.MODEL_KNOWLEDGE) {
                            add("MODEL_KNOWLEDGE_ONLY")
                        }
                        addAll(generation.warnings)
                    },
            )
        }
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
        providerPreparation: Boolean = false,
    ): RagV2RetrievalScope {
        val jdbc = jdbc()
        setActor(ownerUserId)
        val topicsJson = objectMapper.writeValueAsString(topics)
        val issuerSql =
            if (providerPreparation) {
                """
                SELECT *
                FROM issue_rag_v2_retrieval_scope_v3(
                  :ownerUserId,
                  :requestId,
                  ARRAY(SELECT jsonb_array_elements_text(CAST(:topicsJson AS jsonb)))
                )
                """.trimIndent()
            } else {
                """
                SELECT *
                FROM issue_rag_v2_retrieval_scope_v2(
                  :ownerUserId,
                  :requestId,
                  ARRAY(SELECT jsonb_array_elements_text(CAST(:topicsJson AS jsonb)))
                )
                """.trimIndent()
            }
        return jdbc
            .query(
                issuerSql,
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
                    ownerEmbeddingProfileId = result.getString("owner_embedding_profile_id"),
                )
            }.singleOrNull()
            ?: throw RagGuardHistoryUnavailableException()
    }

    /** MCP OAuth owner scope를 DB claim 발급 전에 결박해 public-only 호출이 owner pointer를 읽지 못하게 한다. */
    private fun issueMcpRetrievalScope(
        ownerUserId: String,
        requestId: String,
        topics: List<String>,
        includeOwner: Boolean,
    ): RagV2RetrievalScope {
        val jdbc = jdbc()
        setActor(ownerUserId)
        val topicsJson = objectMapper.writeValueAsString(topics)
        return jdbc
            .query(
                """
                SELECT *
                FROM issue_s4_9_mcp_retrieval_scope(
                  :ownerUserId,
                  :requestId,
                  ARRAY(SELECT jsonb_array_elements_text(CAST(:topicsJson AS jsonb))),
                  :includeOwner
                )
                """.trimIndent(),
                mapOf(
                    "ownerUserId" to ownerUserId,
                    "requestId" to requestId,
                    "topicsJson" to topicsJson,
                    "includeOwner" to includeOwner,
                ),
            ) { result, _ ->
                RagV2RetrievalScope(
                    scopeClaimId = result.getString("scope_claim_id"),
                    exact30GenerationId = result.getString("exact30_generation_id"),
                    oa112GenerationId = result.getString("oa112_generation_id"),
                    ownerGenerationId = result.getString("owner_private_generation_id"),
                    embeddingProfileId = result.getString("embedding_profile_id"),
                    policyVersion = result.getLong("policy_version"),
                    ownerEmbeddingProfileId = result.getString("owner_embedding_profile_id"),
                )
            }.singleOrNull()
            ?: throw RagGuardHistoryUnavailableException()
    }

    /**
     * Vertex packet은 request ID와 exact topic set으로 미리 발급된 five-minute provider claim만 재사용한다.
     * client-supplied profile/owner selector는 받지 않으며 DB function이 current bundle/owner pointer를 다시
     * 검증하므로 stale preparation은 gRPC 또는 provider call 전에 닫힌다.
     */
    private fun readVertexPreparedScope(
        ownerUserId: String,
        requestId: String,
        scopeClaimId: String,
        topics: List<String>,
    ): RagV2PreparedScope {
        val jdbc = jdbc()
        setActor(ownerUserId)
        val topicsJson = objectMapper.writeValueAsString(topics)
        return jdbc
            .query(
                """
                SELECT *
                FROM read_rag_v2_vertex_prepared_scope_v2(
                  :ownerUserId,
                  :requestId,
                  :scopeClaimId,
                  ARRAY(SELECT jsonb_array_elements_text(CAST(:topicsJson AS jsonb)))
                )
                """.trimIndent(),
                mapOf(
                    "ownerUserId" to ownerUserId,
                    "requestId" to requestId,
                    "scopeClaimId" to scopeClaimId,
                    "topicsJson" to topicsJson,
                ),
            ) { result, _ ->
                RagV2PreparedScope(
                    scope =
                        RagV2RetrievalScope(
                            scopeClaimId = result.getString("scope_claim_id"),
                            exact30GenerationId = result.getString("exact30_generation_id"),
                            oa112GenerationId = result.getString("oa112_generation_id"),
                            ownerGenerationId = result.getString("owner_private_generation_id"),
                            embeddingProfileId = result.getString("embedding_profile_id"),
                            policyVersion = result.getLong("policy_version"),
                            ownerEmbeddingProfileId = result.getString("owner_embedding_profile_id"),
                        ),
                    expiresAt = result.getObject("expires_at", OffsetDateTime::class.java).toInstant(),
                )
            }.singleOrNull()
            ?: throw RagV2VertexPreparationUnavailableException()
    }

    /**
     * frozen gRPC adapter도 검증하지만 Spring은 retrieval receipt를 다시 low-authority boundary로 확인한다.
     * profile은 DB scope가 고른 하나만 허용하며, local fixture/BGE path에는 provider fallback이 절대 섞일 수 없다.
     */
    private fun requireProfileSelectedRetrievalBoundary(
        evaluation: RagV2EvaluationResult,
        scope: RagV2RetrievalScope,
        externalQueryConsentGranted: Boolean,
    ) {
        require(evaluation.geminiPhysicalCalls == 0)
        require(evaluation.openAiPhysicalCalls == 0)
        require(!evaluation.externalProviderCandidate)
        require(
            when (scope.embeddingProfileId) {
                BGE_PROFILE ->
                    evaluation.providerPhysicalAttempts == 0 &&
                        evaluation.voyagePhysicalCalls == 0
                VOYAGE_PROFILE ->
                    evaluation.providerPhysicalAttempts == evaluation.voyagePhysicalCalls &&
                        evaluation.voyagePhysicalCalls in 0..1 &&
                        (evaluation.voyagePhysicalCalls == 0 || externalQueryConsentGranted)
                else -> false
            },
        )
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
            // BGE gRPC response에는 single-generator가 생긴 뒤에도 answer surface가 존재하지 않는다.
            RagGenerationStatus.ANSWERED -> throw RagGuardHistoryUnavailableException()
        }
    }

    /**
     * Vertex adapter는 strict JSON을 이미 검사하지만 service도 status·citation subset·byte ceiling을 다시 확인한다.
     * failure code는 provider cause를 담지 않는 typed allowlist만 허용한다.
     */
    private fun requireVertexGenerationBoundary(
        generation: RagV2VertexGenerationResult,
        evidence: List<RagV2VertexEvidence>,
    ) {
        val evidenceCitationIds = evidence.map { it.citationId }.toSet()
        require(evidence.size in 1..MAX_CITATIONS)
        when (generation.generationStatus) {
            RagGenerationStatus.ANSWERED -> {
                val answer = requireNotNull(generation.answer)
                require(answer.toByteArray(Charsets.UTF_8).size in 1..8192)
                require(generation.answerBasis in setOf(StrongLlmAnswerBasis.EVIDENCE, StrongLlmAnswerBasis.MODEL_KNOWLEDGE))
                if (generation.answerBasis == StrongLlmAnswerBasis.EVIDENCE) {
                    require(generation.citationIds.isNotEmpty() && generation.citationCoverage in 0.8..1.0)
                } else {
                    require(generation.citationIds.isEmpty() && generation.citationCoverage == 0.0)
                }
                require(generation.citationIds.size <= evidence.size)
                require(generation.citationIds.distinct().size == generation.citationIds.size)
                require(generation.citationIds.all { it in evidenceCitationIds })
                require(generation.failureCode.isEmpty())
            }
            RagGenerationStatus.GENERATION_UNAVAILABLE,
            RagGenerationStatus.BLOCKED_SENSITIVE,
            RagGenerationStatus.BLOCKED_ADVICE,
            ->
                require(
                    generation.answer == null &&
                        generation.citationIds.isEmpty() &&
                        FAILURE_CODE.matches(generation.failureCode),
                )
            RagGenerationStatus.RETRIEVAL_ONLY ->
                require(
                    generation.answer == null &&
                        generation.citationIds.isEmpty() &&
                        generation.failureCode.isEmpty() &&
                        generation.answerBasis == StrongLlmAnswerBasis.INSUFFICIENT_EVIDENCE,
                )
            else -> throw RagGuardHistoryUnavailableException()
        }
    }

    private fun terminalAnswer(
        requestId: String,
        generationStatus: RagGenerationStatus,
        citationCoverage: Double,
        retrievalFailure: Boolean,
        guardrailFlags: List<String>,
    ): RagV2Answer =
        RagV2Answer(
            requestId = requestId,
            answerId = null,
            generationStatus = generationStatus,
            answer = null,
            citationCoverage = citationCoverage,
            citations = emptyList(),
            retrievalFailure = retrievalFailure,
            guardrailFlags = guardrailFlags,
        )

    private fun vertexUnavailableAnswer(requestId: String): RagV2Answer =
        terminalAnswer(
            requestId = requestId,
            generationStatus = RagGenerationStatus.GENERATION_UNAVAILABLE,
            citationCoverage = 0.0,
            retrievalFailure = false,
            guardrailFlags = listOf("GENERATION_UNAVAILABLE"),
        )

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

    /**
     * V66 persists only AES-GCM ciphertext and citation identities. MODEL_KNOWLEDGE has no citation row and is
     * distinguished by its exact flag; provider response, prompt, usage payload and canonical text remain absent.
     */
    private fun persistStrongLlmAnsweredHistory(
        identity: RagHistoryIdentity,
        requestId: String,
        command: RagAskCommand,
        scope: RagV2RetrievalScope,
        basis: StrongLlmAnswerBasis,
        citationCoverage: Double,
        warnings: List<String>,
        citedEvidence: List<RagV2RetrievedCitation>,
        encrypted: RagEncryptedHistoryPayload,
    ): List<JsonNode> {
        val receipt =
            objectMapper.writeValueAsString(
                citedEvidence.mapIndexed { index, citation ->
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
                SELECT public.persist_s4_9_strong_llm_history(
                  :ownerUserId, :answerId, :requestId, :answerMode, :sessionId, :scopeClaimId,
                  :answerBasis, :citationCoverage, CAST(:guardrailFlags AS text[]), :kekVersion,
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
                    "answerBasis" to basis.name,
                    "citationCoverage" to citationCoverage,
                    "guardrailFlags" to
                        if (basis == StrongLlmAnswerBasis.MODEL_KNOWLEDGE) {
                            arrayOf("MODEL_KNOWLEDGE_ONLY")
                        } else {
                            warnings.toTypedArray()
                        },
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

    /**
     * model이 실제 문장에 사용한 citation만 history/API로 내보낸다. retrieval top-5 전체를 사용 근거처럼
     * 확장하면 grounded answer의 citation coverage를 과장할 수 있으므로 generator가 검증한 순서를 보존한다.
     */
    private fun selectedStrongLlmCitations(
        retrieved: List<RagV2RetrievedCitation>,
        citationIds: List<String>,
        basis: StrongLlmAnswerBasis,
    ): List<RagV2RetrievedCitation> {
        val byCitationId = retrieved.associateBy { it.citationId }
        require(byCitationId.size == retrieved.size)
        if (basis == StrongLlmAnswerBasis.MODEL_KNOWLEDGE) {
            require(citationIds.isEmpty())
            return emptyList()
        }
        require(basis == StrongLlmAnswerBasis.EVIDENCE)
        require(citationIds.isNotEmpty() && citationIds.distinct().size == citationIds.size)
        return citationIds.map { citationId -> requireNotNull(byCitationId[citationId]) }
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

    /** 외부 provider 호출 사이에는 PostgreSQL transaction을 열어 두지 않는다. */
    private fun <T> inDatabaseTransaction(block: () -> T): T =
        TransactionTemplate(
            transactionManagerProvider.getIfAvailable()
                ?: throw RagGuardHistoryUnavailableException(),
        ).execute { block() }
            ?: throw RagGuardHistoryUnavailableException()

    private fun ResultSet.instant(column: String) = getObject(column, OffsetDateTime::class.java).toInstant()

    private fun id(prefix: String): String = "${prefix}_${UUID.randomUUID().toString().replace("-", "")}"

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: throw RagGuardHistoryUnavailableException()

    /**
     * DB definer read의 internal projection이다. API/loopback payload가 아니며 test도 같은 closed
     * state shape를 통해 revoked/granted query capability를 회귀 검증한다.
     */
    internal data class RagV2StoredEffectiveConsent(
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
        const val BGE_PROFILE = "bge_m3_local_1024_v1"
        const val VOYAGE_PROFILE = "voyage_context_4_1024_v1"
        val CHUNK_ID = Regex("^rag_v2_chk_[0-9a-f]{32}$")
        const val MAX_CITATIONS = 5
        const val MAX_GUARDRAIL_FLAGS = 8
    }
}
