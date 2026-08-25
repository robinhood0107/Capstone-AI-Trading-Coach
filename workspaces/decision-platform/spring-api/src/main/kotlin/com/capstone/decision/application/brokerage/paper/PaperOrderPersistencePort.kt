package com.capstone.decision.application.brokerage.paper

import com.capstone.decision.application.brokerage.BrokerageIdempotencyIdentity
import com.capstone.decision.application.brokerage.StoredBrokerageIdempotencyResult
import com.capstone.decision.application.brokerage.SubmitMockOrderCommand
import java.time.Instant

interface PaperIdempotencyIdentityPort {
    fun paperIdentity(
        actorUserId: String,
        rawKey: String,
        command: SubmitMockOrderCommand,
    ): BrokerageIdempotencyIdentity
}

interface PaperOrderPersistencePort {
    fun findIdempotencyResult(
        actorUserId: String,
        scopeHash: String,
        ownerScopeHash: String,
        now: Instant,
    ): StoredBrokerageIdempotencyResult?

    fun findOrderContext(
        actorUserId: String,
        decisionId: String,
    ): PaperOrderContext?

    fun persist(request: PaperOrderWriteRequest)

    fun findOwnedBalance(
        actorUserId: String,
        accountId: String,
    ): StoredPaperBalance?
}
