package com.capstone.decision.infrastructure.grpc

import jakarta.validation.Validation
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class BrokerageGrpcPropertiesTest {
    @Test
    fun `default deadline covers bounded mock limiter and transport waits`() {
        val properties = BrokerageGrpcProperties()

        assertEquals(90_000, properties.deadlineMillis)
        val validator = Validation.buildDefaultValidatorFactory().validator
        assertTrue(validator.validate(properties).isEmpty())
    }

    @Test
    fun `deadline above bounded online envelope is rejected`() {
        val properties =
            BrokerageGrpcProperties(
                target = "127.0.0.1:50052",
                sharedSecret = "s".repeat(32),
                deadlineMillis = 90_001,
            )

        assertThrows(IllegalArgumentException::class.java) {
            properties.validate()
        }
        val validator = Validation.buildDefaultValidatorFactory().validator
        assertFalse(validator.validate(properties).isEmpty())
    }
}
