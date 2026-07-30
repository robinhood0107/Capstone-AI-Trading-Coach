package com.capstone.decision.infrastructure.rag

import com.capstone.decision.application.rag.RagSourceRegistryEntry
import com.capstone.decision.application.rag.RagSourceRegistryList
import com.capstone.decision.application.rag.RagSourceRegistryPort
import com.capstone.decision.infrastructure.risk.ActorScopedReadQuery
import org.springframework.stereotype.Repository
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
        val items =
            actorScopedReadQuery.query(
                actorUserId = actorUserId,
                sql =
                    """
                    SELECT source_id,
                           title,
                           source_type,
                           tier,
                           access_level,
                           license_decision,
                           external_processing_allowed,
                           initial_processing,
                           retention_mode,
                           retention_days,
                           retention_owner,
                           canonical_url,
                           attribution,
                           ingest_status,
                           created_at,
                           retired_at,
                           last_checked_at,
                           latest_check_result
                    FROM read_rag_source_registry(?)
                    """.trimIndent(),
                binder = { statement -> statement.setString(1, actorUserId) },
            ) { result ->
                RagSourceRegistryEntry(
                    sourceId = result.getString("source_id"),
                    title = result.getString("title"),
                    sourceType = result.getString("source_type"),
                    tier = result.getString("tier"),
                    accessLevel = result.getString("access_level"),
                    licenseDecision = result.getString("license_decision"),
                    externalProcessingAllowed = result.getBoolean("external_processing_allowed"),
                    initialProcessing = result.getString("initial_processing"),
                    retentionMode = result.getString("retention_mode"),
                    retentionDays = result.getInt("retention_days"),
                    retentionOwner = result.getString("retention_owner"),
                    canonicalUrl = result.getString("canonical_url"),
                    attribution = result.getString("attribution"),
                    ingestStatus = result.getString("ingest_status"),
                    createdAt = result.getObject("created_at", OffsetDateTime::class.java).toInstant(),
                    retiredAt = result.getNullableInstant("retired_at"),
                    lastCheckedAt = result.getNullableInstant("last_checked_at"),
                    latestCheckResult = result.getString("latest_check_result"),
                )
            }
        return RagSourceRegistryList(items)
    }

    private fun java.sql.ResultSet.getNullableInstant(column: String) = getObject(column, OffsetDateTime::class.java)?.toInstant()
}
