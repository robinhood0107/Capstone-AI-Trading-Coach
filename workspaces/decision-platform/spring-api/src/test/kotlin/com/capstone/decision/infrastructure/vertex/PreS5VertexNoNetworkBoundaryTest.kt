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
    fun `expired OAuth packet fails before a token socket and clears the signed assertion`() {
        val now = Instant.parse("2026-08-03T12:00:00Z")
        val assertion = "header.payload.signature".toByteArray()

        assertThatThrownBy {
            JdkPreS5VertexTokenExecutor(Clock.fixed(now, ZoneOffset.UTC)).execute(
                PreS5VertexTokenRequest(
                    assertion = assertion,
                    timeout = Duration.ofSeconds(1),
                    expiresAt = now,
                    attempt = tokenAttempt(now),
                ),
            )
        }.isInstanceOf(PreS5VertexTokenTransportException::class.java)
        assertThat(assertion.all { it == 0.toByte() }).isTrue()
    }

    @Test
    fun `expired generation packet fails before a provider socket and clears evidence body`() {
        val now = Instant.parse("2026-08-03T12:00:00Z")
        val body = "{\"contents\":[]}".toByteArray()

        assertThatThrownBy {
            JdkPreS5VertexHttpExecutor(Clock.fixed(now, ZoneOffset.UTC)).execute(
                PreS5VertexHttpRequest(
                    endpoint =
                        URI.create(
                            "https://aiplatform.googleapis.com/v1/projects/capstone-rag/locations/global/publishers/google/models/gemini-3.5-flash:generateContent",
                        ),
                    bearerToken = "masked-access-token",
                    body = body,
                    timeout = Duration.ofSeconds(1),
                    expiresAt = now,
                    attempt = generateContentAttempt(now),
                ),
            )
        }.isInstanceOf(PreS5VertexTransportException::class.java)
        assertThat(body.all { it == 0.toByte() }).isTrue()
    }

    private fun tokenAttempt(expiresAt: Instant): PreS5VertexTokenAttempt = PreS5VertexTokenAttempt(lease(expiresAt))

    private fun generateContentAttempt(expiresAt: Instant) = PreS5VertexGenerateContentAttempt(lease(expiresAt))

    private fun lease(expiresAt: Instant): PreS5VertexUsageLease =
        PreS5VertexUsageLease(
            usageEventId = "rgr_vgu_${"a".repeat(32)}",
            ownerUserId = "usr_demo_user",
            expiresAt = expiresAt,
        )
}
