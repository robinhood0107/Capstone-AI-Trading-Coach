package com.capstone.decision.domain.brokerage

enum class OrderReconciliationStatus {
    NOT_APPLICABLE,
    MATCHED,
    MISMATCH,
}

data class OrderReconciliationInput(
    val quantity: Long,
    val filledQuantity: Long,
    val leavesQuantity: Long,
    val unfilledTerminatedQuantity: Long,
    val storedAverageFillPriceKrw: Long?,
    val observedFillQuantity: Long?,
    val recomputedAverageFillPriceKrw: Long?,
    val providerFinalAverageFillPriceKrw: Long? = recomputedAverageFillPriceKrw,
)

/**
 * 주문·체결 projection을 고치지 않고 break 여부만 반환한다.
 * observation이 없을 때는 결측을 MATCHED로 꾸미지 않고 NOT_APPLICABLE을 유지한다.
 */
object OrderReconciliationPolicy {
    fun evaluate(input: OrderReconciliationInput): OrderReconciliationStatus {
        if (input.observedFillQuantity == null) {
            return OrderReconciliationStatus.NOT_APPLICABLE
        }
        val conservationMatches =
            try {
                Math.addExact(
                    Math.addExact(input.filledQuantity, input.leavesQuantity),
                    input.unfilledTerminatedQuantity,
                ) == input.quantity
            } catch (_: ArithmeticException) {
                false
            }
        val quantityMatches = input.observedFillQuantity == input.filledQuantity
        val averageMatches = input.recomputedAverageFillPriceKrw == input.storedAverageFillPriceKrw
        val providerAverageMatches =
            input.providerFinalAverageFillPriceKrw == input.recomputedAverageFillPriceKrw
        return if (conservationMatches && quantityMatches && averageMatches && providerAverageMatches) {
            OrderReconciliationStatus.MATCHED
        } else {
            OrderReconciliationStatus.MISMATCH
        }
    }
}
