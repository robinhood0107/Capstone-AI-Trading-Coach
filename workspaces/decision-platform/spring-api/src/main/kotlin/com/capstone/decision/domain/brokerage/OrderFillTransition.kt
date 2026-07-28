package com.capstone.decision.domain.brokerage

import java.math.BigInteger

enum class OrderFillStatus {
    SUBMITTED,
    PENDING_RECONCILIATION,
    ACCEPTED,
    PARTIALLY_FILLED,
    FILLED,
    CANCEL_REQUESTED,
    CANCELLED,
    REJECTED,
}

enum class FillExecutionType {
    PARTIAL_FILL,
    FILL,
    CANCELLED,
    REJECTED,
}

enum class FillInvalidReason {
    NON_MONOTONIC_CUM_QTY,
    CUM_QTY_OVERFLOW,
    TERMINAL_STATE,
    INVALID_QUANTITY,
    INVALID_LEAVES_QUANTITY,
    INVALID_FILL_PRICE,
    CANCEL_REQUESTED_PARTIAL_FILL,
}

data class OrderFillState(
    val quantity: Long,
    val filledQuantity: Long,
    val leavesQuantity: Long,
    val unfilledTerminatedQuantity: Long,
    val fillNotionalKrw: BigInteger,
    val averageFillPriceKrw: Long?,
    val status: OrderFillStatus,
) {
    init {
        require(quantity > 0)
        require(filledQuantity >= 0)
        require(leavesQuantity >= 0)
        require(unfilledTerminatedQuantity >= 0)
        require(fillNotionalKrw.signum() >= 0)
        if (filledQuantity == 0L) {
            require(fillNotionalKrw == BigInteger.ZERO)
            require(averageFillPriceKrw == null)
        } else {
            require(fillNotionalKrw.signum() > 0)
            requireNotNull(averageFillPriceKrw)
            require(averageFillPriceKrw > 0)
            require(
                fillNotionalKrw.divide(BigInteger.valueOf(filledQuantity)) ==
                    BigInteger.valueOf(averageFillPriceKrw),
            )
        }
    }
}

data class FillObservation(
    val execType: FillExecutionType,
    val fillQuantity: Long,
    val fillPriceKrw: Long?,
    val cumulativeQuantity: Long,
    val leavesQuantity: Long,
)

sealed interface FillTransitionResult {
    data class Applied(
        val next: OrderFillState,
    ) : FillTransitionResult

    data object Duplicate : FillTransitionResult

    data class Invalid(
        val reason: FillInvalidReason,
    ) : FillTransitionResult
}

/**
 * 저장된 sanitized observation 하나를 주문 aggregate에 적용할 수 있는지 순수하게 판정한다.
 * DB 함수는 같은 전이표를 2차 방어로 검증하며 결과가 다르면 transaction을 실패시킨다.
 */
object OrderFillTransition {
    fun apply(
        current: OrderFillState,
        observation: FillObservation,
    ): FillTransitionResult {
        if (current.status in TERMINAL_STATUSES) {
            return FillTransitionResult.Invalid(FillInvalidReason.TERMINAL_STATE)
        }
        if (observation.cumulativeQuantity < current.filledQuantity) {
            return FillTransitionResult.Invalid(FillInvalidReason.NON_MONOTONIC_CUM_QTY)
        }
        if (observation.cumulativeQuantity > current.quantity) {
            return FillTransitionResult.Invalid(FillInvalidReason.CUM_QTY_OVERFLOW)
        }
        if (observation.fillQuantity < 0) {
            return FillTransitionResult.Invalid(FillInvalidReason.INVALID_QUANTITY)
        }
        if (observation.leavesQuantity < 0) {
            return FillTransitionResult.Invalid(FillInvalidReason.INVALID_LEAVES_QUANTITY)
        }
        if (
            current.status == OrderFillStatus.CANCEL_REQUESTED &&
            observation.execType == FillExecutionType.PARTIAL_FILL
        ) {
            return FillTransitionResult.Invalid(FillInvalidReason.CANCEL_REQUESTED_PARTIAL_FILL)
        }

        return when (observation.execType) {
            FillExecutionType.PARTIAL_FILL,
            FillExecutionType.FILL,
            -> applyFill(current, observation)

            FillExecutionType.CANCELLED ->
                terminate(
                    current = current,
                    observation = observation,
                    nextStatus = OrderFillStatus.CANCELLED,
                )

            FillExecutionType.REJECTED ->
                terminate(
                    current = current,
                    observation = observation,
                    nextStatus = OrderFillStatus.REJECTED,
                )
        }
    }

    private fun applyFill(
        current: OrderFillState,
        observation: FillObservation,
    ): FillTransitionResult {
        if (observation.cumulativeQuantity == current.filledQuantity) {
            return FillTransitionResult.Duplicate
        }
        if (observation.fillQuantity <= 0 || observation.fillPriceKrw == null || observation.fillPriceKrw <= 0) {
            return FillTransitionResult.Invalid(
                if (observation.fillQuantity <= 0) {
                    FillInvalidReason.INVALID_QUANTITY
                } else {
                    FillInvalidReason.INVALID_FILL_PRICE
                },
            )
        }
        val delta = observation.cumulativeQuantity - current.filledQuantity
        if (observation.fillQuantity != delta) {
            return FillTransitionResult.Invalid(FillInvalidReason.INVALID_QUANTITY)
        }
        val expectedLeaves = current.quantity - observation.cumulativeQuantity
        if (observation.leavesQuantity != expectedLeaves) {
            return FillTransitionResult.Invalid(FillInvalidReason.INVALID_LEAVES_QUANTITY)
        }
        if (observation.execType == FillExecutionType.PARTIAL_FILL && expectedLeaves == 0L) {
            return FillTransitionResult.Invalid(FillInvalidReason.INVALID_LEAVES_QUANTITY)
        }
        if (observation.execType == FillExecutionType.FILL && expectedLeaves != 0L) {
            return FillTransitionResult.Invalid(FillInvalidReason.INVALID_LEAVES_QUANTITY)
        }
        val aggregation =
            OrderFillAggregation.addFill(
                currentFilledQuantity = current.filledQuantity,
                currentFillNotionalKrw = current.fillNotionalKrw,
                fillQuantity = delta,
                fillPriceKrw = observation.fillPriceKrw,
            )
        return FillTransitionResult.Applied(
            OrderFillState(
                quantity = current.quantity,
                filledQuantity = observation.cumulativeQuantity,
                leavesQuantity = expectedLeaves,
                unfilledTerminatedQuantity = 0,
                fillNotionalKrw = aggregation.fillNotionalKrw,
                averageFillPriceKrw = aggregation.averageFillPriceKrw,
                status =
                    if (expectedLeaves == 0L) {
                        OrderFillStatus.FILLED
                    } else {
                        OrderFillStatus.PARTIALLY_FILLED
                    },
            ),
        )
    }

    private fun terminate(
        current: OrderFillState,
        observation: FillObservation,
        nextStatus: OrderFillStatus,
    ): FillTransitionResult {
        // 취소·거부는 새 체결이 없어 누적수량이 같을 수 있으므로 fill 계열의 duplicate 규칙과 분리한다.
        if (
            observation.fillQuantity != 0L ||
            observation.fillPriceKrw != null ||
            observation.cumulativeQuantity != current.filledQuantity ||
            observation.leavesQuantity != 0L
        ) {
            return FillTransitionResult.Invalid(FillInvalidReason.INVALID_QUANTITY)
        }
        return FillTransitionResult.Applied(
            current.copy(
                leavesQuantity = 0,
                unfilledTerminatedQuantity = current.quantity - current.filledQuantity,
                status = nextStatus,
            ),
        )
    }

    private val TERMINAL_STATUSES =
        setOf(
            OrderFillStatus.FILLED,
            OrderFillStatus.CANCELLED,
            OrderFillStatus.REJECTED,
        )
}
