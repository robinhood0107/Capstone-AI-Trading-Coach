package com.capstone.decision

import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

class MockKJdk25SmokeTest {
    @Test
    fun `mockk can create a mock on jdk twenty five`() {
        val pricePort = mockk<PricePort>()
        every { pricePort.currentPrice("005930") } returns 72_000

        assertEquals(72_000, pricePort.currentPrice("005930"))
        verify(exactly = 1) { pricePort.currentPrice("005930") }
    }

    private interface PricePort {
        fun currentPrice(symbol: String): Int
    }
}
