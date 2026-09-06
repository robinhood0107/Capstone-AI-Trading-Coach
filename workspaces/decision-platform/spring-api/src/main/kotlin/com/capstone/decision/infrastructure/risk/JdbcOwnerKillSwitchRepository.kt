package com.capstone.decision.infrastructure.risk

import com.capstone.decision.application.risk.KillSwitchConflictException
import com.capstone.decision.application.risk.KillSwitchUnauthorizedException
import com.capstone.decision.application.risk.KillSwitchUnavailableException
import com.capstone.decision.application.risk.OwnerKillSwitchAccessPort
import com.capstone.decision.application.risk.OwnerStopSnapshot
import com.capstone.decision.application.security.AppPrincipal
import com.capstone.decision.application.security.AuthenticatedActorRef
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityDeniedException
import com.capstone.decision.infrastructure.security.ActorCapabilityIssuer
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Transactional
import tools.jackson.databind.ObjectMapper
import java.sql.SQLException
import java.time.Instant

/** 개인 상태는 인증된 owner capability로만 읽고 바꾼다. 전역 변경 함수는 호출하지 않는다. */
@Repository
class JdbcOwnerKillSwitchRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val issuer: ActorCapabilityIssuer,
    private val mapper: ObjectMapper,
) : OwnerKillSwitchAccessPort {
    @Transactional
    override fun access(
        actor: AppPrincipal,
        active: Boolean?,
        key: String?,
        requestId: String,
    ): OwnerStopSnapshot {
        val scope = key?.let { ActorCapabilityBinding.sha256(actor.userId + ":" + it) }
        val binding =
            ActorCapabilityBinding.request(
                if (active == null) "READ_OWNER_KILL_SWITCH" else "CHANGE_OWNER_KILL_SWITCH",
                "OWNER_KILL_SWITCH",
                actor.userId,
                ActorCapabilityRolePolicy.OWNER,
                actor.userId,
                actor.securityVersion.toString(),
                active?.toString(),
                scope,
                requestId,
            )
        try {
            val capability = issuer.issue(AuthenticatedActorRef.current(actor.userId, actor.securityVersion), binding)
            val jdbc = jdbcProvider.getIfAvailable() ?: throw KillSwitchUnavailableException()
            val json =
                jdbc.queryForObject(
                    "SELECT owner_kill_switch_authorized(:cap,:owner,:version,:active,:scope,:request)::text",
                    mapOf(
                        "cap" to capability,
                        "owner" to actor.userId,
                        "version" to actor.securityVersion,
                        "active" to active,
                        "scope" to scope,
                        "request" to requestId,
                    ),
                    String::class.java,
                ) ?: throw KillSwitchUnavailableException()
            val node = mapper.readTree(json)
            return OwnerStopSnapshot(
                node.path("active").booleanValue(),
                node.path("globalActive").booleanValue(),
                node.path("effectiveActive").booleanValue(),
                node.path("reasonClass").stringValue(),
                Instant.parse(node.path("changedAt").stringValue()),
                node.path("globalGeneration").longValue(),
            )
        } catch (error: ActorCapabilityDeniedException) {
            throw KillSwitchUnauthorizedException()
        } catch (error: Exception) {
            val sqlState = generateSequence<Throwable>(error) { it.cause }.filterIsInstance<SQLException>().firstOrNull()?.sqlState
            when (sqlState) {
                "42501" -> throw KillSwitchUnauthorizedException()
                "40001" -> throw KillSwitchConflictException()
                else -> throw KillSwitchUnavailableException(error)
            }
        }
    }
}
