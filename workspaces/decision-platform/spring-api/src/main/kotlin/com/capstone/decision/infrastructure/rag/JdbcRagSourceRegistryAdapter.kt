package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagSourceRegistryEntry
import com.capstone.decision.application.rag.RagSourceRegistryList
import com.capstone.decision.application.rag.RagSourceRegistryPort
import com.capstone.decision.application.rag.RagSourceRegistryUnavailableException
import com.capstone.decision.infrastructure.risk.ActorScopedReadQuery
import org.springframework.stereotype.Repository
import java.sql.SQLException
import java.time.OffsetDateTime

@Repository
class JdbcRagSourceRegistryAdapter(
    private val actorScopedReadQuery: ActorScopedReadQuery,
) : RagSourceRegistryPort {
    /**
     * `decision_app`은 rag_sources table SELECT 권한 없이 definer 함수만 실행한다.
     * 함수와 ActorScopedReadQuery가 같은 actor binding을 재검증해 authenticated owner 경계를 고정한다.
     */
    override fun listVisibleSources(actorUserId: String): RagSourceRegistryList {
        try {
            val items =
                actorScopedReadQuery.query(
                    actorUserId = actorUserId,
                    sql =
                        """
                        SELECT source_id,
                               title,
                               institution,
                               topic,
                               attribution,
                               canonical_url,
                               last_checked_at
                        FROM read_rag_source_registry(?)
                        """.trimIndent(),
                    binder = { statement -> statement.setString(1, actorUserId) },
                ) { result ->
                    RagSourceRegistryEntry(
                        sourceId = result.getString("source_id"),
                        title = result.getString("title"),
                        institution = result.getString("institution"),
                        topic = result.getString("topic"),
                        attribution = result.getString("attribution"),
                        canonicalUrl = result.getString("canonical_url"),
                        lastCheckedAt = result.getNullableInstant("last_checked_at"),
                    )
                }
            return RagSourceRegistryList(items)
        } catch (exception: SQLException) {
            // JDBC driver의 checked exception도 public handler에 raw SQL/role 정보를 노출하지 않는다.
            throw RagSourceRegistryUnavailableException(exception)
        }
    }

    private fun java.sql.ResultSet.getNullableInstant(column: String) = getObject(column, OffsetDateTime::class.java)?.toInstant()
}
