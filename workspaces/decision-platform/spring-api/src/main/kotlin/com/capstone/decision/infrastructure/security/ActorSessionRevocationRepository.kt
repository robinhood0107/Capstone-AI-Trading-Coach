package com.capstone.decision.infrastructure.security

import com.capstone.decision.application.security.AuthenticatedActorRef
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Repository

/** Revokes only the already verified Bearer session represented by the current principal. */
@Repository
class ActorSessionRevocationRepository(
    private val authDatabaseProvider: ObjectProvider<AuthDatabase>,
) {
    fun revoke(actor: AuthenticatedActorRef): Boolean =
        JdbcTemplate(
            authDatabaseProvider.ifAvailable?.dataSource
                ?: throw IllegalStateException("Authentication database is unavailable."),
        ).queryForObject(
            "select revoke_actor_auth_session_v1(?,?,?)",
            Boolean::class.java,
            actor.sessionHandle,
            actor.expectedUserId,
            actor.securityVersion,
        ) ?: false
}
