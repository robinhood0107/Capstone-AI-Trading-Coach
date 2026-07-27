package com.capstone.decision.domain.brokerage

/**
 * append-only paper fill 한 건을 현금·포지션 파생 상태로 투영한다.
 * BUY 이동가중평균은 정수 내림, SELL 평균단가는 불변이며 공매도와 음수 현금은 금지한다.
 */
class PaperLedgerPolicy {
    fun apply(
        state: PaperLedgerState,
        side: String,
        fillQuantity: Long,
        fillPriceKrw: Long,
    ): PaperLedgerMutation {
        validate(state, side, fillQuantity, fillPriceKrw)
        val fillAmount =
            exact {
                Math.multiplyExact(fillQuantity, fillPriceKrw)
            }
        val after =
            when (side) {
                "BUY" -> applyBuy(state, fillQuantity, fillPriceKrw, fillAmount)
                else -> applySell(state, fillQuantity, fillPriceKrw, fillAmount)
            }
        return PaperLedgerMutation(
            before = state,
            after = after,
            fillAmountKrw = fillAmount,
        )
    }

    private fun applyBuy(
        state: PaperLedgerState,
        fillQuantity: Long,
        fillPriceKrw: Long,
        fillAmount: Long,
    ): PaperLedgerState {
        if (state.cashKrw < fillAmount) {
            throw PaperLedgerException(PaperLedgerFailure.INSUFFICIENT_CASH)
        }
        return exact {
            val newQuantity = Math.addExact(state.quantity, fillQuantity)
            val existingCost = Math.multiplyExact(state.quantity, state.averagePriceKrw)
            val totalCost = Math.addExact(existingCost, fillAmount)
            PaperLedgerState(
                cashKrw = Math.subtractExact(state.cashKrw, fillAmount),
                quantity = newQuantity,
                averagePriceKrw = totalCost / newQuantity,
                marketValueKrw = Math.multiplyExact(newQuantity, fillPriceKrw),
            )
        }
    }

    private fun applySell(
        state: PaperLedgerState,
        fillQuantity: Long,
        fillPriceKrw: Long,
        fillAmount: Long,
    ): PaperLedgerState {
        if (state.quantity < fillQuantity) {
            throw PaperLedgerException(PaperLedgerFailure.INSUFFICIENT_POSITION)
        }
        return exact {
            val newQuantity = Math.subtractExact(state.quantity, fillQuantity)
            PaperLedgerState(
                cashKrw = Math.addExact(state.cashKrw, fillAmount),
                quantity = newQuantity,
                averagePriceKrw = if (newQuantity == 0L) 0 else state.averagePriceKrw,
                marketValueKrw = Math.multiplyExact(newQuantity, fillPriceKrw),
            )
        }
    }

    private fun validate(
        state: PaperLedgerState,
        side: String,
        fillQuantity: Long,
        fillPriceKrw: Long,
    ) {
        if (
            state.cashKrw < 0 ||
            state.quantity < 0 ||
            state.averagePriceKrw < 0 ||
            state.marketValueKrw < 0 ||
            (state.quantity == 0L && state.averagePriceKrw != 0L) ||
            (state.quantity > 0L && state.averagePriceKrw == 0L) ||
            side !in setOf("BUY", "SELL") ||
            fillQuantity <= 0 ||
            fillPriceKrw <= 0
        ) {
            throw PaperLedgerException(PaperLedgerFailure.INVALID_STATE)
        }
    }

    private fun <T> exact(block: () -> T): T =
        try {
            block()
        } catch (exception: ArithmeticException) {
            throw PaperLedgerException(PaperLedgerFailure.ARITHMETIC_OVERFLOW, exception)
        }
}
