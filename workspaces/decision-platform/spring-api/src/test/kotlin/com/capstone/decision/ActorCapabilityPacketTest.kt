package com.capstone.decision
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityClaims
import com.capstone.decision.infrastructure.security.ActorCapabilityPacketCodec
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import java.security.KeyPairGenerator
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset

class ActorCapabilityPacketTest {
    private val keyPair = KeyPairGenerator.getInstance("Ed25519").generateKeyPair()
    private val now = Instant.parse("2026-08-24T00:00:00Z")
    private val clock = Clock.fixed(now, ZoneOffset.UTC)

    @Test
    fun `packet binds every authorization claim and verifies with public key only`() {
        val claims = claims()
        val token = ActorCapabilityPacketCodec.sign(claims, keyPair.private)

        assertEquals(claims, ActorCapabilityPacketCodec.verify(token, keyPair.public, clock))
    }

    @Test
    fun `forged payload signature and public key fail closed`() {
        val token = ActorCapabilityPacketCodec.sign(claims(), keyPair.private)
        val separator = token.indexOf('.')
        val forgedPayload = token.substring(0, 12) + "A" + token.substring(13)
        val forgedSignature = token.substring(0, separator + 1) + "A" + token.substring(separator + 2)
        val otherKey = KeyPairGenerator.getInstance("Ed25519").generateKeyPair().public

        assertThrows(IllegalArgumentException::class.java) {
            ActorCapabilityPacketCodec.verify(forgedPayload, keyPair.public, clock)
        }
        assertThrows(IllegalArgumentException::class.java) {
            ActorCapabilityPacketCodec.verify(forgedSignature, keyPair.public, clock)
        }
        assertThrows(IllegalArgumentException::class.java) {
            ActorCapabilityPacketCodec.verify(token, otherKey, clock)
        }
    }

    @Test
    fun `expired packet fails before database consumption`() {
        val token =
            ActorCapabilityPacketCodec.sign(
                claims(issuedAt = now.minusSeconds(31), expiresAt = now.minusSeconds(1)),
                keyPair.private,
            )

        assertThrows(IllegalArgumentException::class.java) {
            ActorCapabilityPacketCodec.verify(token, keyPair.public, clock)
        }
    }

    @Test
    fun `API verifies the exact actor operation target and payload binding`() {
        val exactClaims =
            claims().copy(
                targetId = "job_12345678",
                payloadHash = ActorCapabilityBinding.sha256("job_12345678"),
            )
        val token = ActorCapabilityPacketCodec.sign(exactClaims, keyPair.private)
        val binding =
            ActorCapabilityBinding.target(
                operation = "READ_ASYNC_JOB",
                targetKind = "ASYNC_JOB",
                targetId = "job_12345678",
                rolePolicy = ActorCapabilityRolePolicy.ADMIN_ONLY,
            )

        assertEquals(
            exactClaims,
            ActorCapabilityPacketCodec.verifyBound(token, keyPair.public, exactClaims.actorUserId, binding, clock),
        )
        assertThrows(IllegalArgumentException::class.java) {
            ActorCapabilityPacketCodec.verifyBound(
                token,
                keyPair.public,
                exactClaims.actorUserId,
                binding.copy(
                    targetId = "job_other",
                    payloadHash = ActorCapabilityBinding.sha256("job_other"),
                ),
                clock,
            )
        }
    }

    private fun claims(
        issuedAt: Instant = now,
        expiresAt: Instant = now.plusSeconds(15),
    ) = ActorCapabilityClaims(
        actorUserId = "usr_demo_admin",
        actorRole = "ADMIN",
        actorSecurityVersion = 1,
        operation = "READ_ASYNC_JOB",
        targetKind = "ASYNC_JOB",
        targetId = "job_capability_00000001",
        payloadHash = "sha256:" + "a".repeat(64),
        requestId = "req_" + "b".repeat(32),
        transactionId = "txn_" + "c".repeat(32),
        nonce = "d".repeat(32),
        issuedAt = issuedAt,
        expiresAt = expiresAt,
    )
}
