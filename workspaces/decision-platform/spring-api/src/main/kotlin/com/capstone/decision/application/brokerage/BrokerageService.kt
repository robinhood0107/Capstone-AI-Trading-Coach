package com.capstone.decision.application.brokerage

import com.capstone.decision.application.risk.KillSwitchBlockedException
import com.capstone.decision.application.risk.KillSwitchGuard
import com.capstone.decision.application.risk.KillSwitchUnavailableException
import org.springframework.beans.factory.ObjectProvider
import org.springframework.stereotype.Service
import java.time.Clock
import java.util.UUID

/**
 * S3.1 mock order entrypoint는 외부 broker 호출 전에 Decision/Kill Switch/one-use gate를 원자 검증한다.
 * optional gRPC port가 켜져도 KIS_MOCK만 호출하며 KIS_LIVE 주문 transport는 존재하지 않는다.
 */
@Service
class BrokerageService(
    private val persistencePort: BrokerageOrderPersistencePort,
    private val idempotencyHasher: BrokerageIdempotencyIdentityPort,
    private val projectionFactory: BrokerageProjectionFactory,
    private val killSwitchGuard: KillSwitchGuard,
    private val clock: Clock,
    private val gatewayProvider: ObjectProvider<BrokerageGatewayPort>,
) {
    fun submitMockOrder(
        actor: BrokerageActor,
        rawIdempotencyKey: String,
        command: SubmitMockOrderCommand,
    ): MockOrderProjection {
        val observedGate = killSwitchGuard.check()
        val now = clock.instant()
        val identity = idempotencyHasher.identity(actor.userId, rawIdempotencyKey, command)
        try {
            persistencePort
                .findIdempotencyResult(identity.scopeHash, identity.ownerScopeHash, now)
                ?.let { stored ->
                    if (stored.requestHash != identity.requestHash) {
                        throw BrokerageIdempotencyConflictException()
                    }
                    return projectionFactory.fromCanonicalJson(stored.projectionCanonicalJson)
                }
            val accountId =
                persistencePort.findOrderableDecisionAccountId(actor.userId, command.decisionId)
                    ?: throw BrokerageDecisionNotFoundException()
            val projection =
                projectionFactory.createSubmitted(
                    orderId = "ord_mock_${UUID.randomUUID().toString().replace("-", "")}",
                    accountId = accountId,
                    submittedAt = now,
                )
            persistencePort.persist(
                BrokerageOrderWriteRequest(
                    actor = actor,
                    command = command,
                    idempotency = identity,
                    orderId = projection.orderId,
                    projection = projection,
                    projectionCanonicalJson = projectionFactory.canonicalJson(projection),
                    observedKillSwitchGeneration = observedGate.generation,
                    createdAt = now,
                ),
            )
            val gateway = gatewayProvider.getIfAvailable() ?: return projection
            try {
                val providerResult =
                    gateway.submitMockOrder(
                        BrokerageGatewaySubmitRequest(
                            requestId = actor.requestId,
                            orderId = projection.orderId,
                            accountId = projection.accountId,
                            orderIntent = command.orderIntent,
                        ),
                    )
                val accepted =
                    persistencePort.recordProviderOutcome(
                        BrokerageProviderOutcomeRequest(
                            actor = actor,
                            orderId = projection.orderId,
                            status = "ACCEPTED",
                            providerOrderRefHash = providerResult.providerOrderRefHash,
                            trId = providerResult.trId,
                            receivedAt = providerResult.receivedAt,
                        ),
                    )
                return projectionFactory.fromDetail(accepted)
            } catch (exception: Exception) {
                recordProviderPending(actor, projection.orderId, now)
                throw BrokerageUnavailableException(
                    "KIS_MOCK submission requires reconciliation.",
                    exception,
                )
            }
        } catch (exception: BrokerageDecisionNotFoundException) {
            throw exception
        } catch (exception: DecisionExpiredException) {
            throw exception
        } catch (exception: BrokerageDecisionConflictException) {
            throw exception
        } catch (exception: BrokerageValidationException) {
            throw exception
        } catch (exception: BrokerageIdempotencyConflictException) {
            throw exception
        } catch (exception: BrokeragePersistenceReplayException) {
            return projectionFactory.fromCanonicalJson(exception.projectionCanonicalJson)
        } catch (exception: KillSwitchBlockedException) {
            throw exception
        } catch (exception: KillSwitchUnavailableException) {
            throw exception
        } catch (exception: BrokerageUnavailableException) {
            throw exception
        } catch (exception: Exception) {
            throw BrokerageUnavailableException(cause = exception)
        }
    }

    fun getOwnedOrder(
        actorUserId: String,
        orderId: String,
    ): OrderDetailProjection =
        try {
            persistencePort.findOwnedProjection(actorUserId, orderId)
                ?: throw BrokerageOrderNotFoundException()
        } catch (exception: BrokerageOrderNotFoundException) {
            throw exception
        } catch (exception: Exception) {
            throw BrokerageUnavailableException(cause = exception)
        }

    fun cancelOwnedOrder(
        actor: BrokerageActor,
        orderId: String,
    ): OrderDetailProjection {
        try {
            val requested =
                persistencePort.cancelOwnedOrder(
                    actor = actor,
                    orderId = orderId,
                    cancelledAt = clock.instant(),
                )
            val gateway = gatewayProvider.getIfAvailable() ?: return requested
            val providerResult =
                gateway.cancelMockOrder(
                    BrokerageGatewayCancelRequest(
                        requestId = actor.requestId,
                        orderId = requested.orderId,
                        accountId = requested.accountId,
                    ),
                )
            if (providerResult.status != "CANCELLED") {
                throw BrokerageUnavailableException("KIS_MOCK cancel was not confirmed.")
            }
            return persistencePort.recordProviderOutcome(
                BrokerageProviderOutcomeRequest(
                    actor = actor,
                    orderId = requested.orderId,
                    status = "CANCELLED",
                    providerOrderRefHash = null,
                    trId = null,
                    receivedAt = providerResult.receivedAt,
                ),
            )
        } catch (exception: BrokerageOrderNotFoundException) {
            throw exception
        } catch (exception: BrokerageDecisionConflictException) {
            throw exception
        } catch (exception: BrokerageUnavailableException) {
            throw exception
        } catch (exception: Exception) {
            throw BrokerageUnavailableException(cause = exception)
        }
    }

    fun getOwnedBalance(
        actor: BrokerageActor,
        accountId: String,
    ): MockBalanceProjection =
        try {
            val stored =
                persistencePort.findOwnedBalance(actor.userId, accountId)
                    ?: throw BrokerageOrderNotFoundException()
            val gateway = gatewayProvider.getIfAvailable()
            if (gateway != null) {
                val online =
                    gateway.getMockBalance(
                        BrokerageGatewayBalanceRequest(
                            requestId = actor.requestId,
                            accountId = accountId,
                        ),
                    )
                if (online.accountId != accountId) {
                    throw BrokerageUnavailableException("KIS_MOCK balance response identity mismatched.")
                }
                return MockBalanceProjection(
                    accountId = online.accountId,
                    brokerageMode = "KIS_MOCK",
                    cashKrw = online.cashKrw,
                    portfolioEquityKrw = online.portfolioEquityKrw,
                    marginRequirementKrw = online.marginRequirementKrw,
                    positions = online.positions,
                    observedAt = online.observedAt,
                    sourceVersion = online.sourceVersion,
                )
            }
            if (stored.completeness != "COMPLETE" || stored.positionCount != stored.positions.size) {
                throw BrokerageUnavailableException("KIS_MOCK balance source is incomplete.")
            }
            MockBalanceProjection(
                accountId = stored.accountId,
                brokerageMode = "KIS_MOCK",
                cashKrw = stored.cashKrw,
                portfolioEquityKrw = stored.portfolioEquityKrw,
                marginRequirementKrw = stored.marginRequirementKrw,
                positions = stored.positions,
                observedAt = stored.observedAt,
                sourceVersion = stored.sourceVersion,
            )
        } catch (exception: BrokerageOrderNotFoundException) {
            throw exception
        } catch (exception: BrokerageUnavailableException) {
            throw exception
        } catch (exception: Exception) {
            throw BrokerageUnavailableException(cause = exception)
        }

    fun getOwnedBuyable(
        actor: BrokerageActor,
        accountId: String,
        symbol: String,
        estimatedPrice: Long,
    ): MockBuyableProjection {
        val stored =
            try {
                persistencePort.findOwnedBalance(actor.userId, accountId)
                    ?: throw BrokerageOrderNotFoundException()
            } catch (exception: BrokerageOrderNotFoundException) {
                throw exception
            } catch (exception: Exception) {
                throw BrokerageUnavailableException(cause = exception)
            }
        val gateway = gatewayProvider.getIfAvailable()
        if (gateway != null) {
            try {
                val online =
                    gateway.getMockBuyable(
                        BrokerageGatewayBuyableRequest(
                            requestId = actor.requestId,
                            accountId = accountId,
                            symbol = symbol,
                            estimatedPriceKrw = estimatedPrice,
                        ),
                    )
                if (
                    online.accountId != accountId ||
                    online.symbol != symbol ||
                    online.estimatedPriceKrw != estimatedPrice
                ) {
                    throw BrokerageUnavailableException("KIS_MOCK buyable response identity mismatched.")
                }
                return MockBuyableProjection(
                    accountId = online.accountId,
                    brokerageMode = "KIS_MOCK",
                    symbol = online.symbol,
                    estimatedPrice = online.estimatedPriceKrw,
                    buyableQuantity = online.buyableQuantity,
                    buyableAmountKrw = online.buyableAmountKrw,
                    cashKrw = online.cashKrw,
                    observedAt = online.observedAt,
                    sourceVersion = online.sourceVersion,
                )
            } catch (exception: BrokerageUnavailableException) {
                throw exception
            } catch (exception: Exception) {
                throw BrokerageUnavailableException(cause = exception)
            }
        }
        if (stored.completeness != "COMPLETE" || stored.positionCount != stored.positions.size) {
            throw BrokerageUnavailableException("KIS_MOCK balance source is incomplete.")
        }
        val balance =
            MockBalanceProjection(
                accountId = stored.accountId,
                brokerageMode = "KIS_MOCK",
                cashKrw = stored.cashKrw,
                portfolioEquityKrw = stored.portfolioEquityKrw,
                marginRequirementKrw = stored.marginRequirementKrw,
                positions = stored.positions,
                observedAt = stored.observedAt,
                sourceVersion = stored.sourceVersion,
            )
        val quantity = balance.cashKrw / estimatedPrice
        val buyableAmount =
            try {
                Math.multiplyExact(quantity, estimatedPrice)
            } catch (exception: ArithmeticException) {
                throw BrokerageUnavailableException("Buyable amount overflowed.", exception)
            }
        return MockBuyableProjection(
            accountId = balance.accountId,
            brokerageMode = "KIS_MOCK",
            symbol = symbol,
            estimatedPrice = estimatedPrice,
            buyableQuantity = quantity,
            buyableAmountKrw = buyableAmount,
            cashKrw = balance.cashKrw,
            observedAt = balance.observedAt,
            sourceVersion = balance.sourceVersion,
        )
    }

    private fun recordProviderPending(
        actor: BrokerageActor,
        orderId: String,
        receivedAt: java.time.Instant,
    ) {
        try {
            persistencePort.recordProviderOutcome(
                BrokerageProviderOutcomeRequest(
                    actor = actor,
                    orderId = orderId,
                    status = "PENDING_RECONCILIATION",
                    providerOrderRefHash = null,
                    trId = null,
                    receivedAt = receivedAt,
                ),
            )
        } catch (_: Exception) {
            // 최초 SUBMITTED row 자체가 recovery anchor이므로 보조 pending 기록 실패가 원인 예외를 덮지 않는다.
        }
    }
}
