package com.capstone.decision
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityClaims
import com.capstone.decision.infrastructure.security.ActorCapabilityClientProperties
import com.capstone.decision.infrastructure.security.ActorCapabilityPacketCodec
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import com.capstone.decision.infrastructure.security.ActorCapabilityWire
import com.capstone.decision.infrastructure.security.HttpActorCapabilityIssuer
import com.sun.net.httpserver.HttpServer
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import java.net.InetAddress
import java.net.InetSocketAddress
import java.nio.charset.StandardCharsets
import java.security.KeyPairGenerator
import java.time.Instant
import java.util.Base64
import java.util.concurrent.Executors

class ActorCapabilityTransportTest {
    private val keyPair = KeyPairGenerator.getInstance("Ed25519").generateKeyPair()
    private val executor = Executors.newSingleThreadExecutor()
    private var server: HttpServer? = null

    @AfterEach
    fun close() {
        server?.stop(0)
        executor.shutdownNow()
    }

    @Test
    fun `API sends exact binding and accepts only the authority signed response`() {
        val binding = ActorCapabilityBinding.target("READ_ASYNC_JOB", "ASYNC_JOB", "job_12345678", ActorCapabilityRolePolicy.ADMIN_ONLY)
        val issuer =
            issuer { actorUserId, requestedBinding ->
                assertEquals("usr_demo_admin", actorUserId)
                assertEquals(binding, requestedBinding)
                ActorCapabilityPacketCodec.sign(claims(actorUserId, requestedBinding), keyPair.private)
            }

        val token = issuer.issue("usr_demo_admin", binding)

        assertEquals(
            "job_12345678",
            ActorCapabilityPacketCodec.verify(token, keyPair.public).targetId,
        )
    }

    @Test
    fun `API rejects an authority response bound to another target`() {
        val binding = ActorCapabilityBinding.target("READ_ASYNC_JOB", "ASYNC_JOB", "job_12345678", ActorCapabilityRolePolicy.ADMIN_ONLY)
        val issuer =
            issuer { actorUserId, requestedBinding ->
                val forged =
                    requestedBinding.copy(
                        targetId = "job_other",
                        payloadHash = ActorCapabilityBinding.sha256("job_other"),
                    )
                ActorCapabilityPacketCodec.sign(claims(actorUserId, forged), keyPair.private)
            }

        assertThrows(IllegalArgumentException::class.java) {
            issuer.issue("usr_demo_admin", binding)
        }
    }

    private fun issuer(sign: (String, ActorCapabilityBinding) -> String): HttpActorCapabilityIssuer {
        val sharedSecret = "s".repeat(64)
        val authority = HttpServer.create(InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 4)
        authority.executor = executor
        authority.createContext(ActorCapabilityWire.ISSUE_PATH) { exchange ->
            val body = exchange.requestBody.use { it.readAllBytes() }
            val (actorUserId, binding) = ActorCapabilityWire.decode(body)
            assertEquals("Bearer $sharedSecret", exchange.requestHeaders.getFirst("Authorization"))
            val response = sign(actorUserId, binding).toByteArray(StandardCharsets.US_ASCII)
            exchange.sendResponseHeaders(200, response.size.toLong())
            exchange.responseBody.use { it.write(response) }
            exchange.close()
        }
        authority.start()
        server = authority
        val publicKey = Base64.getUrlEncoder().withoutPadding().encodeToString(keyPair.public.encoded)
        return HttpActorCapabilityIssuer(
            ActorCapabilityClientProperties(
                authorityUrl = "http://127.0.0.1:${authority.address.port}${ActorCapabilityWire.ISSUE_PATH}",
                sharedSecret = sharedSecret,
                publicKey = publicKey,
            ),
        )
    }

    private fun claims(
        actorUserId: String,
        binding: ActorCapabilityBinding,
    ): ActorCapabilityClaims {
        val now = Instant.now()
        return ActorCapabilityClaims(
            actorUserId = actorUserId,
            actorRole = if (binding.rolePolicy == ActorCapabilityRolePolicy.ADMIN_ONLY) "ADMIN" else "USER",
            actorSecurityVersion = 1,
            operation = binding.operation,
            targetKind = binding.targetKind,
            targetId = binding.targetId,
            payloadHash = binding.payloadHash,
            requestId = "req_" + "1".repeat(32),
            transactionId = "txn_" + "2".repeat(32),
            nonce = "3".repeat(32),
            issuedAt = now,
            expiresAt = now.plusSeconds(15),
        )
    }
}
