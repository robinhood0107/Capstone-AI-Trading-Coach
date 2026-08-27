package com.capstone.decision.infrastructure.automation

import com.capstone.decision.application.automation.ArmAutomationCommand
import com.capstone.decision.application.automation.AutomationAccessDeniedException
import com.capstone.decision.application.automation.AutomationConflictException
import com.capstone.decision.application.automation.AutomationControlProjection
import com.capstone.decision.application.automation.AutomationIdempotencyConflictException
import com.capstone.decision.application.automation.AutomationNotFoundException
import com.capstone.decision.application.automation.AutomationRepository
import com.capstone.decision.application.automation.AutomationRunCursor
import com.capstone.decision.application.automation.AutomationRunProjection
import com.capstone.decision.application.automation.AutomationStorageException
import com.capstone.decision.application.automation.DisarmAutomationCommand
import com.capstone.decision.infrastructure.security.ActorCapabilityBinding
import com.capstone.decision.infrastructure.security.ActorCapabilityDeniedException
import com.capstone.decision.infrastructure.security.ActorCapabilityRolePolicy
import com.capstone.decision.infrastructure.security.ActorRlsScope
import org.springframework.beans.factory.ObjectProvider
import org.springframework.dao.DataAccessException
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Transactional
import tools.jackson.databind.ObjectMapper
import java.sql.SQLException
import java.time.LocalDate
import java.time.OffsetDateTime

/** V89 RLS와 capability를 같은 transaction에서 열고 owner predicate가 없는 query를 노출하지 않는다. */
@Repository
class JdbcAutomationRepository(
    private val jdbcProvider: ObjectProvider<NamedParameterJdbcTemplate>,
    private val actorRlsScope: ActorRlsScope,
    private val objectMapper: ObjectMapper,
) : AutomationRepository {
    @Transactional
    override fun status(ownerUserId: String): AutomationControlProjection {
        try {
            val jdbc = jdbc()
            actorRlsScope.open(
                jdbc,
                ownerUserId,
                ActorCapabilityBinding.target(
                    "READ_AUTOMATION_STATUS",
                    "AUTOMATION",
                    ownerUserId,
                    ActorCapabilityRolePolicy.OWNER,
                ),
            )
            val row =
                jdbc
                    .query(
                        """
                        SELECT control_state,version,brokerage_mode,principle_id,strategy_id,
                               COALESCE(gate.certification_status,control.certification_status) certification_status,
                               COALESCE((SELECT active FROM public.read_kill_switch_gate()),true) kill_switch_active,
                               EXISTS (
                                 SELECT 1 FROM automation_runs run
                                 WHERE run.user_id=:ownerUserId AND run.state NOT IN (
                                   'NEWS_VETOED','CANCELLED_UNFILLED','COMPLETED','SKIPPED_NO_ACTION',
                                   'SKIPPED_DATA_UNAVAILABLE','SKIPPED_LATE_START','HALTED'
                                 )
                               ) active_run
                        FROM automation_control control
                        LEFT JOIN automation_activation_gate gate USING (user_id)
                        WHERE control.user_id=:ownerUserId
                        """.trimIndent(),
                        mapOf("ownerUserId" to ownerUserId),
                    ) { result, _ ->
                        val state = result.getString("control_state")
                        AutomationControlProjection(
                            controlState = state,
                            projectionState =
                                when {
                                    state == "HALTED" -> "HALTED"
                                    result.getBoolean("active_run") -> "RUNNING"
                                    else -> state
                                },
                            version = result.getInt("version"),
                            brokerageMode = result.getString("brokerage_mode"),
                            principleId = result.getString("principle_id"),
                            strategyId = result.getString("strategy_id"),
                            killSwitchActive = result.getBoolean("kill_switch_active"),
                            certificationStatus = result.getString("certification_status"),
                        )
                    }.singleOrNull()
            return row ?: defaultProjection(jdbc)
        } catch (error: ActorCapabilityDeniedException) {
            throw AutomationAccessDeniedException(error)
        } catch (error: DataAccessException) {
            throw translate(error)
        }
    }

    @Transactional
    override fun arm(
        ownerUserId: String,
        command: ArmAutomationCommand,
        scopeHash: String,
        requestHash: String,
    ): AutomationControlProjection =
        mutate(ownerUserId, "ARM_AUTOMATION", requestHash) { jdbc ->
            jdbc
                .query(
                    """
                    SELECT result_json,replayed FROM p1_arm_automation_v1(
                      :ownerUserId,:brokerageMode,:accountId,:principleId,:strategyId,
                      :expectedVersion,:scopeHash,:requestHash
                    )
                    """.trimIndent(),
                    mapOf(
                        "ownerUserId" to ownerUserId,
                        "brokerageMode" to command.brokerageMode,
                        "accountId" to command.accountId,
                        "principleId" to command.principleId,
                        "strategyId" to command.strategyId,
                        "expectedVersion" to command.expectedVersion,
                        "scopeHash" to scopeHash,
                        "requestHash" to requestHash,
                    ),
                ) { result, _ -> decodeControl(result.getString("result_json")) }
                .single()
        }

    @Transactional
    override fun disarm(
        ownerUserId: String,
        command: DisarmAutomationCommand,
        scopeHash: String,
        requestHash: String,
    ): AutomationControlProjection =
        mutate(ownerUserId, "DISARM_AUTOMATION", requestHash) { jdbc ->
            jdbc
                .query(
                    """
                    SELECT result_json,replayed FROM p1_disarm_automation_v1(
                      :ownerUserId,:expectedVersion,:scopeHash,:requestHash
                    )
                    """.trimIndent(),
                    mapOf(
                        "ownerUserId" to ownerUserId,
                        "expectedVersion" to command.expectedVersion,
                        "scopeHash" to scopeHash,
                        "requestHash" to requestHash,
                    ),
                ) { result, _ -> decodeControl(result.getString("result_json")) }
                .single()
        }

    @Transactional
    override fun listRuns(
        ownerUserId: String,
        limit: Int,
        after: AutomationRunCursor?,
    ): List<AutomationRunProjection> {
        try {
            val jdbc = jdbc()
            actorRlsScope.open(
                jdbc,
                ownerUserId,
                ActorCapabilityBinding.request(
                    "LIST_AUTOMATION_RUNS",
                    "AUTOMATION_RUN_LIST",
                    ownerUserId,
                    ActorCapabilityRolePolicy.OWNER,
                    ownerUserId,
                    limit.toString(),
                    after?.updatedAt?.toString(),
                    after?.runId,
                ),
            )
            val parameters =
                MapSqlParameterSource()
                    .addValue("ownerUserId", ownerUserId)
                    .addValue("limit", limit)
                    .addValue("afterUpdatedAt", after?.updatedAt)
                    .addValue("afterRunId", after?.runId)
            return jdbc.query(
                """
                SELECT run_id,session_date,state,brokerage_mode,selected_symbol,selected_side,
                       physical_submit_count,vertex_call_count,provider_calls,started_at,updated_at
                FROM automation_runs
                WHERE user_id=:ownerUserId
                  AND (CAST(:afterUpdatedAt AS timestamptz) IS NULL OR (updated_at,run_id)<(:afterUpdatedAt,:afterRunId))
                ORDER BY updated_at DESC,run_id DESC
                LIMIT :limit
                """.trimIndent(),
                parameters,
            ) { result, _ ->
                AutomationRunProjection(
                    runId = result.getString("run_id"),
                    sessionDate = result.getObject("session_date", LocalDate::class.java),
                    state = result.getString("state"),
                    brokerageMode = result.getString("brokerage_mode"),
                    selectedSymbol = result.getString("selected_symbol"),
                    selectedSide = result.getString("selected_side"),
                    physicalSubmitCount = result.getInt("physical_submit_count"),
                    vertexCallCount = result.getInt("vertex_call_count"),
                    providerCalls = result.getInt("provider_calls"),
                    startedAt = result.getObject("started_at", OffsetDateTime::class.java),
                    updatedAt = result.getObject("updated_at", OffsetDateTime::class.java),
                )
            }
        } catch (error: ActorCapabilityDeniedException) {
            throw AutomationAccessDeniedException(error)
        } catch (error: DataAccessException) {
            throw translate(error)
        }
    }

    private fun <T> mutate(
        ownerUserId: String,
        operation: String,
        requestHash: String,
        block: (NamedParameterJdbcTemplate) -> T,
    ): T =
        try {
            val jdbc = jdbc()
            actorRlsScope.open(
                jdbc,
                ownerUserId,
                ActorCapabilityBinding(
                    operation = operation,
                    targetKind = "AUTOMATION",
                    targetId = ownerUserId,
                    payloadHash = requestHash,
                    rolePolicy = ActorCapabilityRolePolicy.OWNER,
                ),
            )
            block(jdbc)
        } catch (error: ActorCapabilityDeniedException) {
            throw AutomationAccessDeniedException(error)
        } catch (error: DataAccessException) {
            throw translate(error)
        }

    private fun decodeControl(json: String): AutomationControlProjection =
        objectMapper.readValue(json, AutomationControlProjection::class.java)

    private fun defaultProjection(jdbc: NamedParameterJdbcTemplate): AutomationControlProjection {
        val active =
            jdbc.queryForObject(
                "SELECT COALESCE((SELECT active FROM public.read_kill_switch_gate()),true)",
                emptyMap<String, Any>(),
                Boolean::class.java,
            ) ?: true
        return AutomationControlProjection(
            controlState = "DISARMED",
            projectionState = "DISARMED",
            version = 1,
            brokerageMode = "INTERNAL_PAPER",
            principleId = "prc_00000000",
            strategyId = "strategy_00000000",
            killSwitchActive = active,
            certificationStatus = "NOT_REQUIRED_INTERNAL_PAPER",
        )
    }

    private fun translate(error: DataAccessException): RuntimeException =
        when (error.sqlState()) {
            "23505" -> AutomationIdempotencyConflictException()
            "40001" -> AutomationConflictException(error)
            "P0002" -> AutomationNotFoundException()
            "42501" -> AutomationAccessDeniedException(error)
            else -> AutomationStorageException(error)
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
            ?: throw AutomationStorageException(IllegalStateException("Automation JDBC is unavailable."))
}
