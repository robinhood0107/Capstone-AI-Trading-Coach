package com.capstone.decision.application.brokerage

import com.capstone.decision.domain.brokerage.FillObservation
import com.capstone.decision.domain.brokerage.FillTransitionResult
import com.capstone.decision.domain.brokerage.OrderFillTransition
import com.capstone.decision.domain.brokerage.OrderReconciliationInput
import com.capstone.decision.domain.brokerage.OrderReconciliationPolicy
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional
import java.time.Clock

/**
 * 저장된 sanitized 체결 관측만 순수 전이로 미리 판정하고 DB의 동일 판정과 일치할 때만 반영한다.
 * 외부 provider transport나 자동 보정은 이 transaction에 존재하지 않는다.
 */
@Service
class OrderFillApplicationService(
    private val persistencePort: OrderFillPersistencePort,
    private val clock: Clock,
) {
    @Transactional
    fun reconcile(
        actor: BrokerageActor,
        orderId: String,
    ): OrderFillReconciliationProjection {
        try {
            persistencePort.acquireReconciliationLock(actor, orderId)
            val stored = persistencePort.readReconciliationState(actor, orderId)
            var state = stored.orderState
            var eventCount = 0
            stored.observations.forEach { storedObservation ->
                val result =
                    OrderFillTransition.apply(
                        current = state,
                        observation =
                            FillObservation(
                                execType = storedObservation.execType,
                                fillQuantity = storedObservation.fillQuantity,
                                fillPriceKrw = storedObservation.fillPriceKrw,
                                cumulativeQuantity = storedObservation.cumulativeQuantity,
                                leavesQuantity = storedObservation.leavesQuantity,
                            ),
                    )
                when (result) {
                    is FillTransitionResult.Applied -> {
                        state = result.next
                        eventCount++
                    }

                    is FillTransitionResult.Invalid -> eventCount++
                    FillTransitionResult.Duplicate -> Unit
                }
            }
            val reconciliationStatus =
                OrderReconciliationPolicy
                    .evaluate(
                        OrderReconciliationInput(
                            quantity = state.quantity,
                            filledQuantity = state.filledQuantity,
                            leavesQuantity = state.leavesQuantity,
                            unfilledTerminatedQuantity = state.unfilledTerminatedQuantity,
                            storedAverageFillPriceKrw = state.averageFillPriceKrw,
                            observedFillQuantity =
                                stored.observedFillQuantity.takeIf { stored.observationCount > 0 },
                            recomputedAverageFillPriceKrw = stored.recomputedAverageFillPriceKrw,
                            providerFinalAverageFillPriceKrw = stored.providerFinalAverageFillPriceKrw,
                        ),
                    ).name
            return persistencePort.applyStoredFills(
                OrderFillApplyRequest(
                    actor = actor,
                    orderId = orderId,
                    reconciledAt = clock.instant(),
                    expectedFinal =
                        ExpectedOrderFillState(
                            status = state.status.name,
                            filledQuantity = state.filledQuantity,
                            leavesQuantity = state.leavesQuantity,
                            unfilledTerminatedQuantity = state.unfilledTerminatedQuantity,
                            averageFillPriceKrw = state.averageFillPriceKrw,
                            reconciliationStatus = reconciliationStatus,
                            appliedEventCount = eventCount,
                            hasMore = stored.hasMore,
                        ),
                ),
            )
        } catch (exception: BrokerageOrderNotFoundException) {
            throw exception
        } catch (exception: BrokerageUnavailableException) {
            throw exception
        } catch (exception: OrderFillLogicDivergenceException) {
            throw BrokerageUnavailableException("Order fill reconciliation logic diverged.", exception)
        } catch (exception: Exception) {
            throw BrokerageUnavailableException("Order fill reconciliation is unavailable.", exception)
        }
    }
}
