package com.capstone.decision.domain.brokerage

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class TickSizePolicyTest {
    @Test
    fun `MARKET orders do not require a verified KRX tick table`() {
        assertEquals(TickValidation.Valid, TickSizePolicy.validate("MARKET", 70_000, null))
    }

    @Test
    fun `LIMIT orders fail closed when current tick verification is unavailable`() {
        assertEquals(TickValidation.Unavailable, TickSizePolicy.validate("LIMIT", 70_000, null))
        assertEquals(
            TickValidation.Unavailable,
            TickSizePolicy.validate(
                "LIMIT",
                70_000,
                TickTableContext(isEtfEtn = false, verification = TickTableVerification.UNVERIFIED),
            ),
        )
    }

    @Test
    fun `verified cash equity table keeps ETF ETN low price one won exception`() {
        val stock =
            TickTableContext(
                isEtfEtn = false,
                verification = TickTableVerification.KRX_CASH_EQUITY_202312_ETP_UPDATE,
            )
        val etp =
            TickTableContext(
                isEtfEtn = true,
                verification = TickTableVerification.KRX_CASH_EQUITY_202312_ETP_UPDATE,
            )

        assertEquals(1L, TickSizePolicy.tickSize(1_999, isEtfEtn = false))
        assertEquals(5L, TickSizePolicy.tickSize(2_000, isEtfEtn = false))
        assertEquals(10L, TickSizePolicy.tickSize(5_000, isEtfEtn = false))
        assertEquals(50L, TickSizePolicy.tickSize(20_000, isEtfEtn = false))
        assertEquals(100L, TickSizePolicy.tickSize(50_000, isEtfEtn = false))
        assertEquals(500L, TickSizePolicy.tickSize(200_000, isEtfEtn = false))
        assertEquals(1_000L, TickSizePolicy.tickSize(500_000, isEtfEtn = false))
        assertEquals(1L, TickSizePolicy.tickSize(1_999, isEtfEtn = true))
        assertEquals(5L, TickSizePolicy.tickSize(2_000, isEtfEtn = true))
        assertEquals(TickValidation.Valid, TickSizePolicy.validate("LIMIT", 70_000, stock))
        assertEquals(TickValidation.Valid, TickSizePolicy.validate("LIMIT", 1_999, etp))
        assertTrue(TickSizePolicy.validate("LIMIT", 70_003, stock) is TickValidation.Invalid)
    }
}
