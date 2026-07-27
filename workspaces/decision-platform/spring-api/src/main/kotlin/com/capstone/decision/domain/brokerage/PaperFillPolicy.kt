package com.capstone.decision.domain.brokerage

/**
 * 저장된 현재가 또는 직전 종가만 사용해 S3.2 전량 가상 체결을 결정한다.
 * 부동소수점과 provider fallback은 사용하지 않으며 모든 원화 산술은 exact 정수 연산이다.
 */
class PaperFillPolicy(
    private val slippageBps: Int,
) {
    init {
        require(slippageBps in 0..MAX_SLIPPAGE_BPS)
    }

    fun decide(
        request: PaperFillRequest,
        quote: PaperPriceObservation,
    ): PaperFillDecision {
        validate(request)
        if (quote.completeness != "COMPLETE") {
            throw PaperFillPolicyException(PaperFillFailure.PRICE_UNAVAILABLE)
        }
        val (basePrice, basis) =
            when {
                quote.lastPriceKrw != null && quote.lastPriceKrw > 0 ->
                    quote.lastPriceKrw to PaperPriceBasis.LAST_QUOTE
                quote.previousCloseKrw != null && quote.previousCloseKrw > 0 ->
                    quote.previousCloseKrw to PaperPriceBasis.PREVIOUS_CLOSE
                else -> throw PaperFillPolicyException(PaperFillFailure.PRICE_UNAVAILABLE)
            }
        if (request.orderType == "LIMIT" && !limitCanFill(request, basePrice)) {
            return PaperFillDecision.Accepted(PaperAcceptedReason.LIMIT_NOT_FILLED)
        }
        val effectiveSlippage = if (request.orderType == "MARKET") slippageBps else 0
        val fillPrice =
            try {
                when {
                    request.orderType == "LIMIT" && request.side == "BUY" ->
                        minOf(basePrice, requireNotNull(request.limitPriceKrw))
                    request.orderType == "LIMIT" ->
                        maxOf(basePrice, requireNotNull(request.limitPriceKrw))
                    request.side == "BUY" ->
                        ceilRatio(
                            Math.multiplyExact(basePrice, BPS_DENOMINATOR + effectiveSlippage),
                            BPS_DENOMINATOR,
                        )
                    else ->
                        Math.multiplyExact(basePrice, BPS_DENOMINATOR - effectiveSlippage) / BPS_DENOMINATOR
                }
            } catch (exception: ArithmeticException) {
                throw PaperFillPolicyException(PaperFillFailure.ARITHMETIC_OVERFLOW, exception)
            }
        if (fillPrice <= 0) {
            throw PaperFillPolicyException(PaperFillFailure.PRICE_UNAVAILABLE)
        }
        val amount =
            try {
                Math.multiplyExact(request.quantity, fillPrice)
            } catch (exception: ArithmeticException) {
                throw PaperFillPolicyException(PaperFillFailure.ARITHMETIC_OVERFLOW, exception)
            }
        return PaperFillDecision.Filled(
            quantity = request.quantity,
            priceKrw = fillPrice,
            amountKrw = amount,
            priceBasis = basis,
            slippageBps = effectiveSlippage,
            feeModel = PaperFeeModel.NONE_V1,
            observedAt = quote.observedAt,
        )
    }

    private fun validate(request: PaperFillRequest) {
        if (
            request.side !in setOf("BUY", "SELL") ||
            request.orderType !in setOf("MARKET", "LIMIT") ||
            request.quantity <= 0 ||
            (
                request.orderType == "LIMIT" &&
                    (request.limitPriceKrw == null || request.limitPriceKrw <= 0)
            ) ||
            (request.orderType == "MARKET" && request.limitPriceKrw != null)
        ) {
            throw PaperFillPolicyException(PaperFillFailure.INVALID_INPUT)
        }
    }

    private fun limitCanFill(
        request: PaperFillRequest,
        basePrice: Long,
    ): Boolean =
        if (request.side == "BUY") {
            basePrice <= requireNotNull(request.limitPriceKrw)
        } else {
            basePrice >= requireNotNull(request.limitPriceKrw)
        }

    private fun ceilRatio(
        numerator: Long,
        denominator: Long,
    ): Long =
        try {
            Math.addExact(numerator, denominator - 1) / denominator
        } catch (exception: ArithmeticException) {
            throw PaperFillPolicyException(PaperFillFailure.ARITHMETIC_OVERFLOW, exception)
        }

    private companion object {
        const val BPS_DENOMINATOR = 10_000L
        const val MAX_SLIPPAGE_BPS = 100
    }
}
