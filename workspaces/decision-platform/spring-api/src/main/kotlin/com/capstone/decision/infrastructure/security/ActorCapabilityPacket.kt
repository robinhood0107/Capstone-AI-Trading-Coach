package com.capstone.decision.infrastructure.security

import java.nio.charset.StandardCharsets
import java.security.KeyFactory
import java.security.PrivateKey
import java.security.PublicKey
import java.security.Signature
import java.security.spec.PKCS8EncodedKeySpec
import java.security.spec.X509EncodedKeySpec
import java.time.Clock
import java.time.Instant
import java.util.Base64

data class ActorCapabilityClaims(
    val actorUserId: String,
    val actorRole: String,
    val actorSecurityVersion: Long,
    val operation: String,
    val targetKind: String,
    val targetId: String,
    val payloadHash: String,
    val requestId: String,
    val transactionId: String,
    val nonce: String,
    val issuedAt: Instant,
    val expiresAt: Instant,
) {
    init {
        require(actorUserId.matches(Regex("^usr_[A-Za-z0-9_-]{4,96}$")))
        require(actorRole == "USER" || actorRole == "ADMIN")
        require(actorSecurityVersion >= 1)
        require(operation.matches(Regex("^[A-Z][A-Z0-9_]{2,63}$")))
        require(targetKind.matches(Regex("^[A-Z][A-Z0-9_]{2,31}$")))
        require(targetId.length in 1..160 && targetId.none { it == '\n' || it == '\r' })
        require(payloadHash.matches(Regex("^sha256:[0-9a-f]{64}$")))
        require(requestId.matches(Regex("^req_[0-9a-f]{32}$")))
        require(transactionId.matches(Regex("^txn_[0-9a-f]{32}$")))
        require(nonce.matches(Regex("^[0-9a-f]{32}$")))
        require(expiresAt.isAfter(issuedAt) && !expiresAt.isAfter(issuedAt.plusSeconds(MAX_LIFETIME_SECONDS)))
    }

    fun canonicalBytes(): ByteArray =
        listOf(
            VERSION,
            actorUserId,
            actorRole,
            actorSecurityVersion.toString(),
            operation,
            targetKind,
            targetId,
            payloadHash,
            requestId,
            transactionId,
            nonce,
            issuedAt.epochSecond.toString(),
            expiresAt.epochSecond.toString(),
        ).joinToString("\n").toByteArray(StandardCharsets.UTF_8)

    companion object {
        const val VERSION = "p1-actor-capability.v2"
        const val MAX_LIFETIME_SECONDS = 30L

        fun parse(bytes: ByteArray): ActorCapabilityClaims {
            require(bytes.size in 1..MAX_CANONICAL_BYTES)
            val fields = bytes.toString(StandardCharsets.UTF_8).split('\n')
            require(fields.size == 13 && fields[0] == VERSION)
            return ActorCapabilityClaims(
                actorUserId = fields[1],
                actorRole = fields[2],
                actorSecurityVersion = fields[3].toLong(),
                operation = fields[4],
                targetKind = fields[5],
                targetId = fields[6],
                payloadHash = fields[7],
                requestId = fields[8],
                transactionId = fields[9],
                nonce = fields[10],
                issuedAt = Instant.ofEpochSecond(fields[11].toLong()),
                expiresAt = Instant.ofEpochSecond(fields[12].toLong()),
            ).also { require(it.canonicalBytes().contentEquals(bytes)) }
        }
    }
}

object ActorCapabilityPacketCodec {
    private val encoder = Base64.getUrlEncoder().withoutPadding()
    private val decoder = Base64.getUrlDecoder()
    private const val PREFIX = "cap2_"
    private const val ED25519_SIGNATURE_BYTES = 64
    private const val MAX_TOKEN_CHARS = 1_024

    fun sign(
        claims: ActorCapabilityClaims,
        privateKey: PrivateKey,
    ): String {
        require(privateKey.algorithm.equals("EdDSA", ignoreCase = true) || privateKey.algorithm == "Ed25519")
        val payload = claims.canonicalBytes()
        val signature =
            Signature.getInstance("Ed25519").run {
                initSign(privateKey)
                update(payload)
                sign()
            }
        check(signature.size == ED25519_SIGNATURE_BYTES)
        return PREFIX + encoder.encodeToString(payload) + "." + encoder.encodeToString(signature)
    }

    fun verify(
        token: String,
        publicKey: PublicKey,
        clock: Clock = Clock.systemUTC(),
    ): ActorCapabilityClaims {
        require(publicKey.algorithm.equals("EdDSA", ignoreCase = true) || publicKey.algorithm == "Ed25519")
        require(token.length in 1..MAX_TOKEN_CHARS && token.startsWith(PREFIX))
        val parts = token.removePrefix(PREFIX).split('.')
        require(parts.size == 2 && parts.all { it.isNotEmpty() && it.matches(Regex("^[A-Za-z0-9_-]+$")) })
        val payload = decoder.decode(parts[0])
        val signature = decoder.decode(parts[1])
        require(signature.size == ED25519_SIGNATURE_BYTES)
        val signatureValid =
            runCatching {
                Signature.getInstance("Ed25519").run {
                    initVerify(publicKey)
                    update(payload)
                    verify(signature)
                }
            }.getOrDefault(false)
        require(signatureValid)
        val claims = ActorCapabilityClaims.parse(payload)
        val now = clock.instant()
        require(!claims.issuedAt.isAfter(now.plusSeconds(1)))
        require(claims.expiresAt.isAfter(now))
        return claims
    }

    fun verifyBound(
        token: String,
        publicKey: PublicKey,
        actorUserId: String,
        binding: ActorCapabilityBinding,
        clock: Clock = Clock.systemUTC(),
    ): ActorCapabilityClaims {
        val claims = verify(token, publicKey, clock)
        require(claims.actorUserId == actorUserId)
        require(binding.rolePolicy.accepts(claims.actorRole))
        require(claims.operation == binding.operation)
        require(claims.targetKind == binding.targetKind)
        require(claims.targetId == binding.targetId)
        require(claims.payloadHash == binding.payloadHash)
        return claims
    }
}

object ActorCapabilityKeyCodec {
    private val decoder = Base64.getUrlDecoder()

    fun privateKey(pkcs8Base64Url: String): PrivateKey {
        require(pkcs8Base64Url.length in 32..512 && pkcs8Base64Url.matches(Regex("^[A-Za-z0-9_-]+$")))
        return KeyFactory.getInstance("Ed25519").generatePrivate(PKCS8EncodedKeySpec(decoder.decode(pkcs8Base64Url)))
    }

    fun publicKey(x509Base64Url: String): PublicKey {
        require(x509Base64Url.length in 32..512 && x509Base64Url.matches(Regex("^[A-Za-z0-9_-]+$")))
        return KeyFactory.getInstance("Ed25519").generatePublic(X509EncodedKeySpec(decoder.decode(x509Base64Url)))
    }
}

private const val MAX_CANONICAL_BYTES = 1_024
