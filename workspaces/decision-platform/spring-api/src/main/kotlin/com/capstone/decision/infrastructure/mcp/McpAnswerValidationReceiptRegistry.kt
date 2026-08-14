package com.capstone.decision.infrastructure.mcp

import com.capstone.decision.application.rag.RagHistoryCryptoPort
import com.capstone.decision.application.rag.RagHistoryIdentity
import com.capstone.decision.application.rag.RagV2VertexEvidence
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Component
import org.springframework.transaction.PlatformTransactionManager
import org.springframework.transaction.support.TransactionTemplate
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Clock
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.HexFormat
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

data class McpValidationReceipt(
    val value: String,
    val expiresAt: Instant,
)

/**
 * exact draft의 validation receipt를 DB hash와 owner/client/context에 묶는다. 명시 저장 때만 receipt를 원자 소비하고
 * question/draft를 기존 AES-GCM history crypto로 암호화하며 raw text는 DB 함수 입력 밖에 남기지 않는다.
 */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.web.enabled"], havingValue = "true")
class McpAnswerValidationReceiptRegistry(
    private val properties: RagWebToolProperties,
    private val jdbc: NamedParameterJdbcTemplate,
    private val transactionManager: PlatformTransactionManager,
    private val crypto: RagHistoryCryptoPort,
    private val clock: Clock = Clock.systemUTC(),
) {
    private val entries = ConcurrentHashMap<String, Entry>()

    fun issue(
        caller: McpCaller,
        context: McpResearchContext,
        evidence: List<RagV2VertexEvidence>,
        draft: String,
        status: String,
    ): McpValidationReceipt {
        val id = "s49_val_${UUID.randomUUID().toString().replace("-", "")}"
        val expiresAt = clock.instant().plusSeconds(300)
        val draftHash = sha256(draft)
        val sourceSetHash = sha256(evidence.joinToString("|") { it.canonicalTextSha256 })
        val payload =
            "${caller.ownerUserId}|${caller.oauthClientId}|${context.id}|$sourceSetHash|" +
                "$draftHash|$status|${expiresAt.epochSecond}"
        val receipt = "$id.${hmac(payload)}"
        val entry = Entry(caller, context.id, context.question, sourceSetHash, draftHash, status, expiresAt)
        synchronized(entries) {
            pruneExpired()
            require(entries.size < properties.externalResearchMaxTotalContexts)
            require(
                entries.values.count { it.caller == caller } < properties.externalResearchMaxContextsPerCaller,
            )
            entries[id] = entry
        }
        try {
            TransactionTemplate(transactionManager).executeWithoutResult {
                setActor(caller.ownerUserId)
                require(
                    jdbc.queryForObject(
                        """
                        SELECT public.issue_s4_9_answer_validation_receipt(
                          :receiptHash, :ownerUserId, :clientId, :contextId, :sourceSetHash,
                          :draftHash, :status, :expiresAt
                        ) IS NULL
                        """.trimIndent(),
                        mapOf(
                            "receiptHash" to sha256(receipt),
                            "ownerUserId" to caller.ownerUserId,
                            "clientId" to caller.oauthClientId,
                            "contextId" to context.id,
                            "sourceSetHash" to sourceSetHash,
                            "draftHash" to draftHash,
                            "status" to status,
                            "expiresAt" to OffsetDateTime.ofInstant(expiresAt, ZoneOffset.UTC),
                        ),
                        Boolean::class.java,
                    ) == true,
                )
            }
        } catch (error: Exception) {
            entries.remove(id, entry)
            throw error
        }
        return McpValidationReceipt(receipt, expiresAt)
    }

    fun consume(
        caller: McpCaller,
        receipt: String,
        draft: String,
    ): String {
        val id = receipt.substringBefore('.')
        val entry = entries[id] ?: throw IllegalArgumentException("Unknown validation receipt")
        requireCurrent(id, entry)
        require(entry.caller == caller && entry.draftSha256 == sha256(draft))
        val payload =
            "${caller.ownerUserId}|${caller.oauthClientId}|${entry.contextId}|${entry.sourceSetSha256}|" +
                "${entry.draftSha256}|${entry.status}|${entry.expiresAt.epochSecond}"
        require(MessageDigest.isEqual("$id.${hmac(payload)}".toByteArray(), receipt.toByteArray()))
        val answerId = "rag_mcp_${UUID.randomUUID().toString().replace("-", "")}"
        val createdAt = clock.instant()
        val identity = RagHistoryIdentity(answerId, caller.ownerUserId, createdAt)
        val encrypted = crypto.encrypt(identity, entry.question, draft)
        try {
            TransactionTemplate(transactionManager).executeWithoutResult {
                setActor(caller.ownerUserId)
                require(
                    jdbc.queryForObject(
                        """
                        SELECT public.consume_s4_9_validation_and_save_history(
                          :receiptHash, :ownerUserId, :clientId, :answerId, :draftHash,
                          :kekVersion, :wrapNonce, :wrappedDek, :wrapTag,
                          :questionNonce, :questionCiphertext, :questionTag,
                          :answerNonce, :answerCiphertext, :answerTag, :createdAt
                        ) IS NULL
                        """.trimIndent(),
                        mapOf(
                            "receiptHash" to sha256(receipt),
                            "ownerUserId" to caller.ownerUserId,
                            "clientId" to caller.oauthClientId,
                            "answerId" to answerId,
                            "draftHash" to entry.draftSha256,
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
                            "createdAt" to OffsetDateTime.ofInstant(createdAt, ZoneOffset.UTC),
                        ),
                        Boolean::class.java,
                    ) == true,
                )
            }
            entries.remove(id)
            return answerId
        } finally {
            encrypted.wrapNonce.fill(0)
            encrypted.wrappedDek.fill(0)
            encrypted.wrapTag.fill(0)
            encrypted.question.nonce.fill(0)
            encrypted.question.ciphertext.fill(0)
            encrypted.question.tag.fill(0)
            encrypted.answer.nonce.fill(0)
            encrypted.answer.ciphertext.fill(0)
            encrypted.answer.tag.fill(0)
        }
    }

    fun contextId(
        caller: McpCaller,
        receipt: String,
        draft: String,
    ): String {
        val id = receipt.substringBefore('.')
        val entry = entries[id] ?: throw IllegalArgumentException("Unknown validation receipt")
        requireCurrent(id, entry)
        require(entry.caller == caller && entry.draftSha256 == sha256(draft))
        return entry.contextId
    }

    private fun requireCurrent(
        id: String,
        entry: Entry,
    ) {
        if (!entry.expiresAt.isAfter(clock.instant())) {
            entries.remove(id, entry)
            throw IllegalArgumentException("Validation receipt expired")
        }
    }

    private fun pruneExpired() {
        val now = clock.instant()
        entries.entries.removeIf { !it.value.expiresAt.isAfter(now) }
    }

    private fun setActor(ownerUserId: String) {
        jdbc.queryForObject(
            "SELECT set_config('app.actor_user_id', :ownerUserId, true)",
            mapOf("ownerUserId" to ownerUserId),
            String::class.java,
        )
    }

    private fun hmac(value: String): String {
        val key = properties.receiptHmacKey.toByteArray(StandardCharsets.UTF_8)
        val bytes = value.toByteArray(StandardCharsets.UTF_8)
        return try {
            val mac = Mac.getInstance("HmacSHA256")
            mac.init(SecretKeySpec(key, "HmacSHA256"))
            HexFormat.of().formatHex(mac.doFinal(bytes))
        } finally {
            key.fill(0)
            bytes.fill(0)
        }
    }

    private fun sha256(value: String): String {
        val bytes = value.toByteArray(StandardCharsets.UTF_8)
        return try {
            HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes))
        } finally {
            bytes.fill(0)
        }
    }

    private data class Entry(
        val caller: McpCaller,
        val contextId: String,
        val question: String,
        val sourceSetSha256: String,
        val draftSha256: String,
        val status: String,
        val expiresAt: Instant,
    )
}
