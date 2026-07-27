package com.capstone.decision

import com.capstone.decision.domain.brokerage.FillExecutionType
import com.capstone.decision.domain.brokerage.FillInvalidReason
import com.capstone.decision.domain.brokerage.FillObservation
import com.capstone.decision.domain.brokerage.FillTransitionResult
import com.capstone.decision.domain.brokerage.OrderFillAggregation
import com.capstone.decision.domain.brokerage.OrderFillState
import com.capstone.decision.domain.brokerage.OrderFillStatus
import com.capstone.decision.domain.brokerage.OrderFillTransition
import com.capstone.decision.domain.brokerage.OrderReconciliationInput
import com.capstone.decision.domain.brokerage.OrderReconciliationPolicy
import com.capstone.decision.domain.brokerage.OrderReconciliationStatus
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import java.math.BigInteger

class OrderFillDomainTest {
    @Test
    fun `상태 8종과 exec type 4종 전이표를 전수 검증한다`() {
        var cases = 0
        OrderFillStatus.entries.forEach { status ->
            FillExecutionType.entries.forEach { execType ->
                val current =
                    if (status == OrderFillStatus.PARTIALLY_FILLED) {
                        state(status = status, filled = 4, leaves = 6, average = 100)
                    } else if (status in setOf(OrderFillStatus.FILLED, OrderFillStatus.CANCELLED, OrderFillStatus.REJECTED)) {
                        state(status = status, filled = 10, leaves = 0, average = 100)
                    } else {
                        state(status = status)
                    }
                val observation =
                    when (execType) {
                        FillExecutionType.PARTIAL_FILL ->
                            observation(
                                execType,
                                fillQuantity = if (current.filledQuantity == 0L) 4 else 2,
                                fillPrice = 100,
                                cumulative = if (current.filledQuantity == 0L) 4 else 6,
                                leaves = if (current.filledQuantity == 0L) 6 else 4,
                            )
                        FillExecutionType.FILL ->
                            observation(
                                execType,
                                fillQuantity = current.quantity - current.filledQuantity,
                                fillPrice = 100,
                                cumulative = current.quantity,
                                leaves = 0,
                            )
                        FillExecutionType.CANCELLED,
                        FillExecutionType.REJECTED,
                        -> observation(execType, 0, null, current.filledQuantity, 0)
                    }

                val result = OrderFillTransition.apply(current, observation)
                if (status in setOf(OrderFillStatus.FILLED, OrderFillStatus.CANCELLED, OrderFillStatus.REJECTED)) {
                    assertEquals(
                        FillTransitionResult.Invalid(FillInvalidReason.TERMINAL_STATE),
                        result,
                        "$status/$execType",
                    )
                } else if (
                    status == OrderFillStatus.CANCEL_REQUESTED &&
                    execType == FillExecutionType.PARTIAL_FILL
                ) {
                    assertTrue(result is FillTransitionResult.Invalid, "$status/$execType -> $result")
                    assertEquals(
                        "CANCEL_REQUESTED_PARTIAL_FILL",
                        (result as FillTransitionResult.Invalid).reason.name,
                        "$status/$execType",
                    )
                } else {
                    assertTrue(result is FillTransitionResult.Applied, "$status/$execType -> $result")
                }
                cases++
            }
        }
        assertEquals(32, cases)
    }

    @Test
    fun `종료 상태 3종은 모든 exec type을 invalid로 남긴다`() {
        val terminalStatuses =
            listOf(
                OrderFillStatus.FILLED,
                OrderFillStatus.CANCELLED,
                OrderFillStatus.REJECTED,
            )

        terminalStatuses.forEach { status ->
            FillExecutionType.entries.forEach { execType ->
                val result =
                    OrderFillTransition.apply(
                        current =
                            state(
                                status = status,
                                filled = 10,
                                leaves = 0,
                                average = 100,
                            ),
                        observation =
                            observation(
                                execType = execType,
                                fillQuantity = 0,
                                fillPrice = null,
                                cumulative = 10,
                                leaves = 0,
                            ),
                    )
                assertEquals(
                    FillTransitionResult.Invalid(FillInvalidReason.TERMINAL_STATE),
                    result,
                    "$status/$execType",
                )
            }
        }
    }

    @Test
    fun `부분체결과 전량체결은 누적수량과 평균가를 순차 갱신한다`() {
        val partial =
            OrderFillTransition.apply(
                state(),
                observation(
                    execType = FillExecutionType.PARTIAL_FILL,
                    fillQuantity = 4,
                    fillPrice = 100,
                    cumulative = 4,
                    leaves = 6,
                ),
            ) as FillTransitionResult.Applied
        assertEquals(OrderFillStatus.PARTIALLY_FILLED, partial.next.status)
        assertEquals(4, partial.next.filledQuantity)
        assertEquals(100, partial.next.averageFillPriceKrw)

        val filled =
            OrderFillTransition.apply(
                partial.next,
                observation(
                    execType = FillExecutionType.FILL,
                    fillQuantity = 6,
                    fillPrice = 101,
                    cumulative = 10,
                    leaves = 0,
                ),
            ) as FillTransitionResult.Applied
        assertEquals(OrderFillStatus.FILLED, filled.next.status)
        assertEquals(10, filled.next.filledQuantity)
        assertEquals(100, filled.next.averageFillPriceKrw)
        assertEquals(0, filled.next.leavesQuantity)
    }

    @Test
    fun `같은 누적 fill은 duplicate이고 역행과 초과는 invalid다`() {
        val current =
            state(
                status = OrderFillStatus.PARTIALLY_FILLED,
                filled = 4,
                leaves = 6,
                average = 100,
            )
        assertEquals(
            FillTransitionResult.Duplicate,
            OrderFillTransition.apply(
                current,
                observation(FillExecutionType.PARTIAL_FILL, 1, 100, 4, 6),
            ),
        )
        assertEquals(
            FillTransitionResult.Invalid(FillInvalidReason.NON_MONOTONIC_CUM_QTY),
            OrderFillTransition.apply(
                current,
                observation(FillExecutionType.PARTIAL_FILL, 1, 100, 3, 7),
            ),
        )
        assertEquals(
            FillTransitionResult.Invalid(FillInvalidReason.CUM_QTY_OVERFLOW),
            OrderFillTransition.apply(
                current,
                observation(FillExecutionType.FILL, 7, 100, 11, 0),
            ),
        )
    }

    @Test
    fun `cancel requested 주문은 늦게 도착한 full fill로 수렴한다`() {
        val result =
            OrderFillTransition.apply(
                state(status = OrderFillStatus.CANCEL_REQUESTED),
                observation(FillExecutionType.FILL, 10, 100, 10, 0),
            ) as FillTransitionResult.Applied

        assertEquals(OrderFillStatus.FILLED, result.next.status)
        assertEquals(10, result.next.filledQuantity)
    }

    @Test
    fun `cancel requested 주문은 partial fill로 취소 의도를 지우지 않는다`() {
        val result =
            OrderFillTransition.apply(
                state(status = OrderFillStatus.CANCEL_REQUESTED),
                observation(FillExecutionType.PARTIAL_FILL, 4, 100, 4, 6),
            )

        assertTrue(result is FillTransitionResult.Invalid)
        assertEquals("CANCEL_REQUESTED_PARTIAL_FILL", (result as FillTransitionResult.Invalid).reason.name)
    }

    @Test
    fun `취소와 거부는 남은 수량을 terminated 항으로 이동한다`() {
        listOf(
            FillExecutionType.CANCELLED to OrderFillStatus.CANCELLED,
            FillExecutionType.REJECTED to OrderFillStatus.REJECTED,
        ).forEach { (execType, expectedStatus) ->
            val result =
                OrderFillTransition.apply(
                    state(),
                    observation(execType, 0, null, 0, 0),
                ) as FillTransitionResult.Applied

            assertEquals(expectedStatus, result.next.status)
            assertEquals(0, result.next.leavesQuantity)
            assertEquals(10, result.next.unfilledTerminatedQuantity)
        }
    }

    @Test
    fun `가중 평균가는 정수 내림이고 overflow는 fail closed다`() {
        assertEquals(
            100,
            OrderFillAggregation
                .addFill(
                    currentFilledQuantity = 4,
                    currentFillNotionalKrw = BigInteger.valueOf(400),
                    fillQuantity = 6,
                    fillPriceKrw = 101,
                ).averageFillPriceKrw,
        )
        assertThrows<ArithmeticException> {
            OrderFillAggregation.addFill(
                currentFilledQuantity = Long.MAX_VALUE,
                currentFillNotionalKrw = BigInteger.valueOf(Long.MAX_VALUE),
                fillQuantity = 1,
                fillPriceKrw = 1,
            )
        }
        assertThrows<IllegalArgumentException> {
            OrderFillAggregation.addFill(0, BigInteger.ZERO, 0, 100)
        }
    }

    @Test
    fun `세 번의 단위 체결은 정수 평균에서 버려진 notional 나머지를 보존한다`() {
        var current = state()
        listOf(
            observation(FillExecutionType.PARTIAL_FILL, 1, 1, 1, 9),
            observation(FillExecutionType.PARTIAL_FILL, 1, 2, 2, 8),
            observation(FillExecutionType.PARTIAL_FILL, 1, 3, 3, 7),
        ).forEach { fill ->
            current = (OrderFillTransition.apply(current, fill) as FillTransitionResult.Applied).next
        }

        assertEquals(2, current.averageFillPriceKrw)
        assertEquals(BigInteger.valueOf(6), current.fillNotionalKrw)
    }

    @Test
    fun `대사는 관측 없음 matched와 세 종류 mismatch를 구분한다`() {
        val matched =
            OrderReconciliationInput(
                quantity = 10,
                filledQuantity = 4,
                leavesQuantity = 6,
                unfilledTerminatedQuantity = 0,
                storedAverageFillPriceKrw = 100,
                observedFillQuantity = 4,
                recomputedAverageFillPriceKrw = 100,
            )
        assertEquals(
            OrderReconciliationStatus.NOT_APPLICABLE,
            OrderReconciliationPolicy.evaluate(
                matched.copy(observedFillQuantity = null, recomputedAverageFillPriceKrw = null),
            ),
        )
        assertEquals(OrderReconciliationStatus.MATCHED, OrderReconciliationPolicy.evaluate(matched))
        assertTrue(
            listOf(
                matched.copy(observedFillQuantity = 3),
                matched.copy(recomputedAverageFillPriceKrw = 101),
                matched.copy(providerFinalAverageFillPriceKrw = 101),
                matched.copy(leavesQuantity = 5),
            ).all {
                OrderReconciliationPolicy.evaluate(it) == OrderReconciliationStatus.MISMATCH
            },
        )
    }

    private fun state(
        status: OrderFillStatus = OrderFillStatus.SUBMITTED,
        filled: Long = 0,
        leaves: Long = 10,
        average: Long? = null,
    ): OrderFillState =
        OrderFillState(
            quantity = 10,
            filledQuantity = filled,
            leavesQuantity = leaves,
            unfilledTerminatedQuantity = 0,
            fillNotionalKrw =
                if (filled == 0L) {
                    BigInteger.ZERO
                } else {
                    BigInteger
                        .valueOf(requireNotNull(average))
                        .multiply(BigInteger.valueOf(filled))
                },
            averageFillPriceKrw = average,
            status = status,
        )

    private fun observation(
        execType: FillExecutionType,
        fillQuantity: Long,
        fillPrice: Long?,
        cumulative: Long,
        leaves: Long,
    ): FillObservation =
        FillObservation(
            execType = execType,
            fillQuantity = fillQuantity,
            fillPriceKrw = fillPrice,
            cumulativeQuantity = cumulative,
            leavesQuantity = leaves,
        )
}
