package com.capstone.decision.infrastructure.vertex

import com.capstone.decision.application.rag.RagV2VertexEvidence
import com.capstone.decision.application.rag.StrongLlmAnswerBasis
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component
import java.nio.charset.StandardCharsets
import java.security.MessageDigest

internal data class S49StrongLlmUsage(
    val promptTokens: Int,
    val outputTokens: Int,
    val toolRounds: Int,
    val searchCalls: Int,
    val readCalls: Int,
)

internal interface S49StrongLlmUsagePort {
    fun commit(
        ownerUserId: String,
        requestId: String,
        modelId: String,
        basis: StrongLlmAnswerBasis,
        evidence: List<RagV2VertexEvidence>,
        usage: S49StrongLlmUsage,
    )

    fun unknownBilling(
        ownerUserId: String,
        requestId: String,
        modelId: String,
        evidence: List<RagV2VertexEvidence>,
        toolRounds: Int,
        searchCalls: Int,
        readCalls: Int,
    )
}

/** S4.9 usage는 content-free 집계만 SECURITY DEFINER 함수로 기록하고 prompt·evidence·응답은 저장하지 않는다. */
@Component
@ConditionalOnProperty(name = ["app.s4-9.strong-llm.enabled"], havingValue = "true")
internal class JdbcS49StrongLlmUsageLedger(
    private val jdbcTemplate: JdbcTemplate,
) : S49StrongLlmUsagePort {
    override fun commit(
        ownerUserId: String,
        requestId: String,
        modelId: String,
        basis: StrongLlmAnswerBasis,
        evidence: List<RagV2VertexEvidence>,
        usage: S49StrongLlmUsage,
    ) = record(ownerUserId, requestId, modelId, basis.name, "COMMITTED", evidence, usage)

    override fun unknownBilling(
        ownerUserId: String,
        requestId: String,
        modelId: String,
        evidence: List<RagV2VertexEvidence>,
        toolRounds: Int,
        searchCalls: Int,
        readCalls: Int,
    ) = record(
        ownerUserId,
        requestId,
        modelId,
        null,
        "UNKNOWN_BILLING",
        evidence,
        S49StrongLlmUsage(0, 0, toolRounds, searchCalls, readCalls),
    )

    private fun record(
        ownerUserId: String,
        requestId: String,
        modelId: String,
        basis: String?,
        outcome: String,
        evidence: List<RagV2VertexEvidence>,
        usage: S49StrongLlmUsage,
    ) {
        val evidenceSetSha256 =
            sha256(evidence.joinToString("\n") { "${it.citationId}:${it.canonicalTextSha256}" })
        val usageEventId = "s49_llu_${sha256("$requestId:$outcome:$evidenceSetSha256").take(32)}"
        val recorded =
            jdbcTemplate.queryForObject(
                """
                SELECT public.record_s4_9_strong_llm_usage(
                  ?, ?, ?, 'VERTEX_AI', ?, ?, ?, ?, ?, ?, ?, ?, ?
                ) IS NULL
                """.trimIndent(),
                Boolean::class.java,
                usageEventId,
                ownerUserId,
                requestId,
                modelId,
                basis,
                outcome,
                usage.toolRounds,
                usage.searchCalls,
                usage.readCalls,
                usage.promptTokens.takeIf { outcome == "COMMITTED" },
                usage.outputTokens.takeIf { outcome == "COMMITTED" },
                evidenceSetSha256,
            )
        check(recorded == true)
    }

    private fun sha256(value: String): String {
        val bytes = value.toByteArray(StandardCharsets.UTF_8)
        return try {
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        } finally {
            bytes.fill(0)
        }
    }
}
