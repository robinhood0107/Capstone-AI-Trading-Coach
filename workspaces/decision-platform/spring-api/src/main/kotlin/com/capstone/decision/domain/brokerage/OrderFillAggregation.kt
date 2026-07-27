package com.capstone.decision.domain.brokerage

/**
 * provider 평균가를 신뢰하지 않고 fill delta만으로 정수 KRW 가중평균을 재계산한다.
 * Long 범위를 넘는 입력은 wraparound하지 않고 ArithmeticException으로 fail-closed한다.
 */
object OrderFillAggregation {
    fun nextAveragePrice(
        currentFilledQuantity: Long,
        currentAverageFillPriceKrw: Long?,
        fillQuantity: Long,
        fillPriceKrw: Long,
    ): Long {
        require(currentFilledQuantity >= 0)
        require(fillQuantity > 0)
        require(fillPriceKrw > 0)
        require((currentFilledQuantity == 0L) == (currentAverageFillPriceKrw == null))

        val currentNotional =
            if (currentFilledQuantity == 0L) {
                0L
            } else {
                Math.multiplyExact(
                    currentFilledQuantity,
                    requireNotNull(currentAverageFillPriceKrw),
                )
            }
        val fillNotional = Math.multiplyExact(fillQuantity, fillPriceKrw)
        val totalNotional = Math.addExact(currentNotional, fillNotional)
        val totalQuantity = Math.addExact(currentFilledQuantity, fillQuantity)
        return totalNotional / totalQuantity
    }
}
