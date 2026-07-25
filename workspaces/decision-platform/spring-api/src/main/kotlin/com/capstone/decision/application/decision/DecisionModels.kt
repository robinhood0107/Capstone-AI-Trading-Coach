package com.capstone.decision.application.decision

import com.capstone.decision.domain.principle.PrincipleId
import com.capstone.decision.domain.principle.PrincipleMode
import com.capstone.decision.domain.risk.OrderIntentSnapshot
import java.math.BigDecimal
import java.time.Instant

data class EvaluateOrderCommand(
    val principleId: PrincipleId,
    val portfolioSource: String,
    val orderIntent: OrderIntentSnapshot,
)

data class DecisionActor(
    val userId: String,
    val role: String,
    val requestId: String,
)

data class DecisionProjection(
    val decisionId: String,
    val createdAt: Instant,
    val validUntil: Instant,
    val principleId: String,
    val principleVersionId: String,
    val principleVersion: Int,
    val portfolioSource: String,
    val mode: String,
    val enforcementAction: String,
    val riskDecision: RiskDecisionProjection,
)

data class RiskDecisionProjection(
    val schemaVersion: String,
    val evaluationId: String,
    val decisionId: String,
    val validUntil: Instant,
    val catalogVersion: Int,
    val readinessPolicyVersion: String,
    val decision: String,
    val mode: String,
    val canSubmitOrder: Boolean,
    val principleVersionId: String,
    val principleVersion: Int,
    val portfolioSource: String,
    val semanticInputHash: String,
    val snapshotArtifactHash: String,
    val violations: List<DecisionViolationProjection>,
    val issues: List<DecisionIssueProjection>,
    val warnings: List<DecisionWarningProjection>,
    val abstentions: List<DecisionAbstentionProjection>,
    val riskItems: List<DecisionRiskItemProjection>,
)

data class DecisionViolationProjection(
    val ruleId: String,
    val severity: String,
    val message: String,
    val metricValue: BigDecimal,
    val threshold: BigDecimal,
)

data class DecisionIssueProjection(
    val ruleId: String,
    val code: String,
    val message: String,
    val source: String,
)

data class DecisionWarningProjection(
    val ruleId: String,
    val code: String,
    val message: String,
    val source: String,
)

data class DecisionAbstentionProjection(
    val ruleId: String,
    val code: String,
    val disposition: String,
    val message: String,
    val component: String,
)

data class DecisionRiskItemProjection(
    val metric: String,
    val value: BigDecimal,
    val severity: String,
    val source: String,
    val eventCodes: List<String>,
    val mappingVersion: String,
    val sourceRefs: List<String>,
)

data class DecisionAuditProjection(
    val auditId: String,
    val action: String,
    val requestId: String,
    val createdAt: Instant,
    val payload: DecisionAuditPayloadProjection,
)

data class DecisionAuditPayloadProjection(
    val evaluationId: String,
    val decisionId: String,
    val outcome: String,
    val principleVersionId: String,
    val semanticInputHash: String,
    val snapshotArtifactHash: String,
)

data class DecisionIdempotencyIdentity(
    val scopeHash: String,
    val ownerScopeHash: String,
    val requestHash: String,
)

data class DecisionWriteRequest(
    val actor: DecisionActor,
    val decisionId: String,
    val evaluationId: String,
    val projection: DecisionProjection,
    val projectionCanonicalJson: String,
    val snapshotArtifactCanonicalJson: String,
    val semanticInputHash: String,
    val snapshotArtifactHash: String,
    val snapshotSchemaVersion: String,
    val catalogVersion: Int,
    val readinessPolicyVersion: String,
    val mappingVersions: Map<String, String>,
    val orderIntent: OrderIntentSnapshot,
    val idempotency: DecisionIdempotencyIdentity,
    val principleMode: PrincipleMode,
)

data class StoredDecisionIdempotencyResult(
    val requestHash: String,
    val projectionCanonicalJson: String,
    val expiresAt: Instant,
)

data class DecisionFieldViolation(
    val field: String,
    val reason: String,
)

class DecisionValidationException(
    violations: List<DecisionFieldViolation>,
) : IllegalArgumentException("Decision request validation failed.") {
    val violations: List<DecisionFieldViolation> =
        violations.sortedWith(
            compareBy(DecisionFieldViolation::field, DecisionFieldViolation::reason),
        )
}

class DecisionNotFoundException : RuntimeException("Decision resource was not found.")

class DecisionVersionConflictException : RuntimeException("Pinned Principle changed before persistence.")

class DecisionIdempotencyConflictException : RuntimeException("Decision idempotency conflict.")

class DecisionIdempotencyInProgressException : RuntimeException("Decision idempotency request is in progress.")

class DecisionPersistenceReplayException(
    val projectionCanonicalJson: String,
) : RuntimeException("A durable Decision result already exists.")

class DecisionTechnicalException(
    cause: Throwable? = null,
) : RuntimeException("Decision evaluation failed closed.", cause)
