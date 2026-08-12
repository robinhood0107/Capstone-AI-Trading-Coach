package com.capstone.decision.infrastructure.vertex

import com.capstone.decision.application.rag.RagAskCommand
import com.capstone.decision.application.rag.RagV2VertexGenerationCommand
import com.capstone.decision.application.rag.RagV2VertexQuestionFingerprintPort
import org.springframework.beans.factory.ObjectProvider
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Component
import org.springframework.transaction.PlatformTransactionManager
import org.springframework.transaction.TransactionDefinition
import org.springframework.transaction.support.TransactionTemplate
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Instant
import java.time.OffsetDateTime

internal data class PreS5VertexUsageLease(
    val usageEventId: String,
    val ownerUserId: String,
    val expiresAt: Instant,
)

internal data class PreS5VertexUsage(
    val promptTokenCount: Int,
    val candidateTokenCount: Int,
    val totalTokenCount: Int,
)

/** packet/nonce와 question HMAC을 먼저 commit해 process crash 뒤 generation 재호출을 막는다. */
internal data class PreS5VertexGenerateContentAttempt(
    val lease: PreS5VertexUsageLease,
)

/** service-account OAuth socket 직전에 append-only token attempt를 고정한다. */
internal data class PreS5VertexTokenAttempt(
    val lease: PreS5VertexUsageLease,
)

@Component
@ConditionalOnProperty(name = ["app.rag-v2.vertex.enabled"], havingValue = "true")
internal class JdbcPreS5VertexUsageLedger(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    transactionManager: PlatformTransactionManager,
    private val questionFingerprintPort: RagV2VertexQuestionFingerprintPort,
) {
    private val requiresNew =
        TransactionTemplate(transactionManager).apply {
            propagationBehavior = TransactionDefinition.PROPAGATION_REQUIRES_NEW
        }

    fun reserve(
        command: RagV2VertexGenerationCommand,
        activation: PreS5VertexActivation,
    ): PreS5VertexUsageLease {
        val questionFingerprint =
            questionFingerprintPort.fingerprint(
                ownerUserId = command.ownerUserId,
                command =
                    RagAskCommand(
                        question = command.question,
                        answerMode = command.answerMode,
                        relatedSymbols = command.relatedSymbols,
                        topics = command.topics,
                    ),
            )
        val usageEventId = usageEventId(activation, questionFingerprint)
        val evidenceManifest = evidenceManifest(command)
        require(command.requestId == activation.requestId)
        require(command.scope.scopeClaimId == activation.scopeClaimId)
        require(command.answerMode.name == activation.answerMode)
        require(command.consent.consentEventId == activation.consentEventId)
        require(command.consent.policyDigest == activation.policySha256)
        require(command.consent.processorSetDigest == activation.processorSetSha256)
        require(questionFingerprint == activation.questionFingerprintHmac)
        return inNewTransaction {
            val jdbc = jdbc()
            setActor(jdbc, command.ownerUserId)
            val reservation =
                jdbc
                    .query(
                        """
                        SELECT *
                        FROM reserve_rag_v2_immutable_vertex_usage(
                          :usageEventId, :ownerUserId, :requestId, :scopeClaimId, :questionFingerprintHmac,
                          :answerMode, :consentEventId, :packetSha256, :nonceSha256, :policySha256, :processorSetSha256, :expiresAt,
                          :inputTokenCap, :outputTokenCap, :inputByteCap, :costCapMicrousd,
                          :inputMicrousdPerToken, :outputMicrousdPerToken,
                          :tokenPhysicalCallCap, :generateContentPhysicalCallCap, :authenticationMode,
                          CAST(:evidenceManifest AS jsonb)
                        )
                        """.trimIndent(),
                        mapOf(
                            "usageEventId" to usageEventId,
                            "ownerUserId" to command.ownerUserId,
                            "requestId" to command.requestId,
                            "scopeClaimId" to command.scope.scopeClaimId,
                            "questionFingerprintHmac" to questionFingerprint,
                            "answerMode" to command.answerMode.name,
                            "consentEventId" to command.consent.consentEventId,
                            "packetSha256" to activation.packetSha256,
                            "nonceSha256" to activation.nonceSha256,
                            "policySha256" to activation.policySha256,
                            "processorSetSha256" to activation.processorSetSha256,
                            "expiresAt" to activation.expiresAt,
                            "inputTokenCap" to activation.inputTokenCap,
                            "outputTokenCap" to activation.outputTokenCap,
                            "inputByteCap" to activation.inputByteCap,
                            "costCapMicrousd" to activation.costCapMicrousd,
                            "inputMicrousdPerToken" to activation.inputMicrousdPerToken,
                            "outputMicrousdPerToken" to activation.outputMicrousdPerToken,
                            "tokenPhysicalCallCap" to activation.tokenPhysicalCallCap,
                            "generateContentPhysicalCallCap" to activation.generateContentPhysicalCallCap,
                            "authenticationMode" to activation.authenticationMode,
                            "evidenceManifest" to evidenceManifest,
                        ),
                    ) { result, _ ->
                        result.getString("usage_event_id") to
                            result.getObject("expires_at", OffsetDateTime::class.java).toInstant()
                    }.singleOrNull()
                    ?: throw RagV2VertexUsageLedgerException()
            require(reservation.first == usageEventId && reservation.second == activation.expiresAt)
            PreS5VertexUsageLease(usageEventId, command.ownerUserId, reservation.second)
        }
    }

    fun claimTokenAttempt(lease: PreS5VertexUsageLease): PreS5VertexTokenAttempt {
        claim(lease, "SELECT claim_rag_v2_immutable_vertex_token_attempt(:usageEventId, :ownerUserId)")
        return PreS5VertexTokenAttempt(lease)
    }

    /** OAuth 성공 뒤 generation socket 전에 별도 append-only one-shot receipt가 있어야 한다. */
    fun claimGenerateContentAttempt(lease: PreS5VertexUsageLease): PreS5VertexGenerateContentAttempt {
        claim(lease, "SELECT claim_rag_v2_immutable_vertex_generate_content_attempt(:usageEventId, :ownerUserId)")
        return PreS5VertexGenerateContentAttempt(lease)
    }

    fun commit(
        lease: PreS5VertexUsageLease,
        usage: PreS5VertexUsage,
    ) {
        inNewTransaction {
            val jdbc = jdbc()
            setActor(jdbc, lease.ownerUserId)
            jdbc.queryForObject(
                """
                SELECT commit_rag_v2_immutable_vertex_usage(
                  :usageEventId, :ownerUserId, :promptTokenCount, :candidateTokenCount, :totalTokenCount
                )
                """.trimIndent(),
                mapOf(
                    "usageEventId" to lease.usageEventId,
                    "ownerUserId" to lease.ownerUserId,
                    "promptTokenCount" to usage.promptTokenCount,
                    "candidateTokenCount" to usage.candidateTokenCount,
                    "totalTokenCount" to usage.totalTokenCount,
                ),
                Any::class.java,
            )
            Unit
        }
    }

    fun markUnknownBilling(lease: PreS5VertexUsageLease) {
        inNewTransaction {
            val jdbc = jdbc()
            setActor(jdbc, lease.ownerUserId)
            jdbc.queryForObject(
                "SELECT mark_rag_v2_immutable_vertex_usage_unknown_billing(:usageEventId, :ownerUserId)",
                mapOf("usageEventId" to lease.usageEventId, "ownerUserId" to lease.ownerUserId),
                Any::class.java,
            )
            Unit
        }
    }

    private fun usageEventId(
        activation: PreS5VertexActivation,
        questionFingerprint: String,
    ): String {
        val seed =
            listOf("rag-v2-vertex-usage-event/v1", activation.packetSha256, activation.nonceSha256, questionFingerprint)
                .joinToString("\u0000")
                .toByteArray(StandardCharsets.UTF_8)
        return try {
            "rgr_vgu_" +
                MessageDigest
                    .getInstance("SHA-256")
                    .digest(seed)
                    .joinToString("") { "%02x".format(it) }
                    .take(32)
        } finally {
            seed.fill(0)
        }
    }

    /**
     * DB lease에는 canonical text를 넣지 않는다. claim 시점 V42 function이 이 identity/hash manifest를
     * current scope·pointer·external eligibility와 다시 대조하므로 stale evidence는 provider socket 전에 닫힌다.
     */
    private fun evidenceManifest(command: RagV2VertexGenerationCommand): String {
        val evidence = command.evidence
        require(evidence.size in 1..5)
        require(evidence.map { it.ordinal } == (1..evidence.size).toList())
        require(evidence.map { it.citationId }.distinct().size == evidence.size)
        require(evidence.map { it.chunkRevisionId }.distinct().size == evidence.size)
        require(
            evidence.all {
                CITATION_ID.matches(it.citationId) &&
                    CHUNK_ID.matches(it.chunkRevisionId) &&
                    SHA256.matches(it.canonicalTextSha256)
            },
        )
        return evidence.joinToString(prefix = "[", postfix = "]", separator = ",") { item ->
            """{"ordinal":${item.ordinal},"citationId":"${item.citationId}","chunkRevisionId":"${item.chunkRevisionId}","canonicalTextSha256":"${item.canonicalTextSha256}"}"""
        }
    }

    private fun <T> inNewTransaction(block: () -> T): T =
        try {
            requiresNew.execute { block() } ?: throw RagV2VertexUsageLedgerException()
        } catch (error: RagV2VertexUsageLedgerException) {
            throw error
        } catch (_: Exception) {
            throw RagV2VertexUsageLedgerException()
        }

    private fun jdbc(): NamedParameterJdbcTemplate = jdbcProvider.getIfAvailable() ?: throw RagV2VertexUsageLedgerException()

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

    private fun claim(
        lease: PreS5VertexUsageLease,
        sql: String,
    ) {
        inNewTransaction {
            val jdbc = jdbc()
            setActor(jdbc, lease.ownerUserId)
            jdbc.queryForObject(
                sql,
                mapOf("usageEventId" to lease.usageEventId, "ownerUserId" to lease.ownerUserId),
                Any::class.java,
            )
            Unit
        }
    }

    private companion object {
        val CITATION_ID = Regex("^cit_[1-5]$")
        val CHUNK_ID = Regex("^rag_v2_chk_[0-9a-f]{32}$")
        val SHA256 = Regex("^[0-9a-f]{64}$")
    }
}

internal class RagV2VertexUsageLedgerException : RuntimeException()
