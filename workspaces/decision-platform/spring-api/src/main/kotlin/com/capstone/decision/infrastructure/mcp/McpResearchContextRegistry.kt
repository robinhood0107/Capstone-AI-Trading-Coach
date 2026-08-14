package com.capstone.decision.infrastructure.mcp

import com.capstone.decision.application.rag.RagV2RetrievalScope
import com.capstone.decision.application.rag.RagV2RetrievedCitation
import com.capstone.decision.application.rag.RagV2VertexEvidence
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Clock
import java.time.Instant
import java.util.HexFormat
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

data class McpResearchContext(
    val id: String,
    val ownerUserId: String,
    val oauthClientId: String,
    val question: String,
    val answerMode: String,
    val requestId: String,
    val retrievalScope: RagV2RetrievalScope,
    val retrievalCitations: List<RagV2RetrievedCitation>,
    val retrievalEvidence: List<RagV2VertexEvidence>,
    val evidence: MutableList<RagV2VertexEvidence>,
    val searchableUrls: MutableSet<String>,
    val expiresAt: Instant,
    var searchCount: Int = 0,
    var readCount: Int = 0,
)

/** context receipt는 owner·OAuth client·context·source hash·expiry를 HMAC으로 묶고 raw provider body는 저장하지 않는다. */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.web.enabled"], havingValue = "true")
class McpResearchContextRegistry(
    private val properties: RagWebToolProperties,
    private val clock: Clock = Clock.systemUTC(),
) {
    private val contexts = ConcurrentHashMap<String, McpResearchContext>()

    init {
        properties.validate()
    }

    fun create(
        ownerUserId: String,
        oauthClientId: String,
        question: String,
        answerMode: String,
        requestId: String,
        retrievalScope: RagV2RetrievalScope,
        retrievalCitations: List<RagV2RetrievedCitation>,
        evidence: List<RagV2VertexEvidence>,
    ): Pair<McpResearchContext, String> {
        val id = "s49_ctx_${UUID.randomUUID().toString().replace("-", "")}"
        val context =
            McpResearchContext(
                id,
                ownerUserId,
                oauthClientId,
                question,
                answerMode,
                requestId,
                retrievalScope,
                retrievalCitations,
                evidence,
                evidence.toMutableList(),
                mutableSetOf(),
                clock.instant().plusSeconds(900),
            )
        contexts[id] = context
        return context to receipt(context)
    }

    fun require(
        receipt: String,
        ownerUserId: String,
        oauthClientId: String,
    ): McpResearchContext {
        val id = receipt.substringBefore('.')
        val context = contexts[id] ?: throw IllegalArgumentException("Unknown research context")
        require(context.ownerUserId == ownerUserId && context.oauthClientId == oauthClientId)
        require(context.expiresAt.isAfter(clock.instant()))
        require(
            MessageDigest.isEqual(receipt(context).toByteArray(StandardCharsets.US_ASCII), receipt.toByteArray(StandardCharsets.US_ASCII)),
        )
        return context
    }

    fun refreshedReceipt(context: McpResearchContext): String = receipt(context)

    fun requireById(
        id: String,
        ownerUserId: String,
        oauthClientId: String,
    ): McpResearchContext {
        val context = contexts[id] ?: throw IllegalArgumentException("Unknown research context")
        require(context.ownerUserId == ownerUserId && context.oauthClientId == oauthClientId)
        require(context.expiresAt.isAfter(clock.instant()))
        return context
    }

    private fun receipt(context: McpResearchContext): String {
        val sourceHash = sha256(context.evidence.joinToString("|") { it.canonicalTextSha256 })
        val payload =
            "${context.id}|${context.ownerUserId}|${context.oauthClientId}|${sha256(context.question)}|" +
                "${context.answerMode}|$sourceHash|${context.expiresAt.epochSecond}"
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(properties.receiptHmacKey.toByteArray(StandardCharsets.UTF_8), "HmacSHA256"))
        return "${context.id}.${HexFormat.of().formatHex(mac.doFinal(payload.toByteArray(StandardCharsets.UTF_8)))}"
    }

    private fun sha256(value: String): String =
        HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.toByteArray(StandardCharsets.UTF_8)))
}
