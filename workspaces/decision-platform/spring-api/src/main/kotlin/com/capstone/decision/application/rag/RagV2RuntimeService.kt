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
    @Transactional(readOnly = true)
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
        return RagV2Answer(
            requestId = requestId,
            answerId = null,
            generationStatus = RagGenerationStatus.GENERATION_UNAVAILABLE,
            answer = null,
            citationCoverage = 0.0,
            citations = emptyList(),
            retrievalFailure = false,
            guardrailFlags = listOf("GENERATION_UNAVAILABLE"),
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
            answer = decrypted.answer,
            generationStatus = RagGenerationStatus.valueOf(getString("generation_status")),
            citations = citations(getString("citations")),
            createdAt = identity.createdAt,
            expiresAt = instant("expires_at"),
        )
    }

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
}
