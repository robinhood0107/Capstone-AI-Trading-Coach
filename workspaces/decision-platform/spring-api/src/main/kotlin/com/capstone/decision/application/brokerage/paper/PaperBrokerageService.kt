package com.capstone.decision.application.brokerage.paper

import com.capstone.decision.application.brokerage.BrokerageActor
import com.capstone.decision.application.brokerage.BrokerageDecisionConflictException
import com.capstone.decision.application.brokerage.BrokerageDecisionNotFoundException
import com.capstone.decision.application.brokerage.BrokerageFieldViolation
import com.capstone.decision.application.brokerage.BrokerageIdempotencyConflictException
import com.capstone.decision.application.brokerage.BrokerageOrderNotFoundException
import com.capstone.decision.application.brokerage.BrokeragePersistenceReplayException
import com.capstone.decision.application.brokerage.BrokerageUnavailableException
import com.capstone.decision.application.brokerage.BrokerageValidationException
import com.capstone.decision.application.brokerage.DecisionExpiredException
import com.capstone.decision.application.brokerage.SubmitMockOrderCommand
import com.capstone.decision.application.risk.KillSwitchBlockedException
import com.capstone.decision.application.risk.KillSwitchGuard
import com.capstone.decision.application.risk.KillSwitchUnavailableException
import com.capstone.decision.domain.brokerage.PaperFillFailure
import com.capstone.decision.domain.brokerage.PaperFillPolicy
import com.capstone.decision.domain.brokerage.PaperFillPolicyException
import com.capstone.decision.domain.brokerage.PaperFillRequest
import com.capstone.decision.domain.brokerage.TickSizePolicy
import com.capstone.decision.domain.brokerage.TickValidation
import com.capstone.decision.domain.risk.OrderIntentSnapshot
import com.capstone.decision.infrastructure.brokerage.PaperBrokerageProperties
import org.springframework.stereotype.Service
import tools.jackson.databind.ObjectMapper
import java.time.Clock
import java.time.Duration
import java.util.UUID

/**
 * INTERNAL_PAPER 전용 진입점은 저장 observation과 DB 원장만 사용한다.
 * gRPC/provider port를 생성자에 두지 않아 KIS 실패가 paper 체결로 전환되는 경로를 구조적으로 차단한다.
 */
@Service
class PaperBrokerageService(
    private val persistencePort: PaperOrderPersistencePort,
    private val idempotencyHasher: PaperIdempotencyIdentityPort,
    private val claimPort: PaperIdempotencyClaimPort,
    private val projectionFactory: PaperProjectionFactory,
    private val observability: PaperBrokerageObservability,
    private val properties: PaperBrokerageProperties,
    private val killSwitchGuard: KillSwitchGuard,
    private val objectMapper: ObjectMapper,
    private val clock: Clock,
) {
    fun submitPaperOrder(
        actor: BrokerageActor,
        rawIdempotencyKey: String,
        command: SubmitMockOrderCommand,
    ): PaperOrderProjection {
        val startedAt = System.nanoTime()
        var claim: PaperIdempotencyClaim? = null
        try {
            val observedGate = killSwitchGuard.check()
            val now = clock.instant()
            val identity = idempotencyHasher.paperIdentity(actor.userId, rawIdempotencyKey, command)
            persistencePort
                .findIdempotencyResult(identity.scopeHash, identity.ownerScopeHash, now)
                ?.let { stored ->
                    if (stored.requestHash != identity.requestHash) {
                        throw BrokerageIdempotencyConflictException()
                    }
                    return projectionFactory.fromCanonicalJson(stored.projectionCanonicalJson)
                }
            claim =
                when (val lookup = claimPort.acquire(identity.scopeHash, identity.requestHash)) {
                    is PaperClaimLookup.Acquired -> lookup.claim
                    PaperClaimLookup.Conflict -> throw BrokerageIdempotencyConflictException()
                    PaperClaimLookup.InProgress -> throw PaperIdempotencyInProgressException()
                }
            val context =
                persistencePort.findOrderContext(actor.userId, command.decisionId)
                    ?: throw BrokerageDecisionNotFoundException()
            val accountId = validateDecision(context, command, now)
            val quote = context.quote ?: throw BrokerageUnavailableException("Paper price source is unavailable.")
            val age = Duration.between(quote.observedAt, now)
            if (age.isNegative || age.seconds > properties.priceMaxAgeSeconds) {
                throw PaperDataStaleException()
            }
            validateTick(command)
            val fillDecision =
                PaperFillPolicy(properties.slippageBps)
                    .decide(
                        request =
                            PaperFillRequest(
                                side = command.orderIntent.side,
                                orderType = command.orderIntent.orderType,
                                quantity = command.orderIntent.quantity,
                                limitPriceKrw =
                                    command.orderIntent.estimatedPrice
                                        .takeIf { command.orderIntent.orderType == "LIMIT" },
                            ),
                        quote = quote,
                    )
            val projection =
                projectionFactory.create(
                    orderId = "ord_paper_${UUID.randomUUID().toString().replace("-", "")}",
                    accountId = accountId,
                    submittedAt = now,
                    decision = fillDecision,
                )
            persistencePort.persist(
                PaperOrderWriteRequest(
                    actor = actor,
                    command = command,
                    idempotency = identity,
                    context = context,
                    fillDecision = fillDecision,
                    orderId = projection.orderId,
                    projection = projection,
                    projectionCanonicalJson = projectionFactory.canonicalJson(projection),
                    observedKillSwitchGeneration = observedGate.generation,
                    priceMaxAgeSeconds = properties.priceMaxAgeSeconds,
                    createdAt = now,
                ),
            )
            projection.fill?.let { fill ->
                observability.recordFilled(
                    duration = Duration.ofNanos(System.nanoTime() - startedAt),
                    basis = PaperMetricPriceBasis.valueOf(fill.priceBasis),
                    orderId = projection.orderId,
                    decisionId = command.decisionId,
                    requestId = actor.requestId,
                )
            }
            return projection
        } catch (exception: PaperFillPolicyException) {
            val normalized =
                when (exception.failure) {
                    PaperFillFailure.PRICE_UNAVAILABLE ->
                        BrokerageUnavailableException("Paper price source is unavailable.", exception)
                    PaperFillFailure.INVALID_INPUT, PaperFillFailure.ARITHMETIC_OVERFLOW ->
                        BrokerageValidationException(
                            listOf(BrokerageFieldViolation("/orderIntent", exception.failure.name)),
                        )
                }
            observability.recordRejected(rejectionReason(normalized))
            throw normalized
        } catch (exception: BrokeragePersistenceReplayException) {
            return projectionFactory.fromCanonicalJson(exception.projectionCanonicalJson)
        } catch (exception: RuntimeException) {
            val normalized =
                when (exception) {
                    is BrokerageDecisionNotFoundException,
                    is DecisionExpiredException,
                    is BrokerageDecisionConflictException,
                    is BrokerageValidationException,
                    is BrokerageIdempotencyConflictException,
                    is PaperIdempotencyInProgressException,
                    is PaperDataStaleException,
                    is KillSwitchBlockedException,
                    is KillSwitchUnavailableException,
                    is BrokerageUnavailableException,
                    -> exception
                    else -> BrokerageUnavailableException(cause = exception)
                }
            observability.recordRejected(rejectionReason(normalized))
            throw normalized
        } finally {
            claim?.let { owned -> runCatching { claimPort.release(owned) } }
        }
    }

    fun getOwnedBalance(
        actorUserId: String,
        accountId: String,
    ): PaperBalanceProjection {
        val stored =
            persistencePort.findOwnedBalance(actorUserId, accountId)
                ?: throw BrokerageOrderNotFoundException()
        return PaperBalanceProjection(
            accountId = stored.accountId,
            brokerageMode = "INTERNAL_PAPER",
            cashKrw = stored.cashKrw,
            totalEquityKrw = stored.totalEquityKrw,
            positions = stored.positions,
            asOf = stored.asOf,
            valuationBasis = "LAST_FILL_PRICE_V1",
        )
    }

    fun getOwnedBuyable(
        actorUserId: String,
        accountId: String,
        symbol: String,
        estimatedPrice: Long,
    ): PaperBuyableProjection {
        val balance = getOwnedBalance(actorUserId, accountId)
        val quantity = balance.cashKrw / estimatedPrice
        val amount =
            try {
                Math.multiplyExact(quantity, estimatedPrice)
            } catch (exception: ArithmeticException) {
                throw BrokerageUnavailableException("Paper buyable amount overflowed.", exception)
            }
        return PaperBuyableProjection(
            accountId = accountId,
            brokerageMode = "INTERNAL_PAPER",
            symbol = symbol,
            estimatedPrice = estimatedPrice,
            buyableQuantity = quantity,
            buyableAmountKrw = amount,
            cashKrw = balance.cashKrw,
            asOf = balance.asOf,
        )
    }

    private fun validateDecision(
        context: PaperOrderContext,
        command: SubmitMockOrderCommand,
        now: java.time.Instant,
    ): String {
        if (context.portfolioSource != "INTERNAL_PAPER") {
            throw BrokerageValidationException(
                listOf(BrokerageFieldViolation("/decisionId", "UNSUPPORTED_PORTFOLIO_SOURCE")),
            )
        }
        if (!context.canSubmitOrder || context.outcome !in setOf("ALLOW", "WARN") || context.invalidated) {
            throw KillSwitchBlockedException()
        }
        if (!context.validUntil.isAfter(now)) {
            throw DecisionExpiredException()
        }
        if (context.consumedByOrderId != null) {
            throw BrokerageDecisionConflictException()
        }
        val accountId = context.accountId ?: throw BrokerageDecisionNotFoundException()
        if (context.accountStatus != "ACTIVE") {
            throw BrokerageUnavailableException("Paper account is unavailable.")
        }
        if (context.enforcementAction != "NONE" && !command.userAcknowledgement.warningsAccepted) {
            throw KillSwitchBlockedException()
        }
        val storedOrder = objectMapper.readTree(context.snapshotArtifactCanonicalJson).path("orderIntent")
        val pinnedOrder =
            OrderIntentSnapshot(
                symbol = storedOrder.path("symbol").stringValue(),
                side = storedOrder.path("side").stringValue(),
                orderType = storedOrder.path("orderType").stringValue(),
                quantity = storedOrder.path("quantity").stringValue().toLong(),
                estimatedPrice = storedOrder.path("estimatedPrice").stringValue().toLong(),
                estimatedAmount = storedOrder.path("estimatedAmount").stringValue().toLong(),
                timeframe = storedOrder.path("timeframe").stringValue(),
                strategyId = storedOrder.path("strategyId").stringValue(),
            )
        if (pinnedOrder != command.orderIntent) {
            throw BrokerageValidationException(
                listOf(BrokerageFieldViolation("/orderIntent", "DECISION_MISMATCH")),
            )
        }
        return accountId
    }

    private fun validateTick(command: SubmitMockOrderCommand) {
        when (
            val tick =
                TickSizePolicy.validate(
                    orderType = command.orderIntent.orderType,
                    priceKrw = command.orderIntent.estimatedPrice,
                    context = null,
                )
        ) {
            TickValidation.Valid -> Unit
            TickValidation.Unavailable ->
                throw BrokerageValidationException(
                    listOf(BrokerageFieldViolation("/orderIntent/estimatedPrice", "TICK_TABLE_UNVERIFIED")),
                )
            is TickValidation.Invalid ->
                throw BrokerageValidationException(
                    listOf(BrokerageFieldViolation("/orderIntent/estimatedPrice", tick.reason)),
                )
        }
    }

    private fun rejectionReason(exception: RuntimeException): PaperRejectionReason =
        when (exception) {
            is BrokerageValidationException -> PaperRejectionReason.VALIDATION
            is BrokerageDecisionNotFoundException -> PaperRejectionReason.NOT_FOUND
            is DecisionExpiredException -> PaperRejectionReason.DECISION_EXPIRED
            is BrokerageDecisionConflictException -> PaperRejectionReason.CONFLICT
            is BrokerageIdempotencyConflictException -> PaperRejectionReason.IDEMPOTENCY_CONFLICT
            is PaperIdempotencyInProgressException -> PaperRejectionReason.IDEMPOTENCY_IN_PROGRESS
            is PaperDataStaleException -> PaperRejectionReason.DATA_STALE
            is KillSwitchBlockedException -> PaperRejectionReason.RISK_BLOCKED
            is KillSwitchUnavailableException -> PaperRejectionReason.RISK_UNAVAILABLE
            else -> PaperRejectionReason.BROKERAGE_UNAVAILABLE
        }
}
