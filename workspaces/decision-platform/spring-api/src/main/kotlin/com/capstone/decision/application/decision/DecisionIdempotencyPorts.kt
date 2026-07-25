package com.capstone.decision.application.decision

import java.time.Duration

interface DecisionIdempotencyIdentityPort {
    fun identity(
        actorUserId: String,
        rawKey: String,
        command: EvaluateOrderCommand,
    ): DecisionIdempotencyIdentity
}

data class DecisionIdempotencyClaim(
    val scopeHash: String,
    val requestHash: String,
    val token: String,
)

sealed interface DecisionClaimLookup {
    data class Acquired(
        val claim: DecisionIdempotencyClaim,
    ) : DecisionClaimLookup

    data object Conflict : DecisionClaimLookup

    data object InProgress : DecisionClaimLookup
}

interface DecisionIdempotencyClaimPort {
    fun acquire(
        scopeHash: String,
        requestHash: String,
    ): DecisionClaimLookup

    fun release(claim: DecisionIdempotencyClaim)
}

data class DecisionValidityPolicy(
    val configuredValidity: Duration,
) {
    init {
        require(!configuredValidity.isNegative && !configuredValidity.isZero)
    }
}
