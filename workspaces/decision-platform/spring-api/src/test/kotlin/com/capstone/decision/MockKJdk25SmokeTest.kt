package com.capstone.decision

import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

// 왜: JDK 25에서 MockK bytecode agent가 최소 mock 생성까지 가능한지 PR gate로 확인한다.
class MockKJdk25SmokeTest {
    // 왜: 향후 의존성 업그레이드가 mock 생성 자체를 깨뜨리면 빠르게 분리 대응하기 위함이다.
    @Test
    fun `mockk can create a mock on jdk twenty five`() {
        val pricePort = mockk<PricePort>()
        every { pricePort.currentPrice("005930") } returns 72_000

        assertEquals(72_000, pricePort.currentPrice("005930"))
        verify(exactly = 1) { pricePort.currentPrice("005930") }
    }

    // 왜: 외부 의존성 없는 작은 interface가 MockK/JDK 호환성만 순수하게 드러낸다.
    private interface PricePort {
        fun currentPrice(symbol: String): Int
    }
}
