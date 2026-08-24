package com.capstone.decision.infrastructure.security

import org.springframework.jdbc.core.JdbcTemplate
import java.security.MessageDigest
import java.security.PrivateKey
import java.security.PublicKey
import java.sql.Timestamp
import java.time.Clock
import java.time.Instant
import java.time.temporal.ChronoUnit
import java.util.HexFormat
import java.util.UUID
import javax.sql.DataSource

enum class ActorCapabilityRolePolicy(
    val allowedRoles: Set<String>,
) {
    OWNER(setOf("USER", "ADMIN")),
    ADMIN_ONLY(setOf("ADMIN")),
    ;

    fun accepts(role: String): Boolean = role in allowedRoles
}

data class ActorCapabilityBinding(
    val operation: String,
    val targetKind: String,
    val targetId: String,
    val payloadHash: String,
    val rolePolicy: ActorCapabilityRolePolicy,
) {
    init {
        require(operation.matches(Regex("^[A-Z][A-Z0-9_]{2,63}$")))
        require(targetKind.matches(Regex("^[A-Z][A-Z0-9_]{2,31}$")))
        require(targetId.length in 1..160 && targetId.none { it == '\n' || it == '\r' })
        require(payloadHash.matches(Regex("^sha256:[0-9a-f]{64}$")))
    }

    companion object {
        fun target(
            operation: String,
            targetKind: String,
            targetId: String,
            rolePolicy: ActorCapabilityRolePolicy,
        ): ActorCapabilityBinding =
            ActorCapabilityBinding(
                operation = operation,
                targetKind = targetKind,
                targetId = targetId,
                payloadHash = sha256(targetId),
                rolePolicy = rolePolicy,
            )

        fun request(
            operation: String,
            targetKind: String,
            targetId: String,
            rolePolicy: ActorCapabilityRolePolicy,
            vararg payloadValues: String?,
        ): ActorCapabilityBinding =
            ActorCapabilityBinding(
                operation = operation,
                targetKind = targetKind,
                targetId = targetId,
                payloadHash = sha256(canonicalPayload(*payloadValues)),
                rolePolicy = rolePolicy,
            )

        fun canonicalPayload(vararg values: String?): String =
            values.joinToString(separator = "") { value ->
                if (value == null) "-:\n" else "${value.toByteArray(Charsets.UTF_8).size}:$value\n"
            }

        fun sha256(value: String): String =
            "sha256:" +
                HexFormat
                    .of()
                    .formatHex(MessageDigest.getInstance("SHA-256").digest(value.toByteArray(Charsets.UTF_8)))
    }
}

class DatabaseActorCapabilityAuthority(
    dataSource: DataSource,
    private val privateKey: PrivateKey,
    private val publicKey: PublicKey,
    private val clock: Clock = Clock.systemUTC(),
) {
    private val jdbc = JdbcTemplate(dataSource)

    fun issue(
        actorUserId: String,
        binding: ActorCapabilityBinding,
    ): String {
        val subject =
            jdbc
                .query(
                    "SELECT actor_role,actor_security_version FROM read_actor_capability_subject(?)",
                    { statement -> statement.setString(1, actorUserId) },
                ) { rows, _ -> rows.getString(1) to rows.getLong(2) }
                .singleOrNull()
                ?: throw ActorCapabilityDeniedException("Actor is unavailable.")
        if (!binding.rolePolicy.accepts(subject.first)) {
            throw ActorCapabilityDeniedException("Actor role is unavailable.")
        }
        val issuedAt = Instant.now(clock).truncatedTo(ChronoUnit.SECONDS)
        val claims =
            ActorCapabilityClaims(
                actorUserId = actorUserId,
                actorRole = subject.first,
                actorSecurityVersion = subject.second,
                operation = binding.operation,
                targetKind = binding.targetKind,
                targetId = binding.targetId,
                payloadHash = binding.payloadHash,
                requestId = "req_" + compactUuid(),
                transactionId = "txn_" + compactUuid(),
                nonce = compactUuid(),
                issuedAt = issuedAt,
                expiresAt = issuedAt.plusSeconds(15),
            )
        val token = ActorCapabilityPacketCodec.sign(claims, privateKey)
        check(ActorCapabilityPacketCodec.verify(token, publicKey, clock) == claims)
        val registered =
            jdbc.queryForObject(
                """
                SELECT register_actor_request_capability_v2(
                  ?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                """.trimIndent(),
                Boolean::class.java,
                token,
                claims.actorUserId,
                claims.actorRole,
                claims.actorSecurityVersion,
                claims.operation,
                claims.targetKind,
                claims.targetId,
                claims.payloadHash,
                claims.requestId,
                claims.transactionId,
                claims.nonce,
                Timestamp.from(claims.issuedAt),
                Timestamp.from(claims.expiresAt),
                "ed25519:" + token.substringAfterLast('.'),
            )
        if (registered != true) {
            throw ActorCapabilityDeniedException("Capability registration failed.")
        }
        return token
    }

    private fun compactUuid(): String = UUID.randomUUID().toString().replace("-", "")
}
