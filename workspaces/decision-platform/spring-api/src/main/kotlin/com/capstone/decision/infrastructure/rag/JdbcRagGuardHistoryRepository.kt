package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagAnswerCompletion
import com.capstone.decision.application.rag.RagAnswerMode
import com.capstone.decision.application.rag.RagCitation
import com.capstone.decision.application.rag.RagClaimDecision
import com.capstone.decision.application.rag.RagConsentEvent
import com.capstone.decision.application.rag.RagEffectiveConsent
import com.capstone.decision.application.rag.RagEncryptedFieldPayload
import com.capstone.decision.application.rag.RagEncryptedHistoryPayload
import com.capstone.decision.application.rag.RagGenerationStatus
import com.capstone.decision.application.rag.RagGuardHistoryPersistencePort
import com.capstone.decision.application.rag.RagGuardHistoryUnavailableException
import com.capstone.decision.application.rag.RagHistoryCursorPoint
import com.capstone.decision.application.rag.RagHistoryIdentity
import com.capstone.decision.application.rag.RagHistoryMetadata
import com.capstone.decision.application.rag.RagIdempotencyIdentity
import com.capstone.decision.application.rag.RagPurgeResult
import com.capstone.decision.application.rag.RagStoredEncryptedHistory
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Transactional
import tools.jackson.databind.ObjectMapper
import java.sql.ResultSet
import java.time.OffsetDateTime
import java.time.ZoneOffset

@Repository
class JdbcRagGuardHistoryRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val objectMapper: ObjectMapper,
) : RagGuardHistoryPersistencePort {
    @Transactional
    override fun claim(
        ownerUserId: String,
        idempotency: RagIdempotencyIdentity,
        claimTtlSeconds: Int,
    ): RagClaimDecision =
        guarded {
            val jdbc = jdbc()
            setActor(jdbc, ownerUserId)
            val result =
                jdbc
                    .query(
                        """
                        SELECT outcome, answer_id
                        FROM claim_rag_answer(
                          :ownerUserId,
                          :scopeHmac,
                          :requestFingerprint,
                          :claimTtlSeconds
                        )
                        """.trimIndent(),
                        mapOf(
                            "ownerUserId" to ownerUserId,
                            "scopeHmac" to idempotency.scopeHmac,
                            "requestFingerprint" to idempotency.requestFingerprint,
                            "claimTtlSeconds" to claimTtlSeconds,
                        ),
                    ) { resultSet, _ ->
                        resultSet.getString("outcome") to resultSet.getString("answer_id")
                    }.single()
            when (result.first) {
                "CLAIMED" -> RagClaimDecision.Claimed
                "REPLAY" -> RagClaimDecision.Replay(requireNotNull(result.second))
                "CONFLICT" -> RagClaimDecision.Conflict
                "IN_PROGRESS" -> RagClaimDecision.InProgress
                "RESULT_UNAVAILABLE" -> RagClaimDecision.ResultUnavailable
                "FAILED_BEFORE_PROVIDER" -> RagClaimDecision.FailedBeforeProvider
                "UNKNOWN_AFTER_PROVIDER" -> RagClaimDecision.UnknownAfterProvider
                else -> error("RAG claim returned an unknown bounded outcome.")
            }
        }

    @Transactional
    override fun complete(completion: RagAnswerCompletion) {
        guarded {
            val jdbc = jdbc()
            setActor(jdbc, completion.identity.ownerUserId)
            val evaluation = completion.evaluation
            val encrypted = completion.encrypted
            jdbc.queryForObject(
                """
                SELECT complete_rag_answer(
                  :ownerUserId,
                  :scopeHmac,
                  :requestFingerprint,
                  :answerId,
                  :answerMode,
                  :generationStatus,
                  :citationCoverage,
                  :retrievalFailure,
                  ARRAY(
                    SELECT jsonb_array_elements_text(CAST(:guardrailFlags AS jsonb))
                  ),
                  :kekVersion,
                  :wrapNonce,
                  :wrappedDek,
                  :wrapTag,
                  :questionNonce,
                  :questionCiphertext,
                  :questionTag,
                  :answerNonce,
                  :answerCiphertext,
                  :answerTag,
                  :createdAt,
                  :providerAttempts,
                  CAST(:citations AS jsonb)
                )
                """.trimIndent(),
                mapOf(
                    "ownerUserId" to completion.identity.ownerUserId,
                    "scopeHmac" to completion.idempotency.scopeHmac,
                    "requestFingerprint" to completion.idempotency.requestFingerprint,
                    "answerId" to completion.identity.answerId,
                    "answerMode" to completion.answerMode.name,
                    "generationStatus" to evaluation.generationStatus.name,
                    "citationCoverage" to evaluation.citationCoverage,
                    "retrievalFailure" to evaluation.retrievalFailure,
                    "guardrailFlags" to objectMapper.writeValueAsString(evaluation.guardrailFlags),
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
                    "createdAt" to OffsetDateTime.ofInstant(completion.identity.createdAt, ZoneOffset.UTC),
                    "providerAttempts" to evaluation.providerPhysicalAttempts,
                    "citations" to citationsJson(completion),
                ),
                Any::class.java,
            )
            Unit
        }
    }

    @Transactional
    override fun failBeforeProvider(
        ownerUserId: String,
        idempotency: RagIdempotencyIdentity,
    ) {
        callTerminalFunction(
            "fail_rag_answer_before_provider",
            ownerUserId,
            idempotency,
        )
    }

    @Transactional
    override fun markUnknownAfterProvider(
        ownerUserId: String,
        idempotency: RagIdempotencyIdentity,
    ) {
        callTerminalFunction(
            "mark_rag_answer_unknown_after_provider",
            ownerUserId,
            idempotency,
        )
    }

    @Transactional(readOnly = true)
    override fun findHistory(
        ownerUserId: String,
        answerId: String,
    ): RagStoredEncryptedHistory? =
        guarded {
            val jdbc = jdbc()
            setActor(jdbc, ownerUserId)
            jdbc
                .query(
                    """
                    SELECT *
                    FROM read_rag_history_detail(:ownerUserId, :answerId)
                    """.trimIndent(),
                    mapOf("ownerUserId" to ownerUserId, "answerId" to answerId),
                ) { result, _ -> result.toStoredHistory() }
                .singleOrNull()
        }

    @Transactional(readOnly = true)
    override fun findCitations(
        ownerUserId: String,
        answerId: String,
    ): List<RagCitation> =
        guarded {
            val jdbc = jdbc()
            setActor(jdbc, ownerUserId)
            jdbc.query(
                """
                SELECT *
                FROM read_rag_history_citations(:ownerUserId, :answerId)
                """.trimIndent(),
                mapOf("ownerUserId" to ownerUserId, "answerId" to answerId),
            ) { result, _ ->
                RagCitation(
                    citationId = result.getString("citation_id"),
                    sourceId = result.getString("source_id"),
                    sourceRevisionId = result.getString("source_revision_id"),
                    chunkRevisionId = result.getString("chunk_revision_id"),
                    generationId = result.getString("generation_id"),
                    title = result.getString("title"),
                    sectionTitle = result.getString("section_title"),
                    canonicalUrl = result.getString("canonical_url"),
                )
            }
        }

    @Transactional(readOnly = true)
    override fun listHistory(
        ownerUserId: String,
        cursor: RagHistoryCursorPoint?,
        limit: Int,
    ): List<RagHistoryMetadata> =
        guarded {
            val jdbc = jdbc()
            setActor(jdbc, ownerUserId)
            jdbc.query(
                """
                SELECT *
                FROM read_rag_history_metadata(
                  :ownerUserId,
                  :beforeCreatedAt,
                  :beforeAnswerId,
                  :limit
                )
                """.trimIndent(),
                mapOf(
                    "ownerUserId" to ownerUserId,
                    "beforeCreatedAt" to
                        cursor?.createdAt?.let {
                            OffsetDateTime.ofInstant(it, ZoneOffset.UTC)
                        },
                    "beforeAnswerId" to cursor?.answerId,
                    "limit" to limit,
                ),
            ) { result, _ ->
                RagHistoryMetadata(
                    answerId = result.getString("answer_id"),
                    createdAt = result.instant("created_at"),
                    expiresAt = result.instant("expires_at"),
                    answerMode = RagAnswerMode.valueOf(result.getString("answer_mode")),
                    generationStatus =
                        RagGenerationStatus.valueOf(
                            result.getString("generation_status"),
                        ),
                    helpful = result.booleanOrNull("helpful"),
                )
            }
        }

    @Transactional
    override fun deleteHistory(
        ownerUserId: String,
        answerId: String,
    ) {
        guarded {
            val jdbc = jdbc()
            setActor(jdbc, ownerUserId)
            jdbc.queryForObject(
                "SELECT delete_owned_rag_history(:ownerUserId, :answerId)",
                mapOf("ownerUserId" to ownerUserId, "answerId" to answerId),
                Any::class.java,
            )
            Unit
        }
    }

    @Transactional
    override fun upsertFeedback(
        ownerUserId: String,
        answerId: String,
        helpful: Boolean,
    ): Boolean =
        guarded {
            val jdbc = jdbc()
            setActor(jdbc, ownerUserId)
            requireNotNull(
                jdbc.queryForObject(
                    """
                    SELECT upsert_owned_rag_answer_feedback(
                      :ownerUserId,
                      :answerId,
                      :helpful
                    )
                    """.trimIndent(),
                    mapOf(
                        "ownerUserId" to ownerUserId,
                        "answerId" to answerId,
                        "helpful" to helpful,
                    ),
                    Boolean::class.java,
                ),
            )
        }

    @Transactional
    override fun recordConsent(
        ownerUserId: String,
        consentEventId: String,
        action: String,
        policyVersion: String,
    ): RagConsentEvent =
        guarded {
            val jdbc = jdbc()
            setActor(jdbc, ownerUserId)
            jdbc
                .query(
                    """
                    SELECT *
                    FROM record_rag_consent_event(
                      :ownerUserId,
                      :consentEventId,
                      :action,
                      :policyVersion
                    )
                    """.trimIndent(),
                    mapOf(
                        "ownerUserId" to ownerUserId,
                        "consentEventId" to consentEventId,
                        "action" to action,
                        "policyVersion" to policyVersion,
                    ),
                ) { result, _ ->
                    RagConsentEvent(
                        consentEventId = result.getString("consent_event_id"),
                        consentType = result.getString("consent_type"),
                        action = result.getString("action"),
                        policyVersion = result.getString("policy_version"),
                        createdAt = result.instant("created_at"),
                    )
                }.single()
        }

    @Transactional(readOnly = true)
    override fun effectiveConsent(ownerUserId: String): RagEffectiveConsent =
        guarded {
            val jdbc = jdbc()
            setActor(jdbc, ownerUserId)
            jdbc
                .query(
                    "SELECT * FROM read_effective_rag_consent(:ownerUserId)",
                    mapOf("ownerUserId" to ownerUserId),
                ) { result, _ ->
                    RagEffectiveConsent(
                        granted = result.getBoolean("granted"),
                        policyVersion = result.getString("policy_version"),
                        recordedAt =
                            result
                                .getObject("recorded_at", OffsetDateTime::class.java)
                                ?.toInstant(),
                    )
                }.single()
        }

    @Transactional
    override fun purgeExpired(limit: Int): RagPurgeResult =
        guarded {
            jdbc()
                .query(
                    "SELECT * FROM purge_expired_rag_history(:limit)",
                    mapOf("limit" to limit),
                ) { result, _ ->
                    RagPurgeResult(
                        deletedCount = result.getInt("deleted_count"),
                        oldestExpiredLagSeconds =
                            result.getLong("oldest_expired_lag_seconds"),
                    )
                }.single()
        }

    private fun callTerminalFunction(
        function: String,
        ownerUserId: String,
        idempotency: RagIdempotencyIdentity,
    ) {
        guarded {
            require(function in TERMINAL_FUNCTIONS)
            val jdbc = jdbc()
            setActor(jdbc, ownerUserId)
            jdbc.queryForObject(
                "SELECT $function(:ownerUserId, :scopeHmac, :requestFingerprint)",
                mapOf(
                    "ownerUserId" to ownerUserId,
                    "scopeHmac" to idempotency.scopeHmac,
                    "requestFingerprint" to idempotency.requestFingerprint,
                ),
                Any::class.java,
            )
            Unit
        }
    }

    private fun citationsJson(completion: RagAnswerCompletion): String =
        objectMapper.writeValueAsString(
            completion.evaluation.citations.mapIndexed { index, citation ->
                mapOf(
                    "ordinal" to index + 1,
                    "citationId" to citation.citationId,
                    "sourceId" to citation.sourceId,
                    "sourceRevisionId" to citation.sourceRevisionId,
                    "chunkRevisionId" to citation.chunkRevisionId,
                    "generationId" to citation.generationId,
                    "title" to citation.title,
                    "sectionTitle" to citation.sectionTitle,
                    "canonicalUrl" to citation.canonicalUrl,
                )
            },
        )

    private fun setActor(
        jdbc: NamedParameterJdbcTemplate,
        ownerUserId: String,
    ) {
        jdbc.queryForObject(
            "SELECT set_config('app.actor_user_id', :ownerUserId, true)",
            mapOf("ownerUserId" to ownerUserId),
            String::class.java,
        )
    }

    private fun ResultSet.toStoredHistory(): RagStoredEncryptedHistory =
        RagStoredEncryptedHistory(
            identity =
                RagHistoryIdentity(
                    answerId = getString("answer_id"),
                    ownerUserId = getString("owner_user_id"),
                    createdAt = instant("created_at"),
                ),
            answerMode = RagAnswerMode.valueOf(getString("answer_mode")),
            generationStatus = RagGenerationStatus.valueOf(getString("generation_status")),
            citationCoverage = getDouble("citation_coverage"),
            retrievalFailure = getBoolean("retrieval_failure"),
            guardrailFlags =
                getArray("guardrail_flags").array.let { value ->
                    @Suppress("UNCHECKED_CAST")
                    (value as Array<String>).toList()
                },
            citationCount = getInt("citation_count"),
            encrypted =
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
                ),
            expiresAt = instant("expires_at"),
            helpful = booleanOrNull("helpful"),
        )

    private fun ResultSet.instant(column: String) = getObject(column, OffsetDateTime::class.java).toInstant()

    private fun ResultSet.booleanOrNull(column: String): Boolean? = getObject(column)?.let { getBoolean(column) }

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: throw RagGuardHistoryUnavailableException()

    private inline fun <T> guarded(block: () -> T): T =
        try {
            block()
        } catch (exception: RagGuardHistoryUnavailableException) {
            throw exception
        } catch (exception: RuntimeException) {
            throw RagGuardHistoryUnavailableException(exception)
        }

    private companion object {
        val TERMINAL_FUNCTIONS =
            setOf(
                "fail_rag_answer_before_provider",
                "mark_rag_answer_unknown_after_provider",
            )
    }
}
