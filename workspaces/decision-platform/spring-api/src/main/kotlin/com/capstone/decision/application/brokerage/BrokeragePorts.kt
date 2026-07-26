package com.capstone.decision.application.brokerage

import com.capstone.decision.domain.risk.OrderIntentSnapshot
import java.time.Instant

interface BrokerageIdempotencyIdentityPort {
    fun identity(
        actorUserId: String,
        rawKey: String,
        command: SubmitMockOrderCommand,
    ): BrokerageIdempotencyIdentity
}

data class BrokerageGatewaySubmitRequest(
    val requestId: String,
    val orderId: String,
    val accountId: String,
    val orderIntent: OrderIntentSnapshot,
)

data class BrokerageGatewaySubmitResult(
    val orderId: String,
    val providerOrderRefHash: String,
    val trId: String,
    val receivedAt: Instant,
)

data class BrokerageGatewayCancelRequest(
    val requestId: String,
    val orderId: String,
    val accountId: String,
)

data class BrokerageGatewayCancelResult(
    val orderId: String,
    val status: String,
    val receivedAt: Instant,
)

interface BrokerageGatewayPort {
    fun submitMockOrder(request: BrokerageGatewaySubmitRequest): BrokerageGatewaySubmitResult

    fun cancelMockOrder(request: BrokerageGatewayCancelRequest): BrokerageGatewayCancelResult
}

interface BrokerageOrderPersistencePort {
    fun findIdempotencyResult(
        scopeHash: String,
        ownerScopeHash: String,
        now: java.time.Instant,
    ): StoredBrokerageIdempotencyResult?

    fun persist(request: BrokerageOrderWriteRequest)

    fun findOrderableDecisionAccountId(
        actorUserId: String,
        decisionId: String,
    ): String?

    fun findOwnedProjection(
        actorUserId: String,
        orderId: String,
    ): OrderDetailProjection?

    fun cancelOwnedOrder(
        actor: BrokerageActor,
        orderId: String,
        cancelledAt: java.time.Instant,
    ): OrderDetailProjection

    fun findOwnedBalance(
        actorUserId: String,
        accountId: String,
    ): StoredMockBalance?
}
