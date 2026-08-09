package com.capstone.decision.infrastructure.vertex

import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermission
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset

class PreS5VertexActivationReaderTest {
    @TempDir
    lateinit var temporaryDirectory: Path

    @Test
    fun `packet reader accepts only a secure bounded one shot Vertex packet and exposes hashes not nonce`() {
        val now = Instant.parse("2026-08-03T12:00:00Z")
        val fixture = fixture(now)

        val activation = PreS5VertexActivationReader(fixture.properties, Clock.fixed(now, ZoneOffset.UTC)).read()

        assertThat(activation.projectId).isEqualTo("capstone-rag")
        assertThat(activation.packetSha256).matches("[0-9a-f]{64}")
        assertThat(activation.nonceSha256).matches("[0-9a-f]{64}")
        assertThat(activation.toString()).doesNotContain("nonce-for-test")
        assertThat(activation.inputTokenCap).isEqualTo(13_000)
        assertThat(activation.outputTokenCap).isEqualTo(200)
    }

    @Test
    fun `packet reader rejects a packet that widens physical calls or uses a nonfixed endpoint`() {
        val now = Instant.parse("2026-08-03T12:00:00Z")
        val fixture = fixture(now)
        Files.writeString(
            fixture.packet,
            packetJson(now)
                .replace("\"physicalCallCap\":2", "\"physicalCallCap\":3")
                .replace(
                    "\"POST /v1/projects/{projectId}/locations/global/publishers/google/models/gemini-3.5-flash:generateContent\"",
                    "\"POST /v1/anything\"",
                ),
        )
        Files.setPosixFilePermissions(fixture.packet, FILE_PERMISSIONS)

        assertThatThrownBy {
            PreS5VertexActivationReader(fixture.properties, Clock.fixed(now, ZoneOffset.UTC)).read()
        }.isInstanceOf(PreS5VertexActivationException::class.java)
            .extracting("code")
            .isEqualTo("PRE_S5_VERTEX_PACKET_INVALID")
    }

    @Test
    fun `packet reader rejects a byte cap that could exceed the approved input token cost before generation`() {
        val now = Instant.parse("2026-08-03T12:00:00Z")
        val fixture = fixture(now)
        Files.writeString(
            fixture.packet,
            packetJson(now).replace("\"inputTokenCap\":13000", "\"inputTokenCap\":400"),
        )
        Files.setPosixFilePermissions(fixture.packet, FILE_PERMISSIONS)

        assertThatThrownBy {
            PreS5VertexActivationReader(fixture.properties, Clock.fixed(now, ZoneOffset.UTC)).read()
        }.isInstanceOf(PreS5VertexActivationException::class.java)
            .extracting("code")
            .isEqualTo("PRE_S5_VERTEX_PACKET_INVALID")
    }

    @Test
    fun `packet reader rejects owner identity because DB scope binding owns the actor`() {
        val now = Instant.parse("2026-08-03T12:00:00Z")
        val fixture = fixture(now)
        Files.writeString(
            fixture.packet,
            packetJson(now).replace(
                "\"projectId\":\"capstone-rag\"",
                "\"projectId\":\"capstone-rag\",\"ownerUserId\":\"usr_demo_user\"",
            ),
        )
        Files.setPosixFilePermissions(fixture.packet, FILE_PERMISSIONS)

        assertThatThrownBy {
            PreS5VertexActivationReader(fixture.properties, Clock.fixed(now, ZoneOffset.UTC)).read()
        }.isInstanceOf(PreS5VertexActivationException::class.java)
            .extracting("code")
            .isEqualTo("PRE_S5_VERTEX_PACKET_INVALID")
    }

    private fun fixture(now: Instant): Fixture {
        val root = temporaryDirectory.resolve("local-root")
        val control = root.resolve("control")
        Files.createDirectory(root)
        Files.createDirectory(control)
        Files.setPosixFilePermissions(root, DIRECTORY_PERMISSIONS)
        Files.setPosixFilePermissions(control, DIRECTORY_PERMISSIONS)
        val packet = control.resolve("pre-s5-vertex-activation.json")
        Files.writeString(packet, packetJson(now))
        Files.setPosixFilePermissions(packet, FILE_PERMISSIONS)
        return Fixture(
            packet = packet,
            properties =
                RagV2VertexProperties(
                    enabled = true,
                    localRoot = root.toString(),
                    headCommit = "1".repeat(40),
                    treeDigest = "2".repeat(64),
                    ciDigest = "3".repeat(64),
                    securityDigest = "4".repeat(64),
                ),
        )
    }

    private fun packetJson(now: Instant): String =
        """
        {
          "contractId":"pre-s5-vertex-activation/v1",
          "provider":"VERTEX_AI",
          "origin":"https://aiplatform.googleapis.com",
          "endpoint":"POST /v1/projects/{projectId}/locations/global/publishers/google/models/gemini-3.5-flash:generateContent",
          "authOrigin":"https://oauth2.googleapis.com",
          "authEndpoint":"POST /token",
          "projectId":"capstone-rag",
          "requestId":"req_vertex_packet_0000001",
          "scopeClaimId":"rvs_${"a".repeat(32)}",
          "questionFingerprintHmac":"${"f".repeat(64)}",
          "answerMode":"CONCISE",
          "consentEventId":"rce_vertex_consent_0001",
          "location":"global",
          "modelId":"gemini-3.5-flash",
          "headCommit":"${"1".repeat(40)}",
          "treeDigest":"${"2".repeat(64)}",
          "ciDigest":"${"3".repeat(64)}",
          "securityDigest":"${"4".repeat(64)}",
          "credentialFileSecurityEvidenceSha256":"${"5".repeat(64)}",
          "projectCacheStateEvidenceSha256":"${"6".repeat(64)}",
          "abuseMonitoringStateEvidenceSha256":"${"7".repeat(64)}",
          "modelAvailabilityEvidenceSha256":"${"8".repeat(64)}",
          "policySha256":"${"a".repeat(64)}",
          "processorSetSha256":"${"9".repeat(64)}",
          "issuedAt":"${now.minusSeconds(30)}",
          "expiresAt":"${now.plusSeconds(120)}",
          "logicalCallCap":1,
          "physicalCallCap":2,
          "tokenPhysicalCallCap":1,
          "generateContentPhysicalCallCap":1,
          "inputTokenCap":13000,
          "outputTokenCap":200,
          "inputByteCap":12000,
          "costCapMicrousd":200000,
          "inputMicrousdPerToken":10,
          "outputMicrousdPerToken":20,
          "retryCount":0,
          "rawArtifactCount":0,
          "operator":"pjjpj",
          "nonce":"nonce-for-test-0001"
        }
        """.trimIndent()

    private data class Fixture(
        val packet: Path,
        val properties: RagV2VertexProperties,
    )

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
