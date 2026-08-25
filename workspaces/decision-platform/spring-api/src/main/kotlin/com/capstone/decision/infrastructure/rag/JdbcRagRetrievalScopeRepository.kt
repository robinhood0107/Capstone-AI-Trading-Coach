package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagCitation
import com.capstone.decision.application.rag.RagGuardHistoryUnavailableException
import com.capstone.decision.application.rag.RagRetrievalScope
import com.capstone.decision.application.rag.RagRetrievalScopePort
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import com.capstone.decision.infrastructure.security.ActorRlsScope
import org.springframework.beans.factory.ObjectProvider
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Transactional
import tools.jackson.databind.ObjectMapper

@Repository
@ConditionalOnProperty(name = ["app.rag.grpc.enabled"], havingValue = "true")
class JdbcRagRetrievalScopeRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val objectMapper: ObjectMapper,
    private val actorRlsScope: ActorRlsScope,
) : RagRetrievalScopePort {
    /**
     * SECURITY DEFINER projection이 owner/session/topic을 재검증하고 table SELECT 권한 없이 opaque claim만 반환한다.
     */
    @Transactional
    override fun issue(
        ownerUserId: String,
        sessionId: String,
        topics: List<String>,
    ): RagRetrievalScope =
        guarded {
            val jdbc = jdbcProvider.getIfAvailable() ?: throw RagGuardHistoryUnavailableException()
            val topicsJson = objectMapper.writeValueAsString(topics)
            actorRlsScope.open(
                jdbc,
                ownerUserId,
                ActorCapabilityBinding.request(
                    "ISSUE_RAG_RPC_SCOPE",
                    "RAG_SESSION",
                    sessionId,
                    ActorCapabilityRolePolicy.OWNER,
                    ownerUserId,
                    sessionId,
                    topicsJson,
                ),
            )
            jdbc
                .query(
                    """
                    SELECT *
                    FROM issue_rag_rpc_scope(
                      :ownerUserId,
                      :sessionId,
                      CAST(:topics AS jsonb)
                    )
                    """.trimIndent(),
                    mapOf(
                        "ownerUserId" to ownerUserId,
                        "sessionId" to sessionId,
                        "topics" to topicsJson,
                    ),
                ) { result, _ ->
                    RagRetrievalScope(
                        scopeClaimId = result.getString("scope_claim_id"),
                        policyId = result.getString("policy_id"),
                        policyVersion = result.getLong("policy_version"),
                        activeGenerationId = result.getString("active_generation_id"),
                        embeddingProfileId = result.getString("effective_profile_id"),
                    )
                }.single()
        }

    @Transactional
    override fun requireAuthorized(
        ownerUserId: String,
        sessionId: String,
        scope: RagRetrievalScope,
        citations: List<RagCitation>,
    ) {
        guarded {
            val jdbc = jdbcProvider.getIfAvailable() ?: throw RagGuardHistoryUnavailableException()
            val citationsJson =
                objectMapper.writeValueAsString(
                    citations.mapIndexed { index, citation ->
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
            actorRlsScope.open(
                jdbc,
                ownerUserId,
                ActorCapabilityBinding.request(
                    "RECHECK_RAG_RPC_CITATIONS",
                    "RAG_SCOPE",
                    scope.scopeClaimId,
                    ActorCapabilityRolePolicy.OWNER,
                    ownerUserId,
                    sessionId,
                    scope.scopeClaimId,
                    citationsJson,
                ),
            )
            jdbc.queryForObject(
                """
                SELECT recheck_rag_rpc_citations(
                  :ownerUserId,
                  :sessionId,
                  :scopeClaimId,
                  :policyId,
                  :policyVersion,
                  :activeGenerationId,
                  :embeddingProfileId,
                  CAST(:citations AS jsonb)
                )
                """.trimIndent(),
                mapOf(
                    "ownerUserId" to ownerUserId,
                    "sessionId" to sessionId,
                    "scopeClaimId" to scope.scopeClaimId,
                    "policyId" to scope.policyId,
                    "policyVersion" to scope.policyVersion,
                    "activeGenerationId" to scope.activeGenerationId,
                    "embeddingProfileId" to scope.embeddingProfileId,
                    "citations" to citationsJson,
                ),
                Any::class.java,
            )
            Unit
        }
    }

    private inline fun <T> guarded(block: () -> T): T =
        try {
            block()
        } catch (exception: RagGuardHistoryUnavailableException) {
            throw exception
        } catch (exception: RuntimeException) {
            throw RagGuardHistoryUnavailableException(exception)
        }
}
