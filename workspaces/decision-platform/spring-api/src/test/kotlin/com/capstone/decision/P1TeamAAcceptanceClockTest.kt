package com.capstone.decision

import com.capstone.decision.infrastructure.principle.PrincipleConfiguration
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import java.time.Instant

class P1TeamAAcceptanceClockTest {
    private val configuration = PrincipleConfiguration()

    @Test
    fun `fixed clock requires the explicit offline acceptance boundary`() {
        assertThrows(IllegalArgumentException::class.java) {
            configuration.principleClock(false, "2026-08-27T06:50:00Z", true)
        }
        assertThrows(IllegalArgumentException::class.java) {
            configuration.principleClock(true, "2026-08-27T06:50:00Z", false)
        }
    }

    @Test
    fun `offline acceptance clock is exact`() {
        val expected = Instant.parse("2026-08-27T06:50:00Z")
        assertEquals(expected, configuration.principleClock(true, expected.toString(), true).instant())
    }
}
