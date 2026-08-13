package com.capstone.decision.infrastructure.vertex

import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import tools.jackson.databind.json.JsonMapper
import java.nio.charset.StandardCharsets
import java.security.KeyPairGenerator
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import java.util.Base64

class PreS5VertexServiceAccountOAuthProviderTest {
    @Test
    fun `service account signs one bounded cloud-platform assertion and accepts one bearer token response`() {
        val credentialProvider = mockk<PreS5VertexServiceAccountCredentialProvider>()
        val executor = mockk<PreS5VertexOAuthTokenExecutor>()
        val request = slot<PreS5VertexOAuthTokenRequest>()
        var capturedForm = ""
        val keyPair = KeyPairGenerator.getInstance("RSA").apply { initialize(2048) }.generateKeyPair()
        every { credentialProvider.acquire() } returns
            PreS5VertexServiceAccountCredential(
                projectId = "project-test-123",
                clientEmail = "vertex-test@project-test-123.iam.gserviceaccount.com",
                privateKeyId = "a".repeat(40),
                privateKey = keyPair.private,
            )
        every { executor.execute(capture(request)) } answers {
            capturedForm = request.captured.body.toString(StandardCharsets.US_ASCII)
            PreS5VertexOAuthTokenResponse(
                statusCode = 200,
                body =
                    """{"access_token":"ya29.test_token_123","expires_in":3600,"token_type":"Bearer","scope":"https://www.googleapis.com/auth/cloud-platform"}"""
                        .toByteArray(StandardCharsets.UTF_8),
            )
        }
        val now = Instant.parse("2026-08-12T03:00:00Z")
        val activation = activation(now.plusSeconds(300))

        val token =
            PreS5VertexServiceAccountOAuthProvider(
                credentialProvider,
                executor,
                Clock.fixed(now, ZoneOffset.UTC),
            ).acquire(activation, PreS5VertexTokenAttempt(lease(activation.expiresAt)))

        assertThat(token.projectId).isEqualTo("project-test-123")
        assertThat(token.value.toString(StandardCharsets.US_ASCII)).isEqualTo("ya29.test_token_123")
        val form = capturedForm
        assertThat(form).startsWith("grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion=")
        val jwt = form.substringAfter("&assertion=")
        val segments = jwt.split('.')
        assertThat(segments).hasSize(3)
        val decoder = Base64.getUrlDecoder()
        val header = JsonMapper.builder().build().readTree(decoder.decode(segments[0]))
        val claims = JsonMapper.builder().build().readTree(decoder.decode(segments[1]))
        assertThat(header["alg"].stringValue()).isEqualTo("RS256")
        assertThat(header["kid"].stringValue()).isEqualTo("a".repeat(40))
        assertThat(claims["iss"].stringValue()).isEqualTo("vertex-test@project-test-123.iam.gserviceaccount.com")
        assertThat(claims["scope"].stringValue()).isEqualTo("https://www.googleapis.com/auth/cloud-platform")
        assertThat(claims["aud"].stringValue()).isEqualTo("https://oauth2.googleapis.com/token")
    }

    @Test
    fun `unexpected OAuth response fields fail closed and the provider response buffer is erased`() {
        val credentialProvider = mockk<PreS5VertexServiceAccountCredentialProvider>()
        val executor = mockk<PreS5VertexOAuthTokenExecutor>()
        val keyPair = KeyPairGenerator.getInstance("RSA").apply { initialize(2048) }.generateKeyPair()
        every { credentialProvider.acquire() } returns
            PreS5VertexServiceAccountCredential(
                projectId = "project-test-123",
                clientEmail = "vertex-test@project-test-123.iam.gserviceaccount.com",
                privateKeyId = "a".repeat(40),
                privateKey = keyPair.private,
            )
        val responseBody =
            """{"access_token":"ya29.test_token_123","expires_in":3600,"token_type":"Bearer","refresh_token":"forbidden"}"""
                .toByteArray(StandardCharsets.UTF_8)
        every { executor.execute(any()) } returns PreS5VertexOAuthTokenResponse(200, responseBody)
        val now = Instant.parse("2026-08-12T03:00:00Z")
        val activation = activation(now.plusSeconds(300))

        assertThatThrownBy {
            PreS5VertexServiceAccountOAuthProvider(
                credentialProvider,
                executor,
                Clock.fixed(now, ZoneOffset.UTC),
            ).acquire(activation, PreS5VertexTokenAttempt(lease(activation.expiresAt)))
        }.isInstanceOf(PreS5VertexOAuthException::class.java)
        assertThat(responseBody).containsOnly(0)
    }

    @Test
    fun `OAuth invalid grant is reduced to one content-free failure leaf and the response is erased`() {
        val credentialProvider = mockk<PreS5VertexServiceAccountCredentialProvider>()
        val executor = mockk<PreS5VertexOAuthTokenExecutor>()
        val keyPair = KeyPairGenerator.getInstance("RSA").apply { initialize(2048) }.generateKeyPair()
        every { credentialProvider.acquire() } returns
            PreS5VertexServiceAccountCredential(
                projectId = "project-test-123",
                clientEmail = "vertex-test@project-test-123.iam.gserviceaccount.com",
                privateKeyId = "a".repeat(40),
                privateKey = keyPair.private,
            )
        val responseBody =
            """{"error":"invalid_grant","error_description":"provider detail must not escape"}"""
                .toByteArray(StandardCharsets.UTF_8)
        every { executor.execute(any()) } returns PreS5VertexOAuthTokenResponse(400, responseBody)
        val now = Instant.parse("2026-08-12T03:00:00Z")
        val activation = activation(now.plusSeconds(300))

        val failure =
            runCatching {
                PreS5VertexServiceAccountOAuthProvider(
                    credentialProvider,
                    executor,
                    Clock.fixed(now, ZoneOffset.UTC),
                ).acquire(activation, PreS5VertexTokenAttempt(lease(activation.expiresAt)))
            }.exceptionOrNull()

        assertThat(failure).isInstanceOf(PreS5VertexOAuthException::class.java)
        assertThat((failure as PreS5VertexOAuthException).failureLeaf)
            .isEqualTo(PreS5VertexOAuthFailureLeaf.OAUTH_INVALID_GRANT)
        assertThat(responseBody).containsOnly(0)
    }

    @Test
    fun `OAuth non-success without a body keeps the HTTP failure leaf`() {
        val credentialProvider = mockk<PreS5VertexServiceAccountCredentialProvider>()
        val executor = mockk<PreS5VertexOAuthTokenExecutor>()
        val keyPair = KeyPairGenerator.getInstance("RSA").apply { initialize(2048) }.generateKeyPair()
        every { credentialProvider.acquire() } returns
            PreS5VertexServiceAccountCredential(
                projectId = "project-test-123",
                clientEmail = "vertex-test@project-test-123.iam.gserviceaccount.com",
                privateKeyId = "a".repeat(40),
                privateKey = keyPair.private,
            )
        every { executor.execute(any()) } returns PreS5VertexOAuthTokenResponse(400, byteArrayOf())
        val now = Instant.parse("2026-08-12T03:00:00Z")
        val activation = activation(now.plusSeconds(300))

        val failure =
            runCatching {
                PreS5VertexServiceAccountOAuthProvider(
                    credentialProvider,
                    executor,
                    Clock.fixed(now, ZoneOffset.UTC),
                ).acquire(activation, PreS5VertexTokenAttempt(lease(activation.expiresAt)))
            }.exceptionOrNull()

        assertThat(failure).isInstanceOf(PreS5VertexOAuthException::class.java)
        assertThat((failure as PreS5VertexOAuthException).failureLeaf)
            .isEqualTo(PreS5VertexOAuthFailureLeaf.HTTP_4XX)
    }

    private fun activation(expiresAt: Instant) =
        PreS5VertexActivation(
            packetSha256 = "d".repeat(64),
            nonceSha256 = "e".repeat(64),
            authenticationMode = "SERVICE_ACCOUNT_OAUTH",
            projectId = "project-test-123",
            modelId = "gemini-3.5-flash",
            requestId = "req_vertex_transport_0000001",
            scopeClaimId = "rvs_${"a".repeat(32)}",
            questionFingerprintHmac = "f".repeat(64),
            answerMode = "CONCISE",
            consentEventId = "rce_${"a".repeat(32)}",
            policySha256 = "a".repeat(64),
            processorSetSha256 = "b".repeat(64),
            expiresAt = expiresAt,
            inputTokenCap = 13_000,
            outputTokenCap = 200,
            inputByteCap = 12_000,
            costCapMicrousd = 200_000,
            inputMicrousdPerToken = 10,
            outputMicrousdPerToken = 20,
            tokenPhysicalCallCap = 1,
            generateContentPhysicalCallCap = 1,
        )

    private fun lease(expiresAt: Instant) = PreS5VertexUsageLease("rgr_vgu_${"a".repeat(32)}", "usr_demo_user", expiresAt)
}
