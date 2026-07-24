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
import com.capstone.decision.infrastructure.risk.ActorScopedReadQuery
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Transactional
import tools.jackson.databind.ObjectMapper
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.UUID

/**
 * source read/evaluation이 끝난 뒤 이 adapter의 한 transaction만 decision/trace/audit/outbox/idempotency를 기록한다.
 */
@Repository
class JdbcDecisionPersistenceAdapter(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val actorScopedReadQuery: ActorScopedReadQuery,
    private val projectionFactory: com.capstone.decision.application.decision.DecisionProjectionFactory,
    private val objectMapper: ObjectMapper,
) : DecisionPersistencePort {
    override fun findIdempotencyResult(
        scopeHash: String,
        now: Instant,
    ): StoredDecisionIdempotencyResult? =
        jdbc()
            .query(
                """
                SELECT request_hash, result_canonical_json, expires_at
                FROM decision_idempotency_results
                WHERE scope_hash = :scopeHash
                  AND expires_at > :now
                ORDER BY generation DESC
                LIMIT 1
                """.trimIndent(),
                mapOf(
                    "scopeHash" to scopeHash,
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
        jdbc.queryForObject(
            "SELECT set_config('app.actor_user_id', :actorUserId, true)",
            mapOf("actorUserId" to request.actor.userId),
            String::class.java,
        )
        jdbc.queryForObject(
            "SELECT pg_advisory_xact_lock(hashtextextended(:scopeHash, :seed))",
            mapOf(
                "scopeHash" to request.idempotency.scopeHash,
                "seed" to ADVISORY_LOCK_SEED,
            ),
            Any::class.java,
        )
        val previous =
            jdbc
                .query(
                    """
                    SELECT generation, request_hash, result_canonical_json, expires_at
                    FROM decision_idempotency_results
                    WHERE scope_hash = :scopeHash
                    ORDER BY generation DESC
                    LIMIT 1
                    """.trimIndent(),
                    mapOf("scopeHash" to request.idempotency.scopeHash),
                ) { result, _ ->
                    PreviousIdempotencyResult(
                        generation = result.getInt("generation"),
                        requestHash = result.getString("request_hash"),
                        projectionCanonicalJson = result.getString("result_canonical_json"),
                        expiresAt = result.getObject("expires_at", OffsetDateTime::class.java).toInstant(),
                    )
                }.singleOrNull()
        if (previous != null && previous.expiresAt.isAfter(request.projection.createdAt)) {
            if (previous.requestHash == request.idempotency.requestHash) {
                throw DecisionPersistenceReplayException(previous.projectionCanonicalJson)
            }
            throw DecisionIdempotencyConflictException()
        }
        val inserted =
            jdbc.update(
                """
                INSERT INTO decisions (
                  decision_id, evaluation_id, user_id, principle_id, principle_version_id,
                  principle_version, portfolio_source, symbol, side, outcome, mode,
                  can_submit_order, enforcement_action, evaluation_as_of, created_at, valid_until,
                  result_schema_version, snapshot_schema_version, catalog_version,
                  readiness_policy_version, mapping_versions_json, semantic_input_hash,
                  snapshot_artifact_hash, result_json
                )
                SELECT
                  :decisionId, :evaluationId, :actorUserId, principle.principle_id,
                  version.principle_version_id, version.version, :portfolioSource,
                  :symbol, :side, :outcome, version.mode, :canSubmitOrder,
                  :enforcementAction, :evaluationAsOf, :createdAt, :validUntil,
                  :resultSchemaVersion, :snapshotSchemaVersion, :catalogVersion,
                  :readinessPolicyVersion, CAST(:mappingVersionsJson AS jsonb),
                  :semanticInputHash, :snapshotArtifactHash, CAST(:resultJson AS jsonb)
                FROM principles principle
                JOIN principle_versions version
                  ON version.principle_id = principle.principle_id
                 AND version.version = principle.current_version
                WHERE principle.principle_id = :principleId
                  AND principle.user_id = :actorUserId
                  AND principle.status = 'ACTIVE'
                  AND principle.current_version = :principleVersion
                  AND version.principle_version_id = :principleVersionId
                  AND version.status = 'ACTIVE'
                  AND version.mode = :mode
                """.trimIndent(),
                decisionParameters(request),
            )
        if (inserted != 1) {
            throw DecisionVersionConflictException()
        }
        insertViolations(jdbc, request)
        insertArtifact(jdbc, request)
        insertTraces(jdbc, request)
        insertAudit(jdbc, request)
        insertOutbox(jdbc, request)
        insertIdempotency(jdbc, request, (previous?.generation ?: 0) + 1)
    }

    override fun findOwnedProjection(
        actorUserId: String,
        decisionId: String,
    ): DecisionProjection? =
        actorScopedReadQuery
            .query(
                actorUserId = actorUserId,
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

    private fun decisionParameters(request: DecisionWriteRequest): Map<String, Any> =
        mapOf(
            "decisionId" to request.decisionId,
            "evaluationId" to request.evaluationId,
            "actorUserId" to request.actor.userId,
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
            "mappingVersionsJson" to objectMapper.writeValueAsString(request.mappingVersions),
            "semanticInputHash" to request.semanticInputHash,
            "snapshotArtifactHash" to request.snapshotArtifactHash,
            "resultJson" to request.projectionCanonicalJson,
            "principleId" to request.projection.principleId,
            "principleVersion" to request.projection.principleVersion,
            "principleVersionId" to request.projection.principleVersionId,
            "mode" to request.principleMode.name,
        )

    private fun insertViolations(
        jdbc: NamedParameterJdbcTemplate,
        request: DecisionWriteRequest,
    ) {
        request.projection.riskDecision.violations.forEachIndexed { index, violation ->
            jdbc.update(
                """
                INSERT INTO decision_violations (
                  violation_id, decision_id, evaluation_id, ordinal, rule_id, severity,
                  metric, public_code, observed_value, threshold_value, message, created_at
                )
                VALUES (
                  :violationId, :decisionId, :evaluationId, :ordinal, :ruleId, :severity,
                  NULL, NULL, :observedValue, :thresholdValue, :message, :createdAt
                )
                """.trimIndent(),
                mapOf(
                    "violationId" to id("vio"),
                    "decisionId" to request.decisionId,
                    "evaluationId" to request.evaluationId,
                    "ordinal" to index + 1,
                    "ruleId" to violation.ruleId,
                    "severity" to violation.severity,
                    "observedValue" to violation.metricValue,
                    "thresholdValue" to violation.threshold,
                    "message" to violation.message,
                    "createdAt" to request.projection.createdAt.utc(),
                ),
            )
        }
    }

    private fun insertArtifact(
        jdbc: NamedParameterJdbcTemplate,
        request: DecisionWriteRequest,
    ) {
        jdbc.update(
            """
            INSERT INTO decision_artifacts (
              decision_id, evaluation_id, result_canonical_json,
              snapshot_artifact_canonical_json, semantic_input_hash,
              snapshot_artifact_hash, created_at
            )
            VALUES (
              :decisionId, :evaluationId, :resultJson, :snapshotJson,
              :semanticInputHash, :snapshotArtifactHash, :createdAt
            )
            """.trimIndent(),
            mapOf(
                "decisionId" to request.decisionId,
                "evaluationId" to request.evaluationId,
                "resultJson" to request.projectionCanonicalJson,
                "snapshotJson" to request.snapshotArtifactCanonicalJson,
                "semanticInputHash" to request.semanticInputHash,
                "snapshotArtifactHash" to request.snapshotArtifactHash,
                "createdAt" to request.projection.createdAt.utc(),
            ),
        )
    }

    private fun insertTraces(
        jdbc: NamedParameterJdbcTemplate,
        request: DecisionWriteRequest,
    ) {
        TRACE_TYPES.forEachIndexed { index, traceType ->
            val payload =
                mapOf(
                    "decisionId" to request.decisionId,
                    "evaluationId" to request.evaluationId,
                    "traceType" to traceType,
                )
            jdbc.update(
                """
                INSERT INTO decision_traces (
                  trace_id, decision_id, evaluation_id, step, trace_type, trace_json, created_at
                )
                VALUES (
                  :traceId, :decisionId, :evaluationId, :step, :traceType,
                  CAST(:traceJson AS jsonb), :createdAt
                )
                """.trimIndent(),
                mapOf(
                    "traceId" to id("trc"),
                    "decisionId" to request.decisionId,
                    "evaluationId" to request.evaluationId,
                    "step" to index + 1,
                    "traceType" to traceType,
                    "traceJson" to objectMapper.writeValueAsString(payload),
                    "createdAt" to request.projection.createdAt.utc(),
                ),
            )
        }
    }

    private fun insertAudit(
        jdbc: NamedParameterJdbcTemplate,
        request: DecisionWriteRequest,
    ) {
        jdbc.update(
            """
            INSERT INTO audit_logs (
              audit_log_id, user_id, actor_role, action, target_type, target_id,
              request_id, payload_json, created_at
            )
            VALUES (
              :auditId, :actorUserId, :actorRole, 'DECISION_EVALUATED', 'DECISION',
              :decisionId, :requestId, CAST(:payloadJson AS jsonb), :createdAt
            )
            """.trimIndent(),
            referencePayloadParameters(request) +
                mapOf(
                    "auditId" to id("aud"),
                    "actorUserId" to request.actor.userId,
                    "actorRole" to request.actor.role,
                    "requestId" to request.actor.requestId,
                    "createdAt" to request.projection.createdAt.utc(),
                ),
        )
    }

    private fun insertOutbox(
        jdbc: NamedParameterJdbcTemplate,
        request: DecisionWriteRequest,
    ) {
        jdbc.update(
            """
            INSERT INTO event_outbox (
              event_id, event_type, aggregate_type, aggregate_id, partition_key,
              payload_json, schema_version, status, retry_count, created_at, updated_at
            )
            VALUES (
              :eventId, 'risk.decision-created.v1', 'DECISION', :decisionId, :decisionId,
              CAST(:payloadJson AS jsonb), '1.0.0', 'PENDING', 0, :createdAt, :createdAt
            )
            """.trimIndent(),
            referencePayloadParameters(request) +
                mapOf(
                    "eventId" to id("evt"),
                    "createdAt" to request.projection.createdAt.utc(),
                ),
        )
    }

    private fun insertIdempotency(
        jdbc: NamedParameterJdbcTemplate,
        request: DecisionWriteRequest,
        generation: Int,
    ) {
        jdbc.update(
            """
            INSERT INTO decision_idempotency_results (
              idempotency_result_id, scope_hash, generation, request_hash, owner_scope_hash,
              purpose_version, decision_id, evaluation_id, http_status, content_type,
              result_canonical_json, created_at, expires_at
            )
            VALUES (
              :resultId, :scopeHash, :generation, :requestHash, :ownerScopeHash,
              :purposeVersion, :decisionId, :evaluationId, 200, 'application/json',
              :resultJson, :createdAt, :expiresAt
            )
            """.trimIndent(),
            mapOf(
                "resultId" to id("idr"),
                "scopeHash" to request.idempotency.scopeHash,
                "generation" to generation,
                "requestHash" to request.idempotency.requestHash,
                "ownerScopeHash" to request.idempotency.ownerScopeHash,
                "purposeVersion" to DecisionProperties.PURPOSE_VERSION,
                "decisionId" to request.decisionId,
                "evaluationId" to request.evaluationId,
                "resultJson" to request.projectionCanonicalJson,
                "createdAt" to request.projection.createdAt.utc(),
                "expiresAt" to
                    request.projection.createdAt
                        .plus(IDEMPOTENCY_RETENTION)
                        .utc(),
            ),
        )
    }

    private fun referencePayloadParameters(request: DecisionWriteRequest): Map<String, Any> {
        val payload =
            mapOf(
                "evaluationId" to request.evaluationId,
                "decisionId" to request.decisionId,
                "outcome" to request.projection.riskDecision.decision,
                "principleVersionId" to request.projection.principleVersionId,
                "semanticInputHash" to request.semanticInputHash,
                "snapshotArtifactHash" to request.snapshotArtifactHash,
            )
        return mapOf(
            "decisionId" to request.decisionId,
            "payloadJson" to objectMapper.writeValueAsString(payload),
        )
    }

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: error("Decision persistence JDBC access is unavailable without a configured DataSource.")

    private fun id(prefix: String): String = "${prefix}_${UUID.randomUUID().toString().replace("-", "")}"

    private fun Instant.utc(): OffsetDateTime = OffsetDateTime.ofInstant(this, ZoneOffset.UTC)

    private data class PreviousIdempotencyResult(
        val generation: Int,
        val requestHash: String,
        val projectionCanonicalJson: String,
        val expiresAt: Instant,
    )

    private companion object {
        const val RESULT_SCHEMA_VERSION = "s2-3-decision-response/v1"
        const val ADVISORY_LOCK_SEED = 2303L
        val IDEMPOTENCY_RETENTION: java.time.Duration = java.time.Duration.ofHours(24)
        val TRACE_TYPES =
            listOf(
                "ORDER_VALIDATED",
                "PRINCIPLE_PINNED",
                "FRESHNESS_EVALUATED",
                "RULES_EVALUATED",
                "FINDINGS_COMPOSED",
                "POLICY_APPLIED",
                "PERSISTED",
            )
    }
}
