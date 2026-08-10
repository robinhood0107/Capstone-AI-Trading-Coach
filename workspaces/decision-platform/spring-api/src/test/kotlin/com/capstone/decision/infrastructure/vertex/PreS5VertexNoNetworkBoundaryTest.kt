package com.capstone.decision.infrastructure.vertex

import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import java.net.URI
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.ZoneOffset

class PreS5VertexNoNetworkBoundaryTest {
    @Test
    fun `expired generation packet fails before a provider socket and clears evidence body plus API key`() {
        val now = Instant.parse("2026-08-03T12:00:00Z")
        val body = "{\"contents\":[]}".toByteArray()
        val apiKey = "AIzaSyVertexOnlyKey_1234567890".toByteArray()

        assertThatThrownBy {
            JdkPreS5VertexHttpExecutor(Clock.fixed(now, ZoneOffset.UTC)).execute(
                PreS5VertexHttpRequest(
                    endpoint =
                        URI.create(
                            "https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-3.5-flash:generateContent",
                        ),
                    apiKey = apiKey,
                    body = body,
                    timeout = Duration.ofSeconds(1),
                    expiresAt = now,
                    attempt = generateContentAttempt(now),
                ),
            )
        }.isInstanceOf(PreS5VertexTransportException::class.java)
        assertThat(body.all { it == 0.toByte() }).isTrue()
        assertThat(apiKey.all { it == 0.toByte() }).isTrue()
    }

    private fun generateContentAttempt(expiresAt: Instant) = PreS5VertexGenerateContentAttempt(lease(expiresAt))

    private fun lease(expiresAt: Instant): PreS5VertexUsageLease =
        PreS5VertexUsageLease(
            usageEventId = "rgr_vgu_${"a".repeat(32)}",
            ownerUserId = "usr_demo_user",
            expiresAt = expiresAt,
        )
}
