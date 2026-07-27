package com.capstone.decision.domain.brokerage

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows

class PaperLedgerPolicyTest {
    private val policy = PaperLedgerPolicy()

    @Test
    fun `BUY는 현금을 차감하고 이동가중평균을 정수 내림한다`() {
        val result =
            policy.apply(
                state =
                    PaperLedgerState(
                        cashKrw = 100_000,
                        quantity = 1,
                        averagePriceKrw = 10_000,
                        marketValueKrw = 10_000,
                    ),
                side = "BUY",
                fillQuantity = 1,
                fillPriceKrw = 10_001,
            )

        assertEquals(89_999, result.after.cashKrw)
        assertEquals(2, result.after.quantity)
        assertEquals(10_000, result.after.averagePriceKrw)
        assertEquals(20_002, result.after.marketValueKrw)
        assertEquals(10_001, result.fillAmountKrw)
    }

    @Test
    fun `SELL은 평균단가를 유지하고 전량 매도 때 0으로 reset한다`() {
        val partial =
            policy.apply(
                state = PaperLedgerState(0, 3, 9_999, 30_000),
                side = "SELL",
                fillQuantity = 2,
                fillPriceKrw = 11_000,
            )
        val closed =
            policy.apply(
                state = partial.after,
                side = "SELL",
                fillQuantity = 1,
                fillPriceKrw = 12_000,
            )

        assertEquals(PaperLedgerState(22_000, 1, 9_999, 11_000), partial.after)
        assertEquals(PaperLedgerState(34_000, 0, 0, 0), closed.after)
    }

    @Test
    fun `현금 부족과 보유 초과 매도는 mutation 없이 거부한다`() {
        val cash =
            assertThrows<PaperLedgerException> {
                policy.apply(PaperLedgerState(9_999, 0, 0, 0), "BUY", 1, 10_000)
            }
        val holdings =
            assertThrows<PaperLedgerException> {
                policy.apply(PaperLedgerState(0, 1, 10_000, 10_000), "SELL", 2, 10_000)
            }

        assertEquals(PaperLedgerFailure.INSUFFICIENT_CASH, cash.failure)
        assertEquals(PaperLedgerFailure.INSUFFICIENT_POSITION, holdings.failure)
    }

    @Test
    fun `잘못된 state와 Long overflow는 fail closed한다`() {
        assertEquals(
            PaperLedgerFailure.INVALID_STATE,
            assertThrows<PaperLedgerException> {
                policy.apply(PaperLedgerState(-1, 0, 0, 0), "BUY", 1, 1)
            }.failure,
        )
        assertEquals(
            PaperLedgerFailure.ARITHMETIC_OVERFLOW,
            assertThrows<PaperLedgerException> {
                policy.apply(PaperLedgerState(Long.MAX_VALUE, 1, 1, 1), "SELL", 1, 1)
            }.failure,
        )
        assertEquals(
            PaperLedgerFailure.ARITHMETIC_OVERFLOW,
            assertThrows<PaperLedgerException> {
                policy.apply(PaperLedgerState(Long.MAX_VALUE, 1, Long.MAX_VALUE, 1), "BUY", 1, 1)
            }.failure,
        )
    }
}
