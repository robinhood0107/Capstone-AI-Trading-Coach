package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagV2RetrievalScope
import com.capstone.decision.application.rag.RagV2RetrievedCitation
import com.capstone.decision.application.rag.RagV2VertexEvidence
import com.capstone.decision.application.rag.RagV2VertexEvidencePort
import com.capstone.decision.application.rag.RagV2VertexEvidenceUnavailableException
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import com.capstone.decision.infrastructure.security.ActorRlsScope
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Component
import org.springframework.transaction.annotation.Transactional
import tools.jackson.databind.ObjectMapper
import java.nio.charset.StandardCharsets
import java.security.MessageDigest

/**
 * Vertex 직전 evidence reader는 BGE가 준 display metadata를 신뢰하지 않는다. V39 SECURITY DEFINER function이
 * immutable scope와 external-processing eligibility를 다시 확인한 canonical text만 transiently 반환한다.
 */
@Component
class JdbcRagV2VertexEvidenceRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val objectMapper: ObjectMapper,
    private val actorRlsScope: ActorRlsScope,
) : RagV2VertexEvidencePort {
    @Transactional
    override fun resolve(
        ownerUserId: String,
        requestId: String,
        scope: RagV2RetrievalScope,
        citations: List<RagV2RetrievedCitation>,
    ): List<RagV2VertexEvidence> {
        try {
            require(citations.size in 1..5)
            val receipt =
                objectMapper.writeValueAsString(
                    citations.mapIndexed { index, citation ->
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
            val jdbc = jdbc()
            actorRlsScope.open(
                jdbc,
                ownerUserId,
                ActorCapabilityBinding.request(
                    "READ_VERTEX_EVIDENCE",
                    "RAG_SCOPE",
                    scope.scopeClaimId,
                    ActorCapabilityRolePolicy.OWNER,
                    ownerUserId,
                    requestId,
                    scope.scopeClaimId,
                    receipt,
                ),
            )
            val evidence =
                jdbc.query(
                    """
                    SELECT *
                    FROM read_rag_v2_vertex_generation_evidence(
                      :ownerUserId,
                      :requestId,
                      :scopeClaimId,
                      :citations
                    )
                    """.trimIndent(),
                    mapOf(
                        "ownerUserId" to ownerUserId,
                        "requestId" to requestId,
                        "scopeClaimId" to scope.scopeClaimId,
                        "citations" to receipt,
                    ),
                ) { result, _ ->
                    RagV2VertexEvidence(
                        ordinal = result.getInt("ordinal"),
                        citationId = result.getString("citation_id"),
                        chunkRevisionId = result.getString("chunk_revision_id"),
                        canonicalText = result.getString("canonical_content"),
                        canonicalTextSha256 = result.getString("canonical_content_sha256"),
                    )
                }
            require(evidence.size == citations.size)
            require(evidence.map { it.ordinal } == (1..evidence.size).toList())
            require(evidence.map { it.citationId } == citations.map { it.citationId })
            require(evidence.map { it.chunkRevisionId } == citations.map { it.chunkRevisionId })
            require(
                evidence.all {
                    it.canonicalText.toByteArray(StandardCharsets.UTF_8).size in 1..16_384 &&
                        sha256(it.canonicalText) == it.canonicalTextSha256
                },
            )
            require(evidence.sumOf { it.canonicalText.toByteArray(StandardCharsets.UTF_8).size } <= 60_000)
            val citationById = citations.associateBy { it.citationId }
            return evidence.map { item ->
                item.copy(ownerPrivate = citationById.getValue(item.citationId).citationKind == "LOCAL_DOCUMENT")
            }
        } catch (_: Exception) {
            // canonical text나 owner document metadata를 exception cause로 보존하지 않는다.
            throw RagV2VertexEvidenceUnavailableException()
        }
    }

    private fun jdbc(): NamedParameterJdbcTemplate = jdbcProvider.getIfAvailable() ?: throw RagV2VertexEvidenceUnavailableException()

    private fun sha256(value: String): String {
        val bytes = value.toByteArray(StandardCharsets.UTF_8)
        return try {
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(java.util.Locale.ROOT, it) }
        } finally {
            bytes.fill(0)
        }
    }
}
