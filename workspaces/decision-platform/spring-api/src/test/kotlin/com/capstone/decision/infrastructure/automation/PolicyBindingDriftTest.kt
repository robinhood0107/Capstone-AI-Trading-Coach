package com.capstone.decision.infrastructure.automation

import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class PolicyBindingDriftTest {
    @Test
    fun `saving a policy while disarmed never blocks the next arm`() {
        assertFalse(policyBindingDrifted("DISARMED", "auto_pol_a", 12, "auto_pol_a", 13))
        assertFalse(policyBindingDrifted("DISARMED", "auto_pol_a", 12, "auto_pol_b", 1))
    }

    @Test
    fun `an armed binding that no longer matches the current policy is drift`() {
        assertTrue(policyBindingDrifted("ARMED", "auto_pol_a", 12, "auto_pol_a", 13))
        assertTrue(policyBindingDrifted("HALTED", "auto_pol_a", 12, "auto_pol_b", 12))
        assertFalse(policyBindingDrifted("ARMED", "auto_pol_a", 13, "auto_pol_a", 13))
    }

    @Test
    fun `no control row or no binding is not drift`() {
        assertFalse(policyBindingDrifted(null, null, null, "auto_pol_a", 1))
        assertFalse(policyBindingDrifted("ARMED", null, null, "auto_pol_a", 1))
    }
}
