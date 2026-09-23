package com.capstone.decision.infrastructure.vertex

import io.mockk.Runs
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.ObjectProvider

class S49FullAgentGrossBudgetTest {
    @Test
    fun `full agent reserves model and grounding exposure for the authenticated owner`() {
        val policy = mockk<OperatorAiBudgetPolicyService>()
        val provider = mockk<ObjectProvider<OperatorAiBudgetPolicyService>>()
        val reservationId = slot<String>()
        val gross = slot<Long>()
        every { provider.getIfAvailable() } returns policy
        every {
            policy.reserveGrossUsage(capture(reservationId), "usr_alice", "FULL_AGENT", "VERTEX", capture(gross))
        } just Runs
        val gate = gate("FULL", provider)

        gate.reserve("usr_alice", "s49_run_${"a".repeat(32)}", "call_1", 1_000, 0, 0, true)

        verify(exactly = 1) { policy.reserveGrossUsage(any(), "usr_alice", "FULL_AGENT", "VERTEX", any()) }
        assertTrue(reservationId.captured.matches(Regex("^aibr_[0-9a-f]{32}$")))
        assertEquals((1_000L + 8_192L) * 3L + 4_096L * 17L + 8L * 14_000L, gross.captured)
        assertTrue(
            quoteMaxGrossMicrousd(1_000, 0, 1, 4_096, 3, 17, 0, 14_000) >
                quoteMaxGrossMicrousd(1_000, 0, 0, 4_096, 3, 17, 0, 14_000),
        )
    }

    @Test
    fun `missing budget and demo mode refuse an owner-bound Agent reservation`() {
        val provider = mockk<ObjectProvider<OperatorAiBudgetPolicyService>>()
        every { provider.getIfAvailable() } returns null
        assertThrows(IllegalStateException::class.java) {
            gate("FULL", provider).reserve("usr_alice", "run_a", "call_1", 1_000, 0, 0, false)
        }
        assertThrows(IllegalStateException::class.java) {
            gate("DEMO", provider).reserve("usr_alice", "run_a", "call_1", 1_000, 0, 0, false)
        }
    }

    @Test
    fun `public rate assumptions below the current list price fail closed`() {
        val provider = mockk<ObjectProvider<OperatorAiBudgetPolicyService>>()
        assertThrows(IllegalStateException::class.java) {
            gate("FULL", provider, inputRate = "2")
        }
        assertThrows(IllegalStateException::class.java) {
            gate("FULL", provider, groundingRate = "13999")
        }
    }

    private fun gate(
        mode: String,
        provider: ObjectProvider<OperatorAiBudgetPolicyService>,
        inputRate: String = "3",
        groundingRate: String = "14000",
    ): S49FullAgentGrossBudget =
        S49FullAgentGrossBudget(
            mode,
            inputRate,
            "17",
            groundingRate,
            provider,
            S49StrongLlmProperties(maxOutputTokens = 4_096),
            S49GoogleGroundingProperties(reservePerPrompt = 8),
        )
}
