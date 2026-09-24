package com.capstone.decision.infrastructure.vertex

import io.mockk.Runs
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import org.junit.jupiter.api.Assertions.assertDoesNotThrow
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.ObjectProvider

class S49PublicAgentUsageMeterTest {
    @Test
    fun `full agent records estimated provider use for the authenticated owner`() {
        val meter = mockk<OperatorAiUsageMeter>()
        val provider = provider(meter)
        val reservationId = slot<String>()
        val estimate = slot<Long>()
        every {
            meter.recordGrossEstimate(capture(reservationId), "usr_alice", "FULL_AGENT", "VERTEX", capture(estimate))
        } just Runs

        gate("FULL", provider).record("usr_alice", "s49_run_${"a".repeat(32)}", "call_1", 1_000, 0, 0, true)

        verify(exactly = 1) {
            meter.recordGrossEstimate(any(), "usr_alice", "FULL_AGENT", "VERTEX", any())
        }
        assertTrue(reservationId.captured.matches(Regex("^aibr_[0-9a-f]{32}$")))
        assertEquals((1_000L + 8_192L) * 3L + 4_096L * 17L + 8L * 14_000L, estimate.captured)
        assertTrue(
            quoteMaxGrossMicrousd(1_000, 0, 1, 4_096, 3, 17, 0, 14_000) >
                quoteMaxGrossMicrousd(1_000, 0, 0, 4_096, 3, 17, 0, 14_000),
        )
    }

    @Test
    fun `missing or failing meter never blocks a provider call`() {
        assertDoesNotThrow {
            gate("FULL", provider(null)).record("usr_alice", "run_a", "call_1", 1_000, 0, 0, false)
        }

        val meter = mockk<OperatorAiUsageMeter>()
        val provider = provider(meter)
        every { meter.recordGrossEstimate(any(), any(), any(), any(), any()) } throws IllegalStateException("db down")
        assertDoesNotThrow {
            gate("FULL", provider).record("usr_alice", "run_b", "call_2", 1_000, 0, 0, false)
        }
    }

    @Test
    fun `demo records anonymously and still rejects Google Search`() {
        val meter = mockk<OperatorAiUsageMeter>()
        val provider = provider(meter)
        every { meter.recordGrossEstimate(any(), null, "DEMO_AGENT", "VERTEX", any()) } just Runs
        val gate = gate("DEMO", provider)

        assertDoesNotThrow { gate.record("usr_internal_demo", "run_demo", "call_1", 1_000, 0, 0, false) }
        verify(exactly = 1) { meter.recordGrossEstimate(any(), null, "DEMO_AGENT", "VERTEX", any()) }
        assertThrows(IllegalStateException::class.java) {
            gate.record("usr_internal_demo", "run_demo", "call_2", 1_000, 0, 1, true)
        }
    }

    @Test
    fun `invalid price assumptions only skip an estimate`() {
        val meter = mockk<OperatorAiUsageMeter>()
        val gate =
            S49PublicAgentUsageMeter(
                "FULL",
                "invalid",
                "17",
                "13999",
                provider(meter),
                S49StrongLlmProperties(maxOutputTokens = 4_096),
                S49GoogleGroundingProperties(reservePerPrompt = 8),
            )

        assertDoesNotThrow { gate.record("usr_alice", "run_a", "call_1", 1_000, 0, 0, false) }
        verify(exactly = 1) { meter.recordGrossEstimate(any(), "usr_alice", "FULL_AGENT", "VERTEX", any()) }
    }

    private fun provider(meter: OperatorAiUsageMeter?): ObjectProvider<OperatorAiUsageMeter> =
        mockk<ObjectProvider<OperatorAiUsageMeter>> {
            every { getIfAvailable() } returns meter
        }

    private fun gate(
        mode: String,
        provider: ObjectProvider<OperatorAiUsageMeter>,
    ): S49PublicAgentUsageMeter =
        S49PublicAgentUsageMeter(
            mode,
            "3",
            "17",
            "14000",
            provider,
            S49StrongLlmProperties(maxOutputTokens = 4_096),
            S49GoogleGroundingProperties(reservePerPrompt = 8),
        )
}
