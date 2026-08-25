package com.capstone.decision.infrastructure.grpc

import com.capstone.decision.contract.internal.s49.GroundingRoot
import com.capstone.decision.contract.internal.s49.GroundingSupport
import com.capstone.decision.infrastructure.mcp.RegisteredResearchSource
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import com.capstone.decision.infrastructure.security.ActorRlsScope
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component
import org.springframework.transaction.annotation.Transactional
import tools.jackson.databind.ObjectMapper
import java.nio.charset.StandardCharsets
import java.security.MessageDigest

internal interface S49GroundingProvenancePort {
    fun record(
        ownerUserId: String,
        requestId: String,
        roots: List<GroundingRoot>,
        supports: List<GroundingSupport>,
    )

    fun recordRead(
        ownerUserId: String,
        requestId: String,
        source: RegisteredResearchSource,
        citationId: String,
        contentSha256: String,
    )

    fun recordSearch(
        ownerUserId: String,
        requestId: String,
        ordinal: Int,
        backend: String,
        resultCount: Int,
        outcome: String,
    )
}

/** Google 본문 대신 검증된 source/support hash와 edge만 owner-scoped DB 함수에 기록한다. */
@Component
@ConditionalOnProperty(name = ["app.s4-9.strong-llm.enabled"], havingValue = "true")
internal class JdbcS49GroundingProvenanceRepository(
    private val jdbcTemplate: JdbcTemplate,
    private val objectMapper: ObjectMapper,
    private val actorRlsScope: ActorRlsScope,
) : S49GroundingProvenancePort {
    @Transactional
    override fun record(
        ownerUserId: String,
        requestId: String,
        roots: List<GroundingRoot>,
        supports: List<GroundingSupport>,
    ) {
        require(roots.isNotEmpty() && supports.isNotEmpty())
        val sources =
            roots.map { root ->
                linkedMapOf(
                    "sourceNodeId" to "s49_src_${sha256("$requestId:${root.resultId}").take(32)}",
                    "resultId" to root.resultId,
                    "citationId" to root.citationId,
                    "title" to root.title,
                    "canonicalUrl" to root.uri,
                    "domain" to root.domain,
                    "chunkIndex" to root.chunkIndex,
                )
            }
        val supportReceipts =
            supports.mapIndexed { index, support ->
                val segmentHash = sha256(support.text)
                linkedMapOf(
                    "supportId" to "s49_sup_${sha256("$requestId:$index:$segmentHash").take(32)}",
                    "segmentSha256" to segmentHash,
                    "startIndex" to support.startIndex,
                    "endIndex" to support.endIndex,
                    "chunkIndices" to support.chunkIndicesList,
                )
            }
        val sourcesJson = objectMapper.writeValueAsString(sources)
        val supportsJson = objectMapper.writeValueAsString(supportReceipts)
        actorRlsScope.open(
            jdbcTemplate,
            ownerUserId,
            ActorCapabilityBinding.request(
                "RECORD_GROUNDING_PROVENANCE",
                "RAG_REQUEST",
                requestId,
                ActorCapabilityRolePolicy.OWNER,
                ownerUserId,
                requestId,
                sourcesJson,
                supportsJson,
            ),
        )
        jdbcTemplate.queryForObject(
            "SELECT public.record_s4_9_grounding_provenance(?,?,?,?) IS NULL",
            Boolean::class.java,
            ownerUserId,
            requestId,
            sourcesJson,
            supportsJson,
        )
    }

    @Transactional
    override fun recordRead(
        ownerUserId: String,
        requestId: String,
        source: RegisteredResearchSource,
        citationId: String,
        contentSha256: String,
    ) {
        val sourceNodeId = "s49_src_${sha256("$requestId:${source.resultId}").take(32)}"
        actorRlsScope.open(
            jdbcTemplate,
            ownerUserId,
            ActorCapabilityBinding.request(
                "RECORD_READ_PROVENANCE",
                "RAG_REQUEST",
                requestId,
                ActorCapabilityRolePolicy.OWNER,
                ownerUserId,
                requestId,
                sourceNodeId,
                source.resultId,
                citationId,
                contentSha256,
            ),
        )
        jdbcTemplate.queryForObject(
            """
            SELECT public.record_s4_9_read_provenance(
              ?,?,?,?,?,?,?,?,?,?
            ) IS NULL
            """.trimIndent(),
            Boolean::class.java,
            ownerUserId,
            requestId,
            sourceNodeId,
            source.resultId,
            citationId,
            source.sourceType.name,
            source.title,
            source.url,
            java.net.URI
                .create(source.url)
                .host,
            contentSha256,
        )
    }

    @Transactional
    override fun recordSearch(
        ownerUserId: String,
        requestId: String,
        ordinal: Int,
        backend: String,
        resultCount: Int,
        outcome: String,
    ) {
        require(
            ordinal in 1..3 &&
                backend in setOf("VERTEX_GOOGLE", "SEARXNG") &&
                resultCount in 0..128 &&
                outcome in setOf("COMMITTED", "NO_RESULTS", "SEARCH_UNAVAILABLE", "UNKNOWN_BILLING"),
        )
        actorRlsScope.open(
            jdbcTemplate,
            ownerUserId,
            ActorCapabilityBinding.request(
                "RECORD_SEARCH_ATTEMPT",
                "RAG_REQUEST",
                requestId,
                ActorCapabilityRolePolicy.OWNER,
                ownerUserId,
                requestId,
                ordinal.toString(),
                backend,
                resultCount.toString(),
                outcome,
            ),
        )
        jdbcTemplate.queryForObject(
            "SELECT public.record_s4_9_search_attempt(?,?,?,?,?,?,?) IS NULL",
            Boolean::class.java,
            "s49_sra_${sha256("$requestId:$backend:$ordinal").take(32)}",
            ownerUserId,
            requestId,
            ordinal,
            backend,
            outcome,
            resultCount,
        )
    }

    private fun sha256(value: String): String {
        val bytes = value.toByteArray(StandardCharsets.UTF_8)
        return try {
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(java.util.Locale.ROOT, it) }
        } finally {
            bytes.fill(0)
        }
    }
}
