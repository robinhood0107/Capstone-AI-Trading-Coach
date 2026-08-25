package com.capstone.decision.infrastructure.security

import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Component
import java.sql.Connection

/**
 * Opens transaction-local owner RLS plumbing only after PostgreSQL atomically consumes an exact
 * actor capability. The authenticated identity is derived by the authority; callers never select
 * role or securityVersion and a custom GUC by itself has no authorization value.
 */
@Component
class ActorRlsScope(
    private val actorCapabilityIssuer: ActorCapabilityIssuer,
) {
    fun open(
        jdbc: NamedParameterJdbcTemplate,
        actorUserId: String,
        binding: ActorCapabilityBinding,
    ) {
        val capability = actorCapabilityIssuer.issue(actorUserId, binding)
        check(
            jdbc.queryForObject(
                """
                SELECT open_actor_rls_scope_v1(
                  :capability,:actorUserId,:operation,:targetKind,:targetId,:payloadHash
                )
                """.trimIndent(),
                mapOf(
                    "capability" to capability,
                    "actorUserId" to actorUserId,
                    "operation" to binding.operation,
                    "targetKind" to binding.targetKind,
                    "targetId" to binding.targetId,
                    "payloadHash" to binding.payloadHash,
                ),
                Boolean::class.java,
            ) == true,
        )
    }

    fun open(
        jdbc: JdbcTemplate,
        actorUserId: String,
        binding: ActorCapabilityBinding,
    ) {
        val capability = actorCapabilityIssuer.issue(actorUserId, binding)
        check(
            jdbc.queryForObject(
                "SELECT open_actor_rls_scope_v1(?,?,?,?,?,?)",
                Boolean::class.java,
                capability,
                actorUserId,
                binding.operation,
                binding.targetKind,
                binding.targetId,
                binding.payloadHash,
            ) == true,
        )
    }

    fun open(
        connection: Connection,
        actorUserId: String,
        binding: ActorCapabilityBinding,
    ) {
        val capability = actorCapabilityIssuer.issue(actorUserId, binding)
        connection
            .prepareStatement("SELECT open_actor_rls_scope_v1(?,?,?,?,?,?)")
            .use { statement ->
                statement.setString(1, capability)
                statement.setString(2, actorUserId)
                statement.setString(3, binding.operation)
                statement.setString(4, binding.targetKind)
                statement.setString(5, binding.targetId)
                statement.setString(6, binding.payloadHash)
                statement.executeQuery().use { result -> check(result.next() && result.getBoolean(1)) }
            }
    }
}
