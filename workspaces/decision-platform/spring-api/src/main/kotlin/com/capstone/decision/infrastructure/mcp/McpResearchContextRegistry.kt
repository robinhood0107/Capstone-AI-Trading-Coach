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
        synchronized(contexts) {
            pruneExpired()
            require(contexts.size < properties.externalResearchMaxTotalContexts)
            require(
                contexts.values.count {
                    it.ownerUserId == ownerUserId && it.oauthClientId == oauthClientId
                } < properties.externalResearchMaxContextsPerCaller,
            )
            contexts[id] = context
        }
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
        requireCurrent(context)
        require(
            MessageDigest.isEqual(receipt(context).toByteArray(StandardCharsets.US_ASCII), receipt.toByteArray(StandardCharsets.US_ASCII)),
        )
        return context
    }

    fun refreshedReceipt(context: McpResearchContext): String = receipt(context)

    fun evidenceSnapshot(context: McpResearchContext): List<RagV2VertexEvidence> = synchronized(context) { context.evidence.toList() }

    /** 같은 context의 병렬 tool call도 mode별 search budget을 정확히 한 번씩만 예약한다. */
    fun reserveSearch(
        context: McpResearchContext,
        requestedMode: String,
        maximum: Int,
    ) {
        synchronized(context) {
            require(context.answerMode == requestedMode)
            require(context.searchCount < maximum)
            context.searchCount += 1
        }
    }

    /** URL membership과 read budget을 한 lock에서 확인해 동시 호출의 cap 우회를 막는다. */
    fun reserveRead(
        context: McpResearchContext,
        requestedMode: String,
        maximum: Int,
        url: String,
    ) {
        synchronized(context) {
            require(context.answerMode == requestedMode)
            require(context.readCount < maximum && url in context.searchableUrls)
            context.readCount += 1
        }
    }

    fun addSearchableUrls(
        context: McpResearchContext,
        urls: Collection<String>,
    ) {
        synchronized(context) { context.searchableUrls.addAll(urls) }
    }

    /** public citation cap 5를 유지하면서 병렬 read 완료 순서대로 하나의 canonical evidence set을 만든다. */
    fun appendWebEvidence(
        context: McpResearchContext,
        canonicalText: String,
        canonicalTextSha256: String,
    ): RagV2VertexEvidence =
        synchronized(context) {
            if (context.evidence.size == 5) context.evidence.removeAt(4)
            val ordinal = context.evidence.size + 1
            val evidence =
                RagV2VertexEvidence(
                    ordinal = ordinal,
                    citationId = "cit_$ordinal",
                    chunkRevisionId = "rag_v2_chk_${canonicalTextSha256.take(32)}",
                    canonicalText = canonicalText,
                    canonicalTextSha256 = canonicalTextSha256,
                )
            context.evidence.add(evidence)
            evidence
        }

    fun requireById(
        id: String,
        ownerUserId: String,
        oauthClientId: String,
    ): McpResearchContext {
        val context = contexts[id] ?: throw IllegalArgumentException("Unknown research context")
        require(context.ownerUserId == ownerUserId && context.oauthClientId == oauthClientId)
        requireCurrent(context)
        return context
    }

    private fun requireCurrent(context: McpResearchContext) {
        if (!context.expiresAt.isAfter(clock.instant())) {
            contexts.remove(context.id, context)
            throw IllegalArgumentException("Research context expired")
        }
    }

    /** 다음 context 생성 시 만료된 원문/evidence 참조를 제거해 메모리 보존 시간을 TTL로 제한한다. */
    private fun pruneExpired() {
        val now = clock.instant()
        contexts.entries.removeIf { !it.value.expiresAt.isAfter(now) }
    }

    private fun receipt(context: McpResearchContext): String =
        synchronized(context) {
            val sourceHash = sha256(context.evidence.joinToString("|") { it.canonicalTextSha256 })
            val payload =
                "${context.id}|${context.ownerUserId}|${context.oauthClientId}|${sha256(context.question)}|" +
                    "${context.answerMode}|$sourceHash|${context.expiresAt.epochSecond}"
            val mac = Mac.getInstance("HmacSHA256")
            mac.init(SecretKeySpec(properties.receiptHmacKey.toByteArray(StandardCharsets.UTF_8), "HmacSHA256"))
            "${context.id}.${HexFormat.of().formatHex(mac.doFinal(payload.toByteArray(StandardCharsets.UTF_8)))}"
        }

    private fun sha256(value: String): String =
        HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.toByteArray(StandardCharsets.UTF_8)))
}
