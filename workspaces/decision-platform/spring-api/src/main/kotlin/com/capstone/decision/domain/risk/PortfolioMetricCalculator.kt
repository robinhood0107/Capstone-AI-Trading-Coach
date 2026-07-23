package com.capstone.decision.domain.risk

data class PositionValue(
    val symbol: String,
    val marketValueKrw: Long,
    val goldEtfEtn: Boolean,
) {
    init {
        require(symbol.isNotBlank())
        require(marketValueKrw >= 0)
    }
}

data class PortfolioValues(
    val equityKrw: Long,
    val positions: List<PositionValue>,
) {
    init {
        require(equityKrw > 0) { "Portfolio denominator must be positive." }
        require(positions.size <= EvaluationBounds.MAX_POSITIONS) { "Portfolio position limit exceeded." }
    }
}

// post-order 비중은 owner-scoped complete portfolio만 받아 exact Long 산술 후 scale-4 ratio로 계산한다.
class PortfolioMetricCalculator {
    fun orderAmountKrw(
        unitPriceKrw: Long,
        quantity: Long,
    ): Long {
        require(unitPriceKrw > 0 && quantity > 0)
        return Math.multiplyExact(unitPriceKrw, quantity)
    }

    fun postOrderAssetWeight(
        portfolio: PortfolioValues,
        symbol: String,
        side: String,
        orderAmountKrw: Long,
    ): MetricValue.RatioFraction {
        val current =
            portfolio.positions
                .filter { it.symbol == symbol }
                .fold(0L) { total, position -> Math.addExact(total, position.marketValueKrw) }
        return ratio(adjust(current, side, orderAmountKrw), portfolio.equityKrw)
    }

    fun postOrderGoldWeight(
        portfolio: PortfolioValues,
        orderSymbolIsGoldEtfEtn: Boolean,
        side: String,
        orderAmountKrw: Long,
    ): MetricValue.RatioFraction {
        val current =
            portfolio.positions
                .filter(PositionValue::goldEtfEtn)
                .fold(0L) { total, position -> Math.addExact(total, position.marketValueKrw) }
        val postOrder =
            if (orderSymbolIsGoldEtfEtn) {
                adjust(current, side, orderAmountKrw)
            } else {
                current
            }
        return ratio(postOrder, portfolio.equityKrw)
    }

    private fun adjust(
        current: Long,
        side: String,
        orderAmountKrw: Long,
    ): Long {
        require(orderAmountKrw >= 0)
        val adjusted =
            when (side) {
                "BUY" -> Math.addExact(current, orderAmountKrw)
                "SELL" -> Math.subtractExact(current, orderAmountKrw)
                else -> throw IllegalArgumentException("Unsupported order side.")
            }
        require(adjusted >= 0) { "Post-order position cannot be negative." }
        return adjusted
    }

    private fun ratio(
        numerator: Long,
        denominator: Long,
    ): MetricValue.RatioFraction =
        MetricValue.RatioFraction(
            numerator = numerator,
            denominator = denominator,
            declaredScale = 4,
        )
}
