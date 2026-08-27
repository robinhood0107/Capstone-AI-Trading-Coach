package com.capstone.decision.infrastructure.journal

import com.capstone.decision.application.journal.CreateJournalCommand
import com.capstone.decision.application.journal.DeleteJournalCommand
import com.capstone.decision.application.journal.JournalAccessDeniedException
import com.capstone.decision.application.journal.JournalConflictException
import com.capstone.decision.application.journal.JournalCursor
import com.capstone.decision.application.journal.JournalIdempotencyConflictException
import com.capstone.decision.application.journal.JournalLinks
import com.capstone.decision.application.journal.JournalNotFoundException
import com.capstone.decision.application.journal.JournalProjection
import com.capstone.decision.application.journal.JournalRepository
import com.capstone.decision.application.journal.JournalStorageException
import com.capstone.decision.application.journal.ReplaceJournalCommand
import com.capstone.decision.application.security.OwnerWriteHashes
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityDeniedException
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import com.capstone.decision.infrastructure.security.ActorRlsScope
import org.springframework.beans.factory.ObjectProvider
import org.springframework.dao.DataAccessException
import org.springframework.jdbc.core.RowMapper
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Transactional
import tools.jackson.databind.ObjectMapper
import java.sql.ResultSet
import java.sql.SQLException
import java.time.OffsetDateTime

/** Journal row, link ownership, CAS와 idempotent result를 V89 owner RLS transaction 하나에 묶는다. */
@Repository
class JdbcJournalRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val actorRlsScope: ActorRlsScope,
    private val objectMapper: ObjectMapper,
) : JournalRepository {
    @Transactional
    override fun create(
        ownerUserId: String,
        journalId: String,
        command: CreateJournalCommand,
        scopeHash: String,
        requestHash: String,
    ): JournalProjection =
        write(ownerUserId, "CREATE_JOURNAL", journalId, "CREATE", scopeHash, requestHash) { jdbc ->
            requireOwnedLinks(jdbc, ownerUserId, command.links)
            val projection =
                jdbc
                    .query(
                        """
                        INSERT INTO journals(
                          journal_id,user_id,decision_id,order_id,rag_answer_id,title,body,tags,source_json,
                          created_at,updated_at,deleted_at,owner_scope,backtest_run_id,automation_run_id,version
                        ) VALUES (
                          :journalId,:ownerUserId,:decisionId,:orderId,:ragAnswerId,:title,:body,:tags,
                          CAST(:sourceJson AS jsonb),statement_timestamp(),statement_timestamp(),NULL,
                          :ownerScope,:backtestRunId,:automationRunId,1
                        )
                        RETURNING *
                        """.trimIndent(),
                        journalParameters(ownerUserId, journalId, command.title, command.content, command.tags, command.links),
                        journalRowMapper,
                    ).single()
            persistIdempotency(jdbc, scopeHash, ownerUserId, "CREATE", requestHash, projection)
            projection
        }

    @Transactional
    override fun replace(
        ownerUserId: String,
        journalId: String,
        command: ReplaceJournalCommand,
        scopeHash: String,
        requestHash: String,
    ): JournalProjection =
        write(ownerUserId, "PATCH_JOURNAL", journalId, "PATCH", scopeHash, requestHash) { jdbc ->
            val current = lockCurrent(jdbc, ownerUserId, journalId)
            if (current.version != command.expectedVersion || current.version == Int.MAX_VALUE) {
                throw JournalConflictException()
            }
            requireOwnedLinks(jdbc, ownerUserId, command.links)
            val parameters =
                journalParameters(ownerUserId, journalId, command.title, command.content, command.tags, command.links)
                    .addValue("expectedVersion", command.expectedVersion)
            val projection =
                jdbc
                    .query(
                        """
                        UPDATE journals SET decision_id=:decisionId,order_id=:orderId,rag_answer_id=:ragAnswerId,
                          title=:title,body=:body,tags=:tags,backtest_run_id=:backtestRunId,
                          automation_run_id=:automationRunId,version=version+1,updated_at=statement_timestamp()
                        WHERE journal_id=:journalId AND user_id=:ownerUserId AND deleted_at IS NULL
                          AND version=:expectedVersion
                        RETURNING *
                        """.trimIndent(),
                        parameters,
                        journalRowMapper,
                    ).singleOrNull() ?: throw JournalConflictException()
            persistIdempotency(jdbc, scopeHash, ownerUserId, "PATCH", requestHash, projection)
            projection
        }

    @Transactional
    override fun delete(
        ownerUserId: String,
        journalId: String,
        command: DeleteJournalCommand,
        scopeHash: String,
        requestHash: String,
    ): JournalProjection =
        write(ownerUserId, "DELETE_JOURNAL", journalId, "DELETE", scopeHash, requestHash) { jdbc ->
            val current = lockCurrent(jdbc, ownerUserId, journalId)
            if (current.version != command.expectedVersion || current.version == Int.MAX_VALUE) {
                throw JournalConflictException()
            }
            val projection =
                jdbc
                    .query(
                        """
                        UPDATE journals SET deleted_at=statement_timestamp(),updated_at=statement_timestamp(),
                          version=version+1
                        WHERE journal_id=:journalId AND user_id=:ownerUserId AND deleted_at IS NULL
                          AND version=:expectedVersion
                        RETURNING *
                        """.trimIndent(),
                        mapOf(
                            "journalId" to journalId,
                            "ownerUserId" to ownerUserId,
                            "expectedVersion" to command.expectedVersion,
                        ),
                        journalRowMapper,
                    ).singleOrNull() ?: throw JournalConflictException()
            persistIdempotency(jdbc, scopeHash, ownerUserId, "DELETE", requestHash, projection)
            projection
        }

    @Transactional
    override fun list(
        ownerUserId: String,
        limit: Int,
        after: JournalCursor?,
    ): List<JournalProjection> {
        try {
            val jdbc = jdbc()
            actorRlsScope.open(
                jdbc,
                ownerUserId,
                ActorCapabilityBinding.request(
                    "LIST_JOURNALS",
                    "JOURNAL_LIST",
                    ownerUserId,
                    ActorCapabilityRolePolicy.OWNER,
                    ownerUserId,
                    limit.toString(),
                    after?.updatedAt?.toString(),
                    after?.journalId,
                ),
            )
            return jdbc.query(
                """
                SELECT * FROM journals
                WHERE user_id=:ownerUserId AND deleted_at IS NULL
                  AND (CAST(:afterUpdatedAt AS timestamptz) IS NULL OR (updated_at,journal_id)<(:afterUpdatedAt,:afterJournalId))
                ORDER BY updated_at DESC,journal_id DESC
                LIMIT :limit
                """.trimIndent(),
                mapOf(
                    "ownerUserId" to ownerUserId,
                    "afterUpdatedAt" to after?.updatedAt,
                    "afterJournalId" to after?.journalId,
                    "limit" to limit,
                ),
                journalRowMapper,
            )
        } catch (error: ActorCapabilityDeniedException) {
            throw JournalAccessDeniedException(error)
        } catch (error: DataAccessException) {
            throw translate(error)
        }
    }

    private fun <T> write(
        ownerUserId: String,
        capabilityOperation: String,
        journalId: String,
        idempotencyOperation: String,
        scopeHash: String,
        requestHash: String,
        block: (NamedParameterJdbcTemplate) -> T,
    ): T {
        return try {
            val jdbc = jdbc()
            actorRlsScope.open(
                jdbc,
                ownerUserId,
                ActorCapabilityBinding(
                    operation = capabilityOperation,
                    targetKind = "JOURNAL",
                    targetId = journalId,
                    payloadHash = requestHash,
                    rolePolicy = ActorCapabilityRolePolicy.OWNER,
                ),
            )
            jdbc.query(
                "SELECT pg_advisory_xact_lock(hashtextextended(:scopeHash,89))",
                mapOf("scopeHash" to scopeHash),
            ) { _, _ -> true }
            val prior = findIdempotency(jdbc, scopeHash)
            if (prior != null) {
                if (prior.ownerUserId != ownerUserId || prior.operation != idempotencyOperation || prior.requestHash != requestHash) {
                    throw JournalIdempotencyConflictException()
                }
                @Suppress("UNCHECKED_CAST")
                return decodeJournal(prior.resultJson) as T
            }
            block(jdbc)
        } catch (error: ActorCapabilityDeniedException) {
            throw JournalAccessDeniedException(error)
        } catch (error: DataAccessException) {
            throw translate(error)
        }
    }

    private fun lockCurrent(
        jdbc: NamedParameterJdbcTemplate,
        ownerUserId: String,
        journalId: String,
    ): JournalProjection =
        jdbc
            .query(
                """
                SELECT * FROM journals
                WHERE journal_id=:journalId AND user_id=:ownerUserId AND deleted_at IS NULL
                FOR UPDATE
                """.trimIndent(),
                mapOf("journalId" to journalId, "ownerUserId" to ownerUserId),
                journalRowMapper,
            ).singleOrNull() ?: throw JournalNotFoundException()

    private fun requireOwnedLinks(
        jdbc: NamedParameterJdbcTemplate,
        ownerUserId: String,
        links: JournalLinks,
    ) {
        val owned =
            jdbc.queryForObject(
                "SELECT p1_journal_links_owned(:ownerUserId,:decisionId,:backtestRunId,:ragAnswerId,:orderId,:automationRunId)",
                mapOf(
                    "ownerUserId" to ownerUserId,
                    "decisionId" to links.decisionId,
                    "backtestRunId" to links.backtestRunId,
                    "ragAnswerId" to links.ragAnswerId,
                    "orderId" to links.orderId,
                    "automationRunId" to links.automationRunId,
                ),
                Boolean::class.java,
            ) == true
        if (!owned) throw JournalNotFoundException()
    }

    private fun journalParameters(
        ownerUserId: String,
        journalId: String,
        title: String,
        content: String,
        tags: List<String>,
        links: JournalLinks,
    ): MapSqlParameterSource =
        MapSqlParameterSource()
            .addValue("journalId", journalId)
            .addValue("ownerUserId", ownerUserId)
            .addValue("ownerScope", OwnerWriteHashes.ownerScope(ownerUserId))
            .addValue("title", title)
            .addValue("body", content)
            .addValue("tags", tags.toTypedArray())
            .addValue("decisionId", links.decisionId)
            .addValue("backtestRunId", links.backtestRunId)
            .addValue("ragAnswerId", links.ragAnswerId)
            .addValue("orderId", links.orderId)
            .addValue("automationRunId", links.automationRunId)
            .addValue("sourceJson", SOURCE_JSON)

    private fun persistIdempotency(
        jdbc: NamedParameterJdbcTemplate,
        scopeHash: String,
        ownerUserId: String,
        operation: String,
        requestHash: String,
        projection: JournalProjection,
    ) {
        jdbc.update(
            """
            INSERT INTO journal_idempotency(scope_hash,user_id,operation,request_hash,journal_id,result_json)
            VALUES (:scopeHash,:ownerUserId,:operation,:requestHash,:journalId,CAST(:resultJson AS jsonb))
            """.trimIndent(),
            mapOf(
                "scopeHash" to scopeHash,
                "ownerUserId" to ownerUserId,
                "operation" to operation,
                "requestHash" to requestHash,
                "journalId" to projection.journalId,
                "resultJson" to objectMapper.writeValueAsString(projection),
            ),
        )
    }

    private fun findIdempotency(
        jdbc: NamedParameterJdbcTemplate,
        scopeHash: String,
    ): IdempotencyRow? =
        jdbc
            .query(
                """
                SELECT user_id,operation,request_hash,result_json::text result_json
                FROM journal_idempotency WHERE scope_hash=:scopeHash FOR SHARE
                """.trimIndent(),
                mapOf("scopeHash" to scopeHash),
            ) { result, _ ->
                IdempotencyRow(
                    result.getString("user_id"),
                    result.getString("operation"),
                    result.getString("request_hash"),
                    result.getString("result_json"),
                )
            }.singleOrNull()

    private val journalRowMapper =
        RowMapper { result: ResultSet, _ ->
            JournalProjection(
                journalId = result.getString("journal_id"),
                ownerScope = result.getString("owner_scope"),
                title = result.getString("title"),
                content = result.getString("body"),
                tags = (result.getArray("tags").array as Array<*>).map { it as String },
                links =
                    JournalLinks(
                        decisionId = result.getString("decision_id"),
                        backtestRunId = result.getString("backtest_run_id"),
                        ragAnswerId = result.getString("rag_answer_id"),
                        orderId = result.getString("order_id"),
                        automationRunId = result.getString("automation_run_id"),
                    ),
                version = result.getInt("version"),
                createdAt = result.getObject("created_at", OffsetDateTime::class.java),
                updatedAt = result.getObject("updated_at", OffsetDateTime::class.java),
                deletedAt = result.getObject("deleted_at", OffsetDateTime::class.java),
            )
        }

    private fun decodeJournal(json: String): JournalProjection = objectMapper.readValue(json, JournalProjection::class.java)

    private fun translate(error: DataAccessException): RuntimeException =
        when (error.sqlState()) {
            "23505" -> JournalIdempotencyConflictException()
            "42501" -> JournalAccessDeniedException(error)
            else -> JournalStorageException(error)
        }

    private fun Throwable.sqlState(): String? {
        var current: Throwable? = this
        while (current != null) {
            if (current is SQLException) return current.sqlState
            current = current.cause
        }
        return null
    }

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable() ?: throw JournalStorageException(IllegalStateException("Journal JDBC is unavailable."))

    private data class IdempotencyRow(
        val ownerUserId: String,
        val operation: String,
        val requestHash: String,
        val resultJson: String,
    )

    private companion object {
        const val SOURCE_JSON = "{\"contractId\":\"journal.v1\",\"origin\":\"OWNER_API\"}"
    }
}
