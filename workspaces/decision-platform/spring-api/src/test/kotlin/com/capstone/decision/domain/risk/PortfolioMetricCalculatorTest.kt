package com.capstone.decision.domain.risk

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.math.BigDecimal

class PortfolioMetricCalculatorTest {
    private val calculator = PortfolioMetricCalculator()

    @Test
    fun `order amount and post-order owner weights use exact arithmetic`() {
        val portfolio =
            PortfolioValues(
                equityKrw = 1_000_000,
                positions =
                    listOf(
                        PositionValue("005930", 100_000, goldEtfEtn = false),
                        PositionValue("132030", 200_000, goldEtfEtn = true),
                    ),
            )
        val amount = calculator.orderAmountKrw(50_000, 2)

        assertEquals(100_000, amount)
        assertEquals(
            BigDecimal("0.2"),
            calculator.postOrderAssetWeight(portfolio, "005930", "BUY", amount).asBigDecimal(),
        )
        assertEquals(
            BigDecimal("0.3"),
            calculator.postOrderGoldWeight(portfolio, true, "BUY", amount).asBigDecimal(),
        )
        assertEquals(
            BigDecimal("0.2"),
            calculator.postOrderGoldWeight(portfolio, false, "BUY", amount).asBigDecimal(),
        )
    }

    @Test
    fun `overflow non-positive denominator and oversell fail before evaluation`() {
        assertThrows(ArithmeticException::class.java) {
            calculator.orderAmountKrw(Long.MAX_VALUE, 2)
        }
        assertThrows(IllegalArgumentException::class.java) {
            PortfolioValues(0, emptyList())
        }
        assertThrows(IllegalArgumentException::class.java) {
            calculator.postOrderAssetWeight(
                PortfolioValues(1_000_000, listOf(PositionValue("005930", 10_000, false))),
                "005930",
                "SELL",
                10_001,
            )
        }
    }

    @Test
    fun `sub-scale excess remains a violation instead of rounding down to threshold`() {
        val ratio =
            calculator.postOrderAssetWeight(
                portfolio =
                    PortfolioValues(
                        equityKrw = 1_000_000,
                        positions = listOf(PositionValue("005930", 200_001, false)),
                    ),
                symbol = "005930",
                side = "BUY",
                orderAmountKrw = 0,
            )

        assertTrue(ratio.compareTo(BigDecimal("0.2000")) > 0)
        assertEquals(BigDecimal("0.200001"), ratio.asBigDecimal())
    }

    @Test
    fun `repeating portfolio ratio compares to scale-four threshold exactly`() {
        val ratio =
            calculator.postOrderAssetWeight(
                portfolio =
                    PortfolioValues(
                        equityKrw = 3,
                        positions = listOf(PositionValue("005930", 1, false)),
                    ),
                symbol = "005930",
                side = "BUY",
                orderAmountKrw = 0,
            )

        assertTrue(ratio.compareTo(BigDecimal("0.3333")) > 0)
    }
}
