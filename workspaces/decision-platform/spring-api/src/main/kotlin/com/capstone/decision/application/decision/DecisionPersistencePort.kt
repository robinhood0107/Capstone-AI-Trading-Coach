package com.capstone.decision.application.decision

import java.time.Instant

interface DecisionPersistencePort {
    fun findIdempotencyResult(
        scopeHash: String,
        ownerScopeHash: String,
        now: Instant,
    ): StoredDecisionIdempotencyResult?

    fun persist(request: DecisionWriteRequest)

    fun findOwnedProjection(
        actorUserId: String,
        decisionId: String,
    ): DecisionProjection?

    fun findOwnedAudit(
        actorUserId: String,
        decisionId: String,
    ): DecisionAuditProjection?
}
