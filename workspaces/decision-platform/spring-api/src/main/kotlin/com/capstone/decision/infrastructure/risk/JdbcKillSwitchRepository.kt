package com.capstone.decision.infrastructure.risk

import com.capstone.decision.application.risk.KillSwitchConflictException
import com.capstone.decision.application.risk.KillSwitchForbiddenException
import com.capstone.decision.application.risk.KillSwitchGate
import com.capstone.decision.application.risk.KillSwitchGatePort
import com.capstone.decision.application.risk.KillSwitchMutationCommand
import com.capstone.decision.application.risk.KillSwitchMutationPort
import com.capstone.decision.application.risk.KillSwitchMutationResult
import com.capstone.decision.application.risk.KillSwitchPublicState
import com.capstone.decision.application.risk.KillSwitchQueryPort
import com.capstone.decision.application.risk.KillSwitchUnauthorizedException
import com.capstone.decision.domain.risk.KillSwitchReasonClass
import com.capstone.decision.infrastructure.security.ActorCapabilityDeniedException
import com.capstone.decision.infrastructure.security.ActorCapabilityIssuer
import org.springframework.beans.factory.ObjectProvider
import org.springframework.dao.DataAccessException
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Transactional
import java.sql.SQLException
import java.time.OffsetDateTime

/**
 * GLOBAL row를 매번 DB에서 읽고 상태·전이·무효화·audit·outbox를 하나의 local transaction에 묶는다.
 * 외부 provider, Redis, gRPC 호출은 이 adapter에 존재하지 않는다.
 */
@Repository
class JdbcKillSwitchRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val actorCapabilityIssuer: ActorCapabilityIssuer,
) : KillSwitchQueryPort,
    KillSwitchGatePort,
    KillSwitchMutationPort {
    override fun readPublicState(): KillSwitchPublicState =
        jdbc()
            .query(
                """
                SELECT active, reason_class, changed_at
                FROM kill_switch_user_projection
                LIMIT 2
                """.trimIndent(),
                emptyMap<String, Any>(),
            ) { result, _ ->
                KillSwitchPublicState(
                    active = result.getBoolean("active"),
                    reasonClass = KillSwitchReasonClass.fromStored(result.getString("reason_class")),
                    changedAt = result.getObject("changed_at", OffsetDateTime::class.java).toInstant(),
                )
            }.single()

    override fun readGate(): KillSwitchGate =
        jdbc()
            .query(
                "SELECT active, generation FROM read_kill_switch_gate()",
                emptyMap<String, Any>(),
            ) { result, _ ->
                KillSwitchGate(
                    active = result.getBoolean("active"),
                    generation = result.getLong("generation"),
                )
            }.single()

    @Transactional
    override fun mutate(command: KillSwitchMutationCommand): KillSwitchMutationResult {
        val jdbc = jdbc()
        if (!command.requestedActive && command.actor.role.name != "ADMIN") {
            throw KillSwitchForbiddenException()
        }
        val observedGeneration = readGate().generation
        try {
            return jdbc
                .query(
                    """
                    SELECT active, reason_class, changed_at, changed, previous_active,
                           generation, invalidated_decision_count
                    FROM transition_kill_switch_authorized(
                      :capability, :actorUserId, :securityVersion, :requestedActive,
                      :observedGeneration, :requestId
                    )
                    """.trimIndent(),
                    mapOf(
                        "capability" to actorCapabilityIssuer.issue(command.actor.userId),
                        "actorUserId" to command.actor.userId,
                        "securityVersion" to command.actor.securityVersion,
                        "requestedActive" to command.requestedActive,
                        "observedGeneration" to observedGeneration,
                        "requestId" to command.actor.requestId,
                    ),
                ) { result, _ ->
                    KillSwitchMutationResult(
                        state =
                            KillSwitchPublicState(
                                active = result.getBoolean("active"),
                                reasonClass = KillSwitchReasonClass.fromStored(result.getString("reason_class")),
                                changedAt = result.getObject("changed_at", OffsetDateTime::class.java).toInstant(),
                            ),
                        changed = result.getBoolean("changed"),
                        previousActive = result.getBoolean("previous_active"),
                        generation = result.getLong("generation"),
                        invalidatedDecisionCount = result.getInt("invalidated_decision_count"),
                    )
                }.single()
        } catch (_: ActorCapabilityDeniedException) {
            throw KillSwitchUnauthorizedException()
        } catch (exception: DataAccessException) {
            when (exception.sqlState()) {
                "40001" -> throw KillSwitchConflictException()
                "42501" -> throw KillSwitchUnauthorizedException()
                else -> throw exception
            }
        }
    }

    private fun Throwable.sqlState(): String? {
        var current: Throwable? = this
        while (current != null) {
            if (current is SQLException) return current.sqlState
            current = current.cause
        }
        return null
    }

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: error("Kill Switch JDBC access is unavailable without a configured DataSource.")
}
