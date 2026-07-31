package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagCitation
import com.capstone.decision.application.rag.RagGuardHistoryUnavailableException
import com.capstone.decision.application.rag.RagRetrievalScope
import com.capstone.decision.application.rag.RagRetrievalScopePort
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
            jdbc.queryForObject(
                "SELECT set_config('app.actor_user_id', :ownerUserId, true)",
                mapOf("ownerUserId" to ownerUserId),
                String::class.java,
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
                        "topics" to objectMapper.writeValueAsString(topics),
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
            jdbc.queryForObject(
                "SELECT set_config('app.actor_user_id', :ownerUserId, true)",
                mapOf("ownerUserId" to ownerUserId),
                String::class.java,
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
                    "citations" to
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
                        ),
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
