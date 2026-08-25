package com.capstone.decision.application.security

import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate

/** Application port for opening capability-backed, transaction-local owner RLS plumbing. */
interface ActorRlsScopePort {
    fun open(
        jdbc: NamedParameterJdbcTemplate,
        actorUserId: String,
        operation: String,
        targetKind: String,
        targetId: String,
    )
}
