package com.capstone.decision.infrastructure.security

import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component

fun interface ActorIdentityHandleIssuer {
    fun issue(
        actorUserId: String,
        binding: ActorCapabilityBinding,
    ): String
}

@Component
class DatabaseActorIdentityHandleIssuer(
    authDatabase: AuthDatabase,
) : ActorIdentityHandleIssuer {
    private val jdbc = JdbcTemplate(authDatabase.dataSource)

    override fun issue(
        actorUserId: String,
        binding: ActorCapabilityBinding,
    ): String =
        jdbc.queryForObject(
            """
            SELECT register_actor_identity_handle_v1(?,?,?,?,?,?,?)
            """.trimIndent(),
            String::class.java,
            actorUserId,
            binding.operation,
            binding.targetKind,
            binding.targetId,
            binding.payloadHash,
            binding.rolePolicy.name,
            15,
        ) ?: throw ActorCapabilityDeniedException("Actor identity handle registration failed.")
}
