package com.capstone.decision.infrastructure.decision

import com.capstone.decision.application.decision.DecisionAuditPayloadProjection
import com.capstone.decision.application.decision.DecisionAuditProjection
import com.capstone.decision.application.decision.DecisionIdempotencyConflictException
import com.capstone.decision.application.decision.DecisionPersistencePort
import com.capstone.decision.application.decision.DecisionPersistenceReplayException
import com.capstone.decision.application.decision.DecisionProjection
import com.capstone.decision.application.decision.DecisionVersionConflictException
import com.capstone.decision.application.decision.DecisionWriteRequest
import com.capstone.decision.application.decision.StoredDecisionIdempotencyResult
import com.capstone.decision.application.risk.KillSwitchBlockedException
import com.capstone.decision.application.risk.KillSwitchUnavailableException
import com.capstone.decision.infrastructure.risk.ActorScopedReadQuery
import com.capstone.decision.infrastructure.security.ActorCapabilityIssuer
import org.springframework.beans.factory.ObjectProvider
import org.springframework.dao.DataAccessException
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Transactional
import tools.jackson.databind.ObjectMapper
import java.sql.SQLException
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset

/**
 * source read/evaluation이 끝난 뒤 이 adapter의 한 transaction만 decision/trace/audit/outbox/idempotency를 기록한다.
 */
@Repository
class JdbcDecisionPersistenceAdapter(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val actorScopedReadQuery: ActorScopedReadQuery,
    private val projectionFactory: com.capstone.decision.application.decision.DecisionProjectionFactory,
    private val objectMapper: ObjectMapper,
    private val actorCapabilityIssuer: ActorCapabilityIssuer,
) : DecisionPersistencePort {
    override fun findIdempotencyResult(
        scopeHash: String,
        ownerScopeHash: String,
        now: Instant,
    ): StoredDecisionIdempotencyResult? =
        jdbc()
            .query(
                """
                SELECT request_hash, result_canonical_json, expires_at
                FROM find_decision_idempotency_result(
                  :scopeHash,
                  :ownerScopeHash,
                  :now
                )
                """.trimIndent(),
                mapOf(
                    "scopeHash" to scopeHash,
                    "ownerScopeHash" to ownerScopeHash,
                    "now" to now.utc(),
                ),
            ) { result, _ ->
                StoredDecisionIdempotencyResult(
                    requestHash = result.getString("request_hash"),
                    projectionCanonicalJson = result.getString("result_canonical_json"),
                    expiresAt = result.getObject("expires_at", OffsetDateTime::class.java).toInstant(),
                )
            }.singleOrNull()

    @Transactional
    override fun persist(request: DecisionWriteRequest) {
        val jdbc = jdbc()
        try {
            val result =
                jdbc
                    .query(
                        """
                        SELECT outcome, result_canonical_json
                        FROM persist_decision_bundle_authorized(:capability, CAST(:bundle AS jsonb))
                        """.trimIndent(),
                        mapOf(
                            "capability" to actorCapabilityIssuer.issue(request.actor.userId),
                            "bundle" to objectMapper.writeValueAsString(decisionBundle(request)),
                        ),
                    ) { row, _ -> row.getString("outcome") to row.getString("result_canonical_json") }
                    .single()
            when (result.first) {
                "INSERTED" -> return
                "REPLAY" -> throw DecisionPersistenceReplayException(requireNotNull(result.second))
                "CONFLICT" -> throw DecisionIdempotencyConflictException()
                else -> error("Decision capability returned an invalid outcome.")
            }
        } catch (exception: DataAccessException) {
            when (exception.sqlState()) {
                "40001" -> throw DecisionVersionConflictException()
                "55000" -> throw KillSwitchBlockedException()
                "P5501" -> throw KillSwitchUnavailableException(exception)
                else -> throw exception
            }
        }
    }

    override fun findOwnedProjection(
        actorUserId: String,
        decisionId: String,
    ): DecisionProjection? =
        actorScopedReadQuery
            .query(
                actorUserId = actorUserId,
                requestedDecisionId = decisionId,
                sql =
                    """
                    SELECT result_canonical_json
                    FROM decision_owner_projection
                    WHERE decision_id = ?
                    LIMIT 1
                    """.trimIndent(),
                binder = { statement -> statement.setString(1, decisionId) },
            ) { result ->
                projectionFactory.fromCanonicalJson(result.getString("result_canonical_json"))
            }.singleOrNull()

    override fun findOwnedAudit(
        actorUserId: String,
        decisionId: String,
    ): DecisionAuditProjection? =
        actorScopedReadQuery
            .query(
                actorUserId = actorUserId,
                requestedDecisionId = decisionId,
                sql =
                    """
                    SELECT audit_log_id,
                           action,
                           request_id,
                           created_at,
                           payload_json ->> 'evaluationId' AS evaluation_id,
                           payload_json ->> 'decisionId' AS decision_id,
                           payload_json ->> 'outcome' AS outcome,
                           payload_json ->> 'principleVersionId' AS principle_version_id,
                           payload_json ->> 'semanticInputHash' AS semantic_input_hash,
                           payload_json ->> 'snapshotArtifactHash' AS snapshot_artifact_hash
                    FROM decision_audit_projection
                    WHERE decision_id = ?
                    LIMIT 1
                    """.trimIndent(),
                binder = { statement -> statement.setString(1, decisionId) },
            ) { result ->
                DecisionAuditProjection(
                    auditId = result.getString("audit_log_id"),
                    action = result.getString("action"),
                    requestId = result.getString("request_id"),
                    createdAt = result.getObject("created_at", OffsetDateTime::class.java).toInstant(),
                    payload =
                        DecisionAuditPayloadProjection(
                            evaluationId = result.getString("evaluation_id"),
                            decisionId = result.getString("decision_id"),
                            outcome = result.getString("outcome"),
                            principleVersionId = result.getString("principle_version_id"),
                            semanticInputHash = result.getString("semantic_input_hash"),
                            snapshotArtifactHash = result.getString("snapshot_artifact_hash"),
                        ),
                )
            }.singleOrNull()

    private fun decisionBundle(request: DecisionWriteRequest): Map<String, Any> =
        mapOf(
            "decisionId" to request.decisionId,
            "evaluationId" to request.evaluationId,
            "actorUserId" to request.actor.userId,
            "actorRole" to request.actor.role,
            "requestId" to request.actor.requestId,
            "scopeHash" to request.idempotency.scopeHash,
            "requestHash" to request.idempotency.requestHash,
            "ownerScopeHash" to request.idempotency.ownerScopeHash,
            "portfolioSource" to request.projection.portfolioSource,
            "symbol" to request.orderIntent.symbol,
            "side" to request.orderIntent.side,
            "outcome" to request.projection.riskDecision.decision,
            "canSubmitOrder" to request.projection.riskDecision.canSubmitOrder,
            "enforcementAction" to request.projection.enforcementAction,
            "evaluationAsOf" to request.projection.createdAt.utc(),
            "createdAt" to request.projection.createdAt.utc(),
            "validUntil" to request.projection.validUntil.utc(),
            "resultSchemaVersion" to RESULT_SCHEMA_VERSION,
            "snapshotSchemaVersion" to request.snapshotSchemaVersion,
            "catalogVersion" to request.catalogVersion,
            "readinessPolicyVersion" to request.readinessPolicyVersion,
            "mappingVersions" to request.mappingVersions,
            "semanticInputHash" to request.semanticInputHash,
            "snapshotArtifactHash" to request.snapshotArtifactHash,
            "resultCanonicalJson" to request.projectionCanonicalJson,
            "snapshotCanonicalJson" to request.snapshotArtifactCanonicalJson,
            "principleId" to request.projection.principleId,
            "principleVersion" to request.projection.principleVersion,
            "principleVersionId" to request.projection.principleVersionId,
            "mode" to request.principleMode.name,
            "violations" to
                request.projection.riskDecision.violations.map { violation ->
                    mapOf(
                        "ruleId" to violation.ruleId,
                        "severity" to violation.severity,
                        "observedValue" to violation.metricValue,
                        "thresholdValue" to violation.threshold,
                        "message" to violation.message,
                    )
                },
        )

    private fun Throwable.sqlState(): String? {
        var current: Throwable? = this
        while (current != null) {
            if (current is SQLException) return current.sqlState
            current = current.cause
        }
        return null
    }

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: error("Decision persistence JDBC access is unavailable without a configured DataSource.")

    private fun Instant.utc(): OffsetDateTime = OffsetDateTime.ofInstant(this, ZoneOffset.UTC)

    private companion object {
        const val RESULT_SCHEMA_VERSION = "s2-3-decision-response/v1"
    }
}
