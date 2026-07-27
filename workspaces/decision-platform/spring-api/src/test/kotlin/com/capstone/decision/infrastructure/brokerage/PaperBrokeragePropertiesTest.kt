package com.capstone.decision.infrastructure.brokerage

import jakarta.validation.Validation
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows

class PaperBrokeragePropertiesTest {
    @Test
    fun `slippage와 freshness 설정은 startup validation 범위 밖을 거부한다`() {
        Validation.buildDefaultValidatorFactory().use { factory ->
            val validator = factory.validator
            val invalidSlippage = validator.validate(PaperBrokerageProperties(slippageBps = 101))
            val invalidFreshness = validator.validate(PaperBrokerageProperties(priceMaxAgeSeconds = 301))

            assertEquals(setOf("slippageBps"), invalidSlippage.map { it.propertyPath.toString() }.toSet())
            assertEquals(setOf("priceMaxAgeSeconds"), invalidFreshness.map { it.propertyPath.toString() }.toSet())
            assertTrue(validator.validate(PaperBrokerageProperties()).isEmpty())
        }
        assertThrows<IllegalArgumentException> {
            PaperBrokerageProperties(slippageBps = 101).validate()
        }
    }
}
