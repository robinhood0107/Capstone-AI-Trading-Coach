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
import com.capstone.decision.domain.risk.KillSwitchState
import com.capstone.decision.domain.risk.KillSwitchTransition
import com.capstone.decision.domain.risk.KillSwitchTransitionPolicy
import org.springframework.beans.factory.ObjectProvider
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Transactional
import tools.jackson.databind.ObjectMapper
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.UUID

/**
 * GLOBAL row를 매번 DB에서 읽고 상태·전이·무효화·audit·outbox를 하나의 local transaction에 묶는다.
 * 외부 provider, Redis, gRPC 호출은 이 adapter에 존재하지 않는다.
 */
@Repository
class JdbcKillSwitchRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val objectMapper: ObjectMapper,
) : KillSwitchQueryPort,
    KillSwitchGatePort,
    KillSwitchMutationPort {
    private val transitionPolicy = KillSwitchTransitionPolicy()

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
        val current = lockCurrent(jdbc)
        if (!command.requestedActive) {
            revalidateAdmin(jdbc, command)
        }
        val transition =
            transitionPolicy.decide(
                current = current,
                requestedActive = command.requestedActive,
                actorRole = command.actor.role,
            )
        when (transition) {
            KillSwitchTransition.ResumeRequiresAdmin -> throw KillSwitchForbiddenException()
            KillSwitchTransition.NoOp ->
                return KillSwitchMutationResult(
                    state = current.publicState(),
                    changed = false,
                    previousActive = current.active,
                    generation = current.generation,
                    invalidatedDecisionCount = 0,
                )

            is KillSwitchTransition.Applied ->
                require(command.reasonClass == transition.reasonClass) {
                    "Kill Switch reason class does not match the locked transition policy."
                }
        }

        val next =
            current.next(
                active = transition.nextActive,
                reasonClass = transition.reasonClass,
                changedAt = lockedDatabaseTime(jdbc, current),
            )
        val updateCount =
            jdbc.update(
                """
                UPDATE risk_kill_switch
                SET active = :active,
                    reason_class = :reasonClass,
                    generation = :nextGeneration,
                    changed_by = :changedBy,
                    changed_by_role = :changedByRole,
                    changed_at = :changedAt,
                    request_id = :requestId
                WHERE kill_switch_id = 'GLOBAL'
                  AND generation = :observedGeneration
                """.trimIndent(),
                mapOf(
                    "active" to next.active,
                    "reasonClass" to next.reasonClass.name,
                    "nextGeneration" to next.generation,
                    "changedBy" to command.actor.userId,
                    "changedByRole" to command.actor.role.name,
                    "changedAt" to next.changedAt.utc(),
                    "requestId" to command.actor.requestId,
                    "observedGeneration" to current.generation,
                ),
            )
        if (updateCount != 1) {
            throw KillSwitchConflictException()
        }

        val invalidatedCount =
            if (next.active) {
                requireNotNull(
                    jdbc.queryForObject(
                        """
                        SELECT invalidate_unused_decisions_for_kill_switch(
                          :generation,
                          :changedAt,
                          :requestId
                        )
                        """.trimIndent(),
                        mapOf(
                            "generation" to next.generation,
                            "changedAt" to next.changedAt.utc(),
                            "requestId" to command.actor.requestId,
                        ),
                        Int::class.java,
                    ),
                )
            } else {
                0
            }

        appendTransition(jdbc, command, current, next, invalidatedCount)
        appendAudit(jdbc, command, current, next, invalidatedCount)
        appendOutbox(jdbc, next)
        return KillSwitchMutationResult(
            state = next.publicState(),
            changed = true,
            previousActive = current.active,
            generation = next.generation,
            invalidatedDecisionCount = invalidatedCount,
        )
    }

    private fun lockCurrent(jdbc: NamedParameterJdbcTemplate): KillSwitchState =
        jdbc
            .query(
                """
                SELECT active, reason_class, generation, changed_at
                FROM risk_kill_switch
                WHERE kill_switch_id = 'GLOBAL'
                FOR UPDATE
                """.trimIndent(),
                emptyMap<String, Any>(),
            ) { result, _ ->
                KillSwitchState(
                    active = result.getBoolean("active"),
                    reasonClass = KillSwitchReasonClass.fromStored(result.getString("reason_class")),
                    generation = result.getLong("generation"),
                    changedAt = result.getObject("changed_at", OffsetDateTime::class.java).toInstant(),
                )
            }.single()

    // 잠금 대기와 clock rollback 뒤에도 안전 정지가 과거 시각 때문에 실패하지 않도록 DB 시각을 단조화한다.
    private fun lockedDatabaseTime(
        jdbc: NamedParameterJdbcTemplate,
        current: KillSwitchState,
    ): Instant =
        requireNotNull(
            jdbc.queryForObject(
                """
                SELECT GREATEST(
                  clock_timestamp(),
                  CAST(:currentChangedAt AS timestamptz)
                )
                """.trimIndent(),
                mapOf("currentChangedAt" to current.changedAt.utc()),
                OffsetDateTime::class.java,
            ),
        ).toInstant()

    private fun revalidateAdmin(
        jdbc: NamedParameterJdbcTemplate,
        command: KillSwitchMutationCommand,
    ) {
        val result =
            requireNotNull(
                jdbc.queryForObject(
                    """
                    SELECT revalidate_kill_switch_admin(
                      :actorUserId,
                      :securityVersion
                    )
                    """.trimIndent(),
                    mapOf(
                        "actorUserId" to command.actor.userId,
                        "securityVersion" to command.actor.securityVersion,
                    ),
                    String::class.java,
                ),
            )
        when (result) {
            "AUTHORIZED" ->
                if (command.actor.role.name != "ADMIN") {
                    throw KillSwitchForbiddenException()
                }

            "UNAUTHORIZED" -> throw KillSwitchUnauthorizedException()
            "FORBIDDEN" -> throw KillSwitchForbiddenException()
            else -> error("Kill Switch actor revalidation returned an invalid result.")
        }
    }

    private fun appendTransition(
        jdbc: NamedParameterJdbcTemplate,
        command: KillSwitchMutationCommand,
        current: KillSwitchState,
        next: KillSwitchState,
        invalidatedCount: Int,
    ) {
        jdbc.update(
            """
            INSERT INTO risk_kill_switch_transitions (
              transition_id,
              generation,
              previous_active,
              next_active,
              reason_class,
              changed_by,
              changed_by_role,
              changed_at,
              request_id,
              invalidated_decision_count
            )
            VALUES (
              :transitionId,
              :generation,
              :previousActive,
              :nextActive,
              :reasonClass,
              :changedBy,
              :changedByRole,
              :changedAt,
              :requestId,
              :invalidatedCount
            )
            """.trimIndent(),
            mutationParameters(command, current, next, invalidatedCount) +
                ("transitionId" to id("kst")),
        )
    }

    private fun appendAudit(
        jdbc: NamedParameterJdbcTemplate,
        command: KillSwitchMutationCommand,
        current: KillSwitchState,
        next: KillSwitchState,
        invalidatedCount: Int,
    ) {
        val payload =
            linkedMapOf(
                "generation" to next.generation,
                "previousActive" to current.active,
                "nextActive" to next.active,
                "reasonClass" to next.reasonClass.name,
                "changedBy" to command.actor.userId,
                "changedByRole" to command.actor.role.name,
                "correlationId" to command.actor.requestId,
                "invalidatedDecisionCount" to invalidatedCount,
            )
        jdbc.update(
            """
            INSERT INTO audit_logs (
              audit_log_id,
              user_id,
              actor_role,
              action,
              target_type,
              target_id,
              request_id,
              payload_json,
              created_at
            )
            VALUES (
              :auditId,
              :changedBy,
              :changedByRole,
              'KILL_SWITCH_CHANGED',
              'KILL_SWITCH',
              'GLOBAL',
              :requestId,
              CAST(:payloadJson AS jsonb),
              :changedAt
            )
            """.trimIndent(),
            mapOf(
                "auditId" to id("aud"),
                "changedBy" to command.actor.userId,
                "changedByRole" to command.actor.role.name,
                "requestId" to command.actor.requestId,
                "payloadJson" to objectMapper.writeValueAsString(payload),
                "changedAt" to next.changedAt.utc(),
            ),
        )
    }

    private fun appendOutbox(
        jdbc: NamedParameterJdbcTemplate,
        next: KillSwitchState,
    ) {
        val payload =
            linkedMapOf(
                "active" to next.active,
                "changedAt" to next.changedAt.toString(),
            )
        jdbc.update(
            """
            INSERT INTO event_outbox (
              event_id,
              event_type,
              aggregate_type,
              aggregate_id,
              partition_key,
              payload_json,
              schema_version,
              status,
              retry_count,
              created_at,
              updated_at
            )
            VALUES (
              :eventId,
              'kill-switch.changed',
              'KILL_SWITCH',
              'GLOBAL',
              'GLOBAL',
              CAST(:payloadJson AS jsonb),
              '1.0.0',
              'PENDING',
              0,
              :changedAt,
              :changedAt
            )
            """.trimIndent(),
            mapOf(
                "eventId" to id("evt"),
                "payloadJson" to objectMapper.writeValueAsString(payload),
                "changedAt" to next.changedAt.utc(),
            ),
        )
    }

    private fun mutationParameters(
        command: KillSwitchMutationCommand,
        current: KillSwitchState,
        next: KillSwitchState,
        invalidatedCount: Int,
    ): Map<String, Any> =
        mapOf(
            "generation" to next.generation,
            "previousActive" to current.active,
            "nextActive" to next.active,
            "reasonClass" to next.reasonClass.name,
            "changedBy" to command.actor.userId,
            "changedByRole" to command.actor.role.name,
            "changedAt" to next.changedAt.utc(),
            "requestId" to command.actor.requestId,
            "invalidatedCount" to invalidatedCount,
        )

    private fun KillSwitchState.publicState(): KillSwitchPublicState =
        KillSwitchPublicState(
            active = active,
            reasonClass = reasonClass,
            changedAt = changedAt,
        )

    private fun Instant.utc(): OffsetDateTime = atOffset(ZoneOffset.UTC)

    private fun id(prefix: String): String = "${prefix}_${UUID.randomUUID().toString().replace("-", "")}"

    private fun jdbc(): NamedParameterJdbcTemplate =
        jdbcProvider.getIfAvailable()
            ?: error("Kill Switch JDBC access is unavailable without a configured DataSource.")
}
