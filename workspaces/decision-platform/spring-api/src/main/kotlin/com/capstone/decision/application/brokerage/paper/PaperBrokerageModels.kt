package com.capstone.decision.application.brokerage.paper

import com.capstone.decision.application.brokerage.BrokerageActor
import com.capstone.decision.application.brokerage.BrokerageIdempotencyIdentity
import com.capstone.decision.application.brokerage.SubmitMockOrderCommand
import com.capstone.decision.domain.brokerage.PaperFillDecision
import com.capstone.decision.domain.brokerage.PaperPriceObservation
import java.time.Instant

data class PaperFillProjection(
    val quantity: Long,
    val priceKrw: Long,
    val amountKrw: Long,
    val priceBasis: String,
    val slippageBps: Int,
    val feeModel: String,
    val observedAt: Instant,
)

data class PaperOrderProjection(
    val orderId: String,
    val accountId: String,
    val brokerageMode: String,
    val status: String,
    val submittedAt: Instant,
    val fill: PaperFillProjection?,
)

data class PaperBalancePositionProjection(
    val symbol: String,
    val quantity: Long,
    val marketValueKrw: Long,
    val averagePriceKrw: Long,
)

data class PaperBalanceProjection(
    val accountId: String,
    val brokerageMode: String,
    val cashKrw: Long,
    val totalEquityKrw: Long,
    val positions: List<PaperBalancePositionProjection>,
    val asOf: Instant,
    val valuationBasis: String,
)

data class PaperBuyableProjection(
    val accountId: String,
    val brokerageMode: String,
    val symbol: String,
    val estimatedPrice: Long,
    val buyableQuantity: Long,
    val buyableAmountKrw: Long,
    val cashKrw: Long,
    val asOf: Instant,
)

data class PaperOrderContext(
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
    val consumedByOrderId: String?,
    val accountId: String?,
    val accountStatus: String?,
    val quote: PaperPriceObservation?,
)

data class PaperOrderWriteRequest(
    val actor: BrokerageActor,
    val command: SubmitMockOrderCommand,
    val idempotency: BrokerageIdempotencyIdentity,
    val context: PaperOrderContext,
    val fillDecision: PaperFillDecision,
    val orderId: String,
    val projection: PaperOrderProjection,
    val projectionCanonicalJson: String,
    val observedKillSwitchGeneration: Long,
    val priceMaxAgeSeconds: Int,
    val createdAt: Instant,
)

data class StoredPaperBalance(
    val accountId: String,
    val cashKrw: Long,
    val totalEquityKrw: Long,
    val positions: List<PaperBalancePositionProjection>,
    val asOf: Instant,
)

class PaperDataStaleException : RuntimeException("Stored paper price source is stale.")
