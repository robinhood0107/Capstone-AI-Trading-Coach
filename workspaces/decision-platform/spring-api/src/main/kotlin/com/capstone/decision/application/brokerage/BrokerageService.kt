package com.capstone.decision.application.brokerage

import com.capstone.decision.application.risk.KillSwitchBlockedException
import com.capstone.decision.application.risk.KillSwitchGuard
import com.capstone.decision.application.risk.KillSwitchUnavailableException
import org.springframework.stereotype.Service
import java.time.Clock
import java.util.UUID

/**
 * S3.1 mock order entrypoint는 외부 broker 호출 전에 Decision/Kill Switch/one-use gate를 원자 검증한다.
 * 이 서비스는 KIS live 계좌·주문 transport를 열지 않고, 저장된 mock ledger 결과만 반환한다.
 */
@Service
class BrokerageService(
    private val persistencePort: BrokerageOrderPersistencePort,
    private val idempotencyHasher: BrokerageIdempotencyIdentityPort,
    private val projectionFactory: BrokerageProjectionFactory,
    private val killSwitchGuard: KillSwitchGuard,
    private val clock: Clock,
) {
    fun submitMockOrder(
        actor: BrokerageActor,
        rawIdempotencyKey: String,
        command: SubmitMockOrderCommand,
    ): MockOrderProjection {
        killSwitchGuard.check()
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
                    createdAt = now,
                ),
            )
            return projection
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
    ): OrderDetailProjection =
        try {
            persistencePort.cancelOwnedOrder(
                actor = actor,
                orderId = orderId,
                cancelledAt = clock.instant(),
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

    fun getOwnedBalance(
        actorUserId: String,
        accountId: String,
    ): MockBalanceProjection =
        try {
            val balance =
                persistencePort.findOwnedBalance(actorUserId, accountId)
                    ?: throw BrokerageOrderNotFoundException()
            if (balance.completeness != "COMPLETE" || balance.positionCount != balance.positions.size) {
                throw BrokerageUnavailableException("KIS_MOCK balance source is incomplete.")
            }
            MockBalanceProjection(
                accountId = balance.accountId,
                brokerageMode = "KIS_MOCK",
                cashKrw = balance.cashKrw,
                portfolioEquityKrw = balance.portfolioEquityKrw,
                marginRequirementKrw = balance.marginRequirementKrw,
                positions = balance.positions,
                observedAt = balance.observedAt,
                sourceVersion = balance.sourceVersion,
            )
        } catch (exception: BrokerageOrderNotFoundException) {
            throw exception
        } catch (exception: BrokerageUnavailableException) {
            throw exception
        } catch (exception: Exception) {
            throw BrokerageUnavailableException(cause = exception)
        }

    fun getOwnedBuyable(
        actorUserId: String,
        accountId: String,
        symbol: String,
        estimatedPrice: Long,
    ): MockBuyableProjection {
        val balance = getOwnedBalance(actorUserId, accountId)
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
}
