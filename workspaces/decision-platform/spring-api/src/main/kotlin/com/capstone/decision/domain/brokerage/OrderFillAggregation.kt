package com.capstone.decision.domain.brokerage

import java.math.BigInteger

data class ExactFillAggregation(
    val fillNotionalKrw: BigInteger,
    val averageFillPriceKrw: Long,
)

/**
 * provider 평균가를 신뢰하지 않고 적용된 fill delta의 정확한 notional을 누적해 정수 KRW 평균을 계산한다.
 * 수량과 최종 평균의 Long 경계는 fail-closed하고, 중간 notional은 BigInteger로 나머지를 보존한다.
 */
object OrderFillAggregation {
    fun addFill(
        currentFilledQuantity: Long,
        currentFillNotionalKrw: BigInteger,
        fillQuantity: Long,
        fillPriceKrw: Long,
    ): ExactFillAggregation {
        require(currentFilledQuantity >= 0)
        require(fillQuantity > 0)
        require(fillPriceKrw > 0)
        require(currentFillNotionalKrw.signum() >= 0)
        require((currentFilledQuantity == 0L) == (currentFillNotionalKrw.signum() == 0))

        val totalQuantity = Math.addExact(currentFilledQuantity, fillQuantity)
        val fillNotional =
            BigInteger
                .valueOf(fillQuantity)
                .multiply(BigInteger.valueOf(fillPriceKrw))
        val totalNotional = currentFillNotionalKrw.add(fillNotional)
        val average =
            totalNotional
                .divide(BigInteger.valueOf(totalQuantity))
                .longValueExact()
        return ExactFillAggregation(
            fillNotionalKrw = totalNotional,
            averageFillPriceKrw = average,
        )
    }
}
