package com.capstone.decision.infrastructure.vertex

import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import tools.jackson.databind.json.JsonMapper
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermission
import java.security.KeyPairGenerator
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.ZoneOffset
import java.util.Base64

class PreS5VertexCredentialProviderTest {
    @TempDir
    lateinit var temporaryDirectory: Path

    @Test
    fun `secure service account file signs one bounded OAuth assertion without a library refresh call`() {
        val now = Instant.parse("2026-08-03T12:00:00Z")
        val root = temporaryDirectory.resolve("vertex-credentials")
        Files.createDirectory(root)
        Files.setPosixFilePermissions(root, DIRECTORY_PERMISSIONS)
        val credentialsPath = root.resolve("service-account.json")
        Files.writeString(credentialsPath, validServiceAccountJson())
        Files.setPosixFilePermissions(credentialsPath, FILE_PERMISSIONS)
        val executor = RecordingTokenExecutor()
        val provider =
            PreS5VertexCredentialProvider.forTest(
                tokenExecutor = executor,
                environment = { name ->
                    mapOf(
                        "GOOGLE_CLOUD_PROJECT" to "capstone-rag",
                        "GOOGLE_APPLICATION_CREDENTIALS" to credentialsPath.toString(),
                    )[name]
                },
                clock = Clock.fixed(now, ZoneOffset.UTC),
            )

        val prepared = provider.prepare(activation(now.plusSeconds(120)))
        val token =
            prepared.issueAccessToken(
                tokenAttempt(now.plusSeconds(120)),
                Duration.ofSeconds(2),
                now.plusSeconds(120),
            )

        assertThat(token).isEqualTo("masked-access-token")
        assertThat(executor.calls).isEqualTo(1)
        assertThat(executor.request?.assertion?.count { it.toInt().toChar() == '.' }).isEqualTo(0)
        assertThat(executor.capturedAssertion.count { it.toInt().toChar() == '.' }).isEqualTo(2)
        assertThat(executor.capturedAssertion.toString(StandardCharsets.US_ASCII)).doesNotContain("PRIVATE KEY")
        assertThat(executor.responseBody.all { it == 0.toByte() }).isTrue()
    }

    @Test
    fun `unsafe credential permissions or a mismatched project fail before a token attempt`() {
        val now = Instant.parse("2026-08-03T12:00:00Z")
        val root = temporaryDirectory.resolve("vertex-credentials-invalid")
        Files.createDirectory(root)
        Files.setPosixFilePermissions(root, DIRECTORY_PERMISSIONS)
        val credentialsPath = root.resolve("service-account.json")
        Files.writeString(credentialsPath, validServiceAccountJson())
        Files.setPosixFilePermissions(
            credentialsPath,
            FILE_PERMISSIONS + PosixFilePermission.GROUP_READ,
        )
        val executor = RecordingTokenExecutor()
        val unsafeProvider =
            PreS5VertexCredentialProvider.forTest(
                tokenExecutor = executor,
                environment = { name ->
                    mapOf(
                        "GOOGLE_CLOUD_PROJECT" to "capstone-rag",
                        "GOOGLE_APPLICATION_CREDENTIALS" to credentialsPath.toString(),
                    )[name]
                },
                clock = Clock.fixed(now, ZoneOffset.UTC),
            )

        assertThatThrownBy { unsafeProvider.prepare(activation(now.plusSeconds(120))) }
            .isInstanceOf(PreS5VertexCredentialException::class.java)
        assertThat(executor.calls).isZero()

        Files.setPosixFilePermissions(credentialsPath, FILE_PERMISSIONS)
        val wrongProjectProvider =
            PreS5VertexCredentialProvider.forTest(
                tokenExecutor = executor,
                environment = { name ->
                    mapOf(
                        "GOOGLE_CLOUD_PROJECT" to "other-project",
                        "GOOGLE_APPLICATION_CREDENTIALS" to credentialsPath.toString(),
                    )[name]
                },
                clock = Clock.fixed(now, ZoneOffset.UTC),
            )
        assertThatThrownBy { wrongProjectProvider.prepare(activation(now.plusSeconds(120))) }
            .isInstanceOf(PreS5VertexCredentialException::class.java)
        assertThat(executor.calls).isZero()
    }

    private fun activation(expiresAt: Instant): PreS5VertexActivation =
        PreS5VertexActivation(
            packetSha256 = "a".repeat(64),
            nonceSha256 = "b".repeat(64),
            projectId = "capstone-rag",
            requestId = "req_vertex_credential_00001",
            scopeClaimId = "rvs_${"c".repeat(32)}",
            questionFingerprintHmac = "d".repeat(64),
            answerMode = "CONCISE",
            consentEventId = "rce_vertex_consent_0001",
            policySha256 = "e".repeat(64),
            processorSetSha256 = "f".repeat(64),
            expiresAt = expiresAt,
            inputTokenCap = 2_000,
            outputTokenCap = 100,
            inputByteCap = 1_024,
            costCapMicrousd = 100_000,
            inputMicrousdPerToken = 10,
            outputMicrousdPerToken = 20,
            tokenPhysicalCallCap = 1,
            generateContentPhysicalCallCap = 1,
        )

    private fun tokenAttempt(expiresAt: Instant): PreS5VertexTokenAttempt =
        PreS5VertexTokenAttempt(
            PreS5VertexUsageLease(
                usageEventId = "rgr_vgu_${"a".repeat(32)}",
                ownerUserId = "usr_demo_user",
                expiresAt = expiresAt,
            ),
        )

    private fun validServiceAccountJson(): String {
        val keyPair = KeyPairGenerator.getInstance("RSA").apply { initialize(2048) }.generateKeyPair()
        val pem =
            "-----BEGIN PRIVATE KEY-----\n" +
                Base64.getMimeEncoder(64, "\n".toByteArray(StandardCharsets.US_ASCII)).encodeToString(keyPair.private.encoded) +
                "\n-----END PRIVATE KEY-----\n"
        return JsonMapper
            .builder()
            .build()
            .writeValueAsString(
                linkedMapOf(
                    "type" to "service_account",
                    "project_id" to "capstone-rag",
                    "private_key_id" to "a".repeat(40),
                    "private_key" to pem,
                    "client_email" to "capstone-rag@capstone-rag.iam.gserviceaccount.com",
                    "client_id" to "123456789012",
                    "token_uri" to "https://oauth2.googleapis.com/token",
                ),
            )
    }

    private class RecordingTokenExecutor : PreS5VertexTokenExecutor {
        var calls = 0
        var request: PreS5VertexTokenRequest? = null
        var capturedAssertion = ByteArray(0)
        val responseBody =
            """{"access_token":"masked-access-token","expires_in":3600,"token_type":"Bearer"}"""
                .toByteArray(StandardCharsets.UTF_8)

        override fun execute(request: PreS5VertexTokenRequest): PreS5VertexTokenResponse {
            calls += 1
            this.request = request
            capturedAssertion = request.assertion.copyOf()
            return PreS5VertexTokenResponse(200, responseBody)
        }
    }

    private companion object {
        val DIRECTORY_PERMISSIONS =
            setOf(
                PosixFilePermission.OWNER_READ,
                PosixFilePermission.OWNER_WRITE,
                PosixFilePermission.OWNER_EXECUTE,
            )
        val FILE_PERMISSIONS =
            setOf(
                PosixFilePermission.OWNER_READ,
                PosixFilePermission.OWNER_WRITE,
            )
    }
}
