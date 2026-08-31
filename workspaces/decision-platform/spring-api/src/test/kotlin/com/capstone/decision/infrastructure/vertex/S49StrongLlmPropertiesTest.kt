package com.capstone.decision.infrastructure.vertex

import org.assertj.core.api.Assertions.assertThatCode
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test

class S49StrongLlmPropertiesTest {
    @Test
    fun `public automation provider does not require owner consent hashes at startup`() {
        val properties =
            S49StrongLlmProperties(
                enabled = true,
                modelId = "gemini-3.5-flash",
                localRoot = "/tmp/strong-llm",
            )

        assertThatCode(properties::validateEnabled).doesNotThrowAnyException()
    }

    @Test
    fun `configured owner consent hashes remain exact sha256 values`() {
        val properties =
            S49StrongLlmProperties(
                enabled = true,
                modelId = "gemini-3.5-flash",
                localRoot = "/tmp/strong-llm",
                ownerConsentPolicySha256 = "invalid",
            )

        assertThatThrownBy(properties::validateEnabled).isInstanceOf(IllegalArgumentException::class.java)
    }
}
