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
        val forgedPayload = forgeCharacter(token, 12)
        val forgedSignature = forgeCharacter(token, separator + 1)
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

    /**
     * 고정 문자로 덮어쓰면 원본이 이미 그 문자일 때 위조본이 원본과 같아져 검증이 통과한다.
     * nonce가 매 실행마다 달라지므로 반드시 원본과 다른 문자를 고른다.
     */
    private fun forgeCharacter(
        token: String,
        index: Int,
    ): String {
        val replacement = if (token[index] == 'A') 'B' else 'A'
        return token.substring(0, index) + replacement + token.substring(index + 1)
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
