package com.capstone.decision.application.brokerage

import com.capstone.decision.domain.risk.OrderIntentSnapshot
import java.time.Instant

data class BrokerageActor(
    val userId: String,
    val role: String,
    val securityVersion: Long,
    val requestId: String,
)

data class UserAcknowledgement(
    val warningsAccepted: Boolean,
)

data class SubmitMockOrderCommand(
    val decisionId: String,
    val orderIntent: OrderIntentSnapshot,
    val userAcknowledgement: UserAcknowledgement,
)

data class MockOrderProjection(
    val orderId: String,
    val accountId: String,
    val brokerageMode: String,
    val status: String,
    val submittedAt: Instant,
)

data class OrderDetailProjection(
    val orderId: String,
    val accountId: String,
    val brokerageMode: String,
    val status: String,
    val submittedAt: Instant,
    val decisionId: String,
)

data class MockBalancePositionProjection(
    val symbol: String,
    val quantity: Long,
    val marketValueKrw: Long,
    val isGoldEtfEtn: Boolean,
)

data class MockBalanceProjection(
    val accountId: String,
    val brokerageMode: String,
    val cashKrw: Long,
    val portfolioEquityKrw: Long,
    val marginRequirementKrw: Long,
    val positions: List<MockBalancePositionProjection>,
    val observedAt: Instant,
    val sourceVersion: String,
)

data class MockBuyableProjection(
    val accountId: String,
    val brokerageMode: String,
    val symbol: String,
    val estimatedPrice: Long,
    val buyableQuantity: Long,
    val buyableAmountKrw: Long,
    val cashKrw: Long,
    val observedAt: Instant,
    val sourceVersion: String,
)

data class BrokerageIdempotencyIdentity(
    val scopeHash: String,
    val ownerScopeHash: String,
    val requestHash: String,
)

data class StoredBrokerageIdempotencyResult(
    val requestHash: String,
    val projectionCanonicalJson: String,
    val expiresAt: Instant,
)

data class OrderableDecision(
    val decisionId: String,
    val evaluationId: String,
    val portfolioSource: String,
    val outcome: String,
    val mode: String,
    val canSubmitOrder: Boolean,
    val enforcementAction: String,
    val validUntil: Instant,
    val snapshotArtifactCanonicalJson: String,
    val portfolioOwnerScopeHash: String,
    val invalidated: Boolean,
    val invalidationReasonClass: String?,
    val consumedByOrderId: String?,
)

data class BrokerageOrderWriteRequest(
    val actor: BrokerageActor,
    val command: SubmitMockOrderCommand,
    val idempotency: BrokerageIdempotencyIdentity,
    val orderId: String,
    val projection: MockOrderProjection,
    val projectionCanonicalJson: String,
    val observedKillSwitchGeneration: Long,
    val createdAt: Instant,
)

data class BrokerageProviderOutcomeRequest(
    val actor: BrokerageActor,
    val orderId: String,
    val status: String,
    val providerOrderRefHash: String?,
    val trId: String?,
    val receivedAt: Instant,
)

data class StoredMockBalance(
    val accountId: String,
    val accountScopeHash: String,
    val cashKrw: Long,
    val portfolioEquityKrw: Long,
    val marginRequirementKrw: Long,
    val completeness: String,
    val positionCount: Int,
    val positions: List<MockBalancePositionProjection>,
    val observedAt: Instant,
    val sourceVersion: String,
)

data class BrokerageFieldViolation(
    val field: String,
    val reason: String,
)

class BrokerageValidationException(
    violations: List<BrokerageFieldViolation>,
) : RuntimeException("Brokerage request validation failed.") {
    val violations: List<BrokerageFieldViolation> =
        violations.sortedWith(compareBy(BrokerageFieldViolation::field, BrokerageFieldViolation::reason))
}

class BrokerageOrderNotFoundException : RuntimeException("Brokerage order was not found.")

class BrokerageDecisionNotFoundException : RuntimeException("Owned Decision was not found.")

class DecisionExpiredException : RuntimeException("Decision is expired.")

class BrokerageDecisionConflictException : RuntimeException("Decision has already been consumed by an order.")

class BrokerageIdempotencyConflictException : RuntimeException("Brokerage idempotency conflict.")

class BrokeragePersistenceReplayException(
    val projectionCanonicalJson: String,
) : RuntimeException("A durable mock order result already exists.")

class BrokerageUnavailableException(
    message: String = "Brokerage service is unavailable.",
    cause: Throwable? = null,
) : RuntimeException(message, cause)
