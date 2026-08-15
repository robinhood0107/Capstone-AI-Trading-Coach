package com.capstone.decision.infrastructure.vertex

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import java.time.Clock
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset

class S49GoogleGroundingBudgetTest {
    @Test
    fun `Pacific billing month changes at Los Angeles midnight rather than UTC month`() {
        val beforePacificMonth = Clock.fixed(Instant.parse("2026-08-01T06:59:59Z"), ZoneOffset.UTC)
        val afterPacificMonth = Clock.fixed(Instant.parse("2026-08-01T07:00:00Z"), ZoneOffset.UTC)

        assertEquals(LocalDate.parse("2026-07-01"), s49GoogleBillingPeriodStart(beforePacificMonth, "America/Los_Angeles"))
        assertEquals(LocalDate.parse("2026-08-01"), s49GoogleBillingPeriodStart(afterPacificMonth, "America/Los_Angeles"))
    }

    @Test
    fun `Google grounding configuration rejects overage and caps outside contract`() {
        assertThrows<IllegalArgumentException> {
            S49GoogleGroundingProperties(
                overageAllowed = true,
                billingAccountFingerprint = "a".repeat(64),
            ).validate()
        }
        assertThrows<IllegalArgumentException> {
            S49GoogleGroundingProperties(
                monthlySoftCap = 5_001,
                billingAccountFingerprint = "a".repeat(64),
            ).validate()
        }
        assertThrows<IllegalArgumentException> {
            S49GoogleGroundingProperties(
                reservePerPrompt = 9,
                billingAccountFingerprint = "a".repeat(64),
            ).validate()
        }
    }
}
