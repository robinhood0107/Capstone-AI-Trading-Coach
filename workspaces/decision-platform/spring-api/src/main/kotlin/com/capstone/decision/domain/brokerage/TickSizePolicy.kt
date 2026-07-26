package com.capstone.decision.domain.brokerage

/**
 * 주문 제출 전 서버가 적용하는 호가단위 정책이다.
 * KRX 조문을 현재 세션에서 기계적으로 추출하지 못하면 LIMIT 주문은 열지 않고 MARKET만 통과시킨다.
 */
object TickSizePolicy {
    fun validate(
        orderType: String,
        priceKrw: Long,
        context: TickTableContext?,
    ): TickValidation =
        when {
            orderType == "MARKET" -> TickValidation.Valid
            orderType != "LIMIT" -> TickValidation.Invalid("UNSUPPORTED_ORDER_TYPE")
            context?.verification != TickTableVerification.KRX_CASH_EQUITY_202312_ETP_UPDATE ->
                TickValidation.Unavailable
            priceKrw % tickSize(priceKrw, context.isEtfEtn) == 0L -> TickValidation.Valid
            else -> TickValidation.Invalid("INVALID_TICK_SIZE")
        }

    fun tickSize(
        priceKrw: Long,
        isEtfEtn: Boolean,
    ): Long {
        require(priceKrw > 0)
        if (isEtfEtn) {
            // 2023년 말 ETF/ETN 2,000원 미만 구간은 5원 고정에서 1원으로 축소되었다.
            return if (priceKrw < 2_000L) 1L else 5L
        }
        return when {
            priceKrw < 2_000L -> 1L
            priceKrw < 5_000L -> 5L
            priceKrw < 20_000L -> 10L
            priceKrw < 50_000L -> 50L
            priceKrw < 200_000L -> 100L
            priceKrw < 500_000L -> 500L
            else -> 1_000L
        }
    }
}

data class TickTableContext(
    val isEtfEtn: Boolean,
    val verification: TickTableVerification,
)

enum class TickTableVerification {
    UNVERIFIED,
    KRX_CASH_EQUITY_202312_ETP_UPDATE,
}

sealed interface TickValidation {
    data object Valid : TickValidation

    data object Unavailable : TickValidation

    data class Invalid(
        val reason: String,
    ) : TickValidation
}
