package com.capstone.decision.infrastructure.automation

import com.capstone.decision.application.automation.ArmAutomationCommand
import com.capstone.decision.application.automation.ArmAutomationV2Command
import com.capstone.decision.application.automation.ArmAutomationV3Command
import com.capstone.decision.application.automation.AutomationAccessDeniedException
import com.capstone.decision.application.automation.AutomationBlockedException
import com.capstone.decision.application.automation.AutomationCandidateEvidenceV3Projection
import com.capstone.decision.application.automation.AutomationCandidateScreeningV3Projection
import com.capstone.decision.application.automation.AutomationConflictException
import com.capstone.decision.application.automation.AutomationControlProjection
import com.capstone.decision.application.automation.AutomationIdempotencyConflictException
import com.capstone.decision.application.automation.AutomationNotFoundException
import com.capstone.decision.application.automation.AutomationPolicyV2Projection
import com.capstone.decision.application.automation.AutomationPolicyV3Projection
import com.capstone.decision.application.automation.AutomationPositionV2Page
import com.capstone.decision.application.automation.AutomationPositionV2Projection
import com.capstone.decision.application.automation.AutomationPositionV3Projection
import com.capstone.decision.application.automation.AutomationRealizedPerformanceV2Projection
import com.capstone.decision.application.automation.AutomationRepository
import com.capstone.decision.application.automation.AutomationRunCursor
import com.capstone.decision.application.automation.AutomationRunDetailV3Projection
import com.capstone.decision.application.automation.AutomationRunProjection
import com.capstone.decision.application.automation.AutomationRunV2Projection
import com.capstone.decision.application.automation.AutomationRunV3Projection
import com.capstone.decision.application.automation.AutomationStatusV2Projection
import com.capstone.decision.application.automation.AutomationStatusV3Projection
import com.capstone.decision.application.automation.AutomationStorageException
import com.capstone.decision.application.automation.DisarmAutomationCommand
import com.capstone.decision.application.automation.PutAutomationPolicyV2Command
import com.capstone.decision.application.automation.PutAutomationPolicyV3Command
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
import java.sql.ResultSet
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

    @Transactional
    override fun statusV2(ownerUserId: String): AutomationStatusV2Projection {
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
            return readStatusV2(jdbc, ownerUserId)
        } catch (error: ActorCapabilityDeniedException) {
            throw AutomationAccessDeniedException(error)
        } catch (error: DataAccessException) {
            throw translate(error)
        }
    }

    @Transactional
    override fun putPolicyV2(
        ownerUserId: String,
        command: PutAutomationPolicyV2Command,
        scopeHash: String,
        requestHash: String,
    ): AutomationPolicyV2Projection =
        mutate(ownerUserId, "PUT_AUTOMATION_POLICY", requestHash, "AUTOMATION_POLICY") { jdbc ->
            val principleId =
                jdbc.queryForObject(
                    """
                    SELECT active_principle_id
                    FROM p1_automation_status_facts_v2(:ownerUserId,NULL::text)
                    """.trimIndent(),
                    mapOf("ownerUserId" to ownerUserId),
                    String::class.java,
                ) ?: throw AutomationNotFoundException()
            val json =
                jdbc.queryForObject(
                    """
                    SELECT result_json FROM p1_put_automation_policy_v1(
                      :ownerUserId,:principleId,:capitalLimitKrw,:stopLossBps,:takeProfitBps,
                      :expectedVersion,:scopeHash,:requestHash
                    )
                    """.trimIndent(),
                    mapOf(
                        "ownerUserId" to ownerUserId,
                        "principleId" to principleId,
                        "capitalLimitKrw" to command.capitalLimitKrw,
                        "stopLossBps" to command.stopLossBps,
                        "takeProfitBps" to command.takeProfitBps,
                        "expectedVersion" to command.expectedVersion,
                        "scopeHash" to scopeHash,
                        "requestHash" to requestHash,
                    ),
                    String::class.java,
                ) ?: throw AutomationStorageException(IllegalStateException("Policy function returned no result."))
            decodePolicy(json)
        }

    @Transactional
    override fun armV2(
        ownerUserId: String,
        command: ArmAutomationV2Command,
        scopeHash: String,
        requestHash: String,
    ): AutomationStatusV2Projection =
        mutate(ownerUserId, "ARM_AUTOMATION", requestHash) { jdbc ->
            jdbc.queryForObject(
                """
                SELECT result_json FROM p1_arm_automation_v2(
                  :ownerUserId,:accountId,:policyId,:expectedPolicyVersion,
                  :expectedControlVersion,:scopeHash,:requestHash
                )
                """.trimIndent(),
                mapOf(
                    "ownerUserId" to ownerUserId,
                    "accountId" to command.accountId,
                    "policyId" to command.policyId,
                    "expectedPolicyVersion" to command.expectedPolicyVersion,
                    "expectedControlVersion" to command.expectedControlVersion,
                    "scopeHash" to scopeHash,
                    "requestHash" to requestHash,
                ),
                String::class.java,
            ) ?: throw AutomationStorageException(IllegalStateException("V2 arm function returned no result."))
            readStatusV2(jdbc, ownerUserId)
        }

    @Transactional
    override fun listRunsV2(
        ownerUserId: String,
        limit: Int,
        after: AutomationRunCursor?,
    ): List<AutomationRunV2Projection> {
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
            return jdbc.query(
                """
                SELECT run.run_id,run.session_date,run.state,run.brokerage_mode,
                       run.selected_symbol,run.selected_side,run.policy_id,run.policy_version,
                       reservation.quantity,reservation.filled_quantity,reservation.leaves_quantity,
                       reservation.limit_price_krw,reservation.estimated_amount_krw,reservation.exit_reason,
                       run.physical_submit_count,run.provider_calls,run.started_at,run.updated_at
                FROM automation_runs run
                LEFT JOIN automation_order_reservations reservation ON reservation.run_id=run.run_id
                WHERE run.user_id=:ownerUserId
                  AND (CAST(:afterUpdatedAt AS timestamptz) IS NULL OR
                    (run.updated_at,run.run_id)<(:afterUpdatedAt,:afterRunId))
                ORDER BY run.updated_at DESC,run.run_id DESC LIMIT :limit
                """.trimIndent(),
                MapSqlParameterSource()
                    .addValue("ownerUserId", ownerUserId)
                    .addValue("limit", limit)
                    .addValue("afterUpdatedAt", after?.updatedAt)
                    .addValue("afterRunId", after?.runId),
            ) { row, _ ->
                AutomationRunV2Projection(
                    runId = row.getString("run_id"),
                    sessionDate = row.getObject("session_date", LocalDate::class.java),
                    state = row.getString("state"),
                    brokerageMode = row.getString("brokerage_mode"),
                    selectedSymbol = row.getString("selected_symbol"),
                    selectedSide = row.getString("selected_side"),
                    policyId = row.getString("policy_id"),
                    policyVersion = row.intOrNull("policy_version"),
                    orderQuantity = row.longOrNull("quantity"),
                    filledQuantity = row.longOrNull("filled_quantity"),
                    leavesQuantity = row.longOrNull("leaves_quantity"),
                    limitPriceKrw = row.longOrNull("limit_price_krw"),
                    estimatedAmountKrw = row.longOrNull("estimated_amount_krw"),
                    exitReason = row.getString("exit_reason"),
                    physicalSubmitCount = row.getInt("physical_submit_count"),
                    providerCalls = row.getInt("provider_calls"),
                    startedAt = row.getObject("started_at", OffsetDateTime::class.java),
                    updatedAt = row.getObject("updated_at", OffsetDateTime::class.java),
                )
            }
        } catch (error: ActorCapabilityDeniedException) {
            throw AutomationAccessDeniedException(error)
        } catch (error: DataAccessException) {
            throw translate(error)
        }
    }

    @Transactional
    override fun readPositionPageV2(ownerUserId: String): AutomationPositionV2Page {
        try {
            val jdbc = jdbc()
            actorRlsScope.open(
                jdbc,
                ownerUserId,
                ActorCapabilityBinding.target(
                    "LIST_AUTOMATION_POSITIONS",
                    "AUTOMATION_POSITION_LIST",
                    ownerUserId,
                    ActorCapabilityRolePolicy.OWNER,
                ),
            )
            // actor capability scope는 요청당 한 번만 연다. 두 번 열면 두 번째 질의가 403이 된다.
            val summary =
                jdbc.query(
                    "SELECT * FROM p1_automation_realized_performance_v2(:ownerUserId)",
                    mapOf("ownerUserId" to ownerUserId),
                ) { row, _ ->
                    AutomationRealizedPerformanceV2Projection(
                        closedPositionCount = row.getLong("closed_position_count"),
                        realizedPnlKrw = row.getLong("realized_pnl_krw"),
                        realizedGrossKrw = row.getLong("realized_gross_krw"),
                        winningPositionCount = row.getLong("winning_position_count"),
                        losingPositionCount = row.getLong("losing_position_count"),
                    )
                }
            val items =
                jdbc.query(
                    """
                    (
                      SELECT position_id,account_id,symbol,quantity,entry_average_fill_price_krw,
                             entry_session,expiry_session,policy_id,policy_version,stop_loss_bps,
                             take_profit_bps,status,exit_reason,exit_average_fill_price_krw,
                             realized_pnl_krw,bot_owned,short_allowed,created_at,closed_at
                      FROM automation_positions
                      WHERE user_id=:ownerUserId AND policy_id IS NOT NULL
                        AND account_id=(
                          SELECT control.account_id FROM automation_control control
                          WHERE control.user_id=:ownerUserId AND control.brokerage_mode='KIS_MOCK'
                          LIMIT 1
                        )
                        AND max_holding_sessions IS NULL
                        AND status IN ('OPEN','EXIT_PENDING')
                      ORDER BY entry_session,symbol,position_id LIMIT 5
                    )
                    UNION ALL
                    (
                      SELECT position_id,account_id,symbol,quantity,entry_average_fill_price_krw,
                             entry_session,expiry_session,policy_id,policy_version,stop_loss_bps,
                             take_profit_bps,status,exit_reason,exit_average_fill_price_krw,
                             realized_pnl_krw,bot_owned,short_allowed,created_at,closed_at
                      FROM automation_positions
                      WHERE user_id=:ownerUserId AND policy_id IS NOT NULL
                        AND account_id=(
                          SELECT control.account_id FROM automation_control control
                          WHERE control.user_id=:ownerUserId AND control.brokerage_mode='KIS_MOCK'
                          LIMIT 1
                        )
                        AND max_holding_sessions IS NULL AND status='CLOSED'
                      ORDER BY closed_at DESC,position_id LIMIT 5
                    )
                    """.trimIndent(),
                    mapOf("ownerUserId" to ownerUserId),
                ) { row, _ ->
                    AutomationPositionV2Projection(
                        positionId = row.getString("position_id"),
                        accountId = row.getString("account_id"),
                        symbol = row.getString("symbol"),
                        quantity = row.getLong("quantity"),
                        entryAverageFillPriceKrw = row.getLong("entry_average_fill_price_krw"),
                        entrySession = row.getObject("entry_session", LocalDate::class.java),
                        expirySession = row.getObject("expiry_session", LocalDate::class.java),
                        policyId = row.getString("policy_id"),
                        policyVersion = row.getInt("policy_version"),
                        stopLossBps = row.getInt("stop_loss_bps"),
                        takeProfitBps = row.getInt("take_profit_bps"),
                        status = row.getString("status"),
                        exitReason = row.getString("exit_reason"),
                        exitAverageFillPriceKrw =
                            row
                                .getObject("exit_average_fill_price_krw", java.lang.Long::class.java)
                                ?.toLong(),
                        realizedPnlKrw =
                            row.getObject("realized_pnl_krw", java.lang.Long::class.java)?.toLong(),
                        botOwned = row.getBoolean("bot_owned"),
                        shortAllowed = row.getBoolean("short_allowed"),
                        createdAt = row.getObject("created_at", OffsetDateTime::class.java),
                        closedAt = row.getObject("closed_at", OffsetDateTime::class.java),
                    )
                }
            return AutomationPositionV2Page(summary.single(), items, null)
        } catch (error: ActorCapabilityDeniedException) {
            throw AutomationAccessDeniedException(error)
        } catch (error: DataAccessException) {
            throw translate(error)
        }
    }

    @Transactional
    override fun statusV3(ownerUserId: String): AutomationStatusV3Projection {
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
            return readStatusV3(jdbc, ownerUserId)
        } catch (error: ActorCapabilityDeniedException) {
            throw AutomationAccessDeniedException(error)
        } catch (error: DataAccessException) {
            throw translate(error)
        }
    }

    @Transactional
    override fun putPolicyV3(
        ownerUserId: String,
        command: PutAutomationPolicyV3Command,
        scopeHash: String,
        requestHash: String,
    ): AutomationPolicyV3Projection =
        mutate(ownerUserId, "PUT_AUTOMATION_POLICY", requestHash, "AUTOMATION_POLICY") { jdbc ->
            val principleId =
                jdbc.queryForObject(
                    "SELECT active_principle_id FROM p1_automation_status_facts_v2(:ownerUserId,NULL::text)",
                    mapOf("ownerUserId" to ownerUserId),
                    String::class.java,
                ) ?: throw AutomationNotFoundException()
            val json =
                jdbc.queryForObject(
                    """
                    SELECT result_json FROM p1_put_automation_policy_v2(
                      :ownerUserId,:principleId,:capitalLimitKrw,:stopLossBps,:takeProfitBps,
                      :maxHoldingSessions,:atrPeriod,:atrMultiplierMilli,:modelSellEnabled,
                      :expectedVersion,:scopeHash,:requestHash
                    )
                    """.trimIndent(),
                    mapOf(
                        "ownerUserId" to ownerUserId,
                        "principleId" to principleId,
                        "capitalLimitKrw" to command.capitalLimitKrw,
                        "stopLossBps" to command.stopLossBps,
                        "takeProfitBps" to command.takeProfitBps,
                        "maxHoldingSessions" to command.maxHoldingSessions,
                        "atrPeriod" to command.atrPeriod,
                        "atrMultiplierMilli" to command.atrMultiplierMilli,
                        "modelSellEnabled" to command.modelSellEnabled,
                        "expectedVersion" to command.expectedVersion,
                        "scopeHash" to scopeHash,
                        "requestHash" to requestHash,
                    ),
                    String::class.java,
                ) ?: throw AutomationStorageException(IllegalStateException("V3 policy function returned no result."))
            decodePolicyV3(json)
        }

    @Transactional
    override fun armV3(
        ownerUserId: String,
        command: ArmAutomationV3Command,
        scopeHash: String,
        requestHash: String,
        providerCapabilityReady: Boolean,
    ): AutomationStatusV3Projection =
        mutate(ownerUserId, "ARM_AUTOMATION", requestHash) { jdbc ->
            jdbc.queryForObject(
                """
                SELECT result_json FROM p1_arm_automation_v3(
                  :ownerUserId,:accountId,:policyId,:expectedPolicyVersion,
                  :expectedControlVersion,:scopeHash,:requestHash,:providerCapabilityReady
                )
                """.trimIndent(),
                mapOf(
                    "ownerUserId" to ownerUserId,
                    "accountId" to command.accountId,
                    "policyId" to command.policyId,
                    "expectedPolicyVersion" to command.expectedPolicyVersion,
                    "expectedControlVersion" to command.expectedControlVersion,
                    "scopeHash" to scopeHash,
                    "requestHash" to requestHash,
                    "providerCapabilityReady" to providerCapabilityReady,
                ),
                String::class.java,
            ) ?: throw AutomationStorageException(IllegalStateException("V3 arm function returned no result."))
            readStatusV3(jdbc, ownerUserId)
        }

    @Transactional
    override fun listRunsV3(
        ownerUserId: String,
        limit: Int,
        after: AutomationRunCursor?,
    ): List<AutomationRunV3Projection> {
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
            return jdbc.query(
                RUN_V3_SELECT +
                    """
                    WHERE run.user_id=:ownerUserId
                      AND (CAST(:afterUpdatedAt AS timestamptz) IS NULL OR
                        (run.updated_at,run.run_id)<(:afterUpdatedAt,:afterRunId))
                    ORDER BY run.updated_at DESC,run.run_id DESC LIMIT :limit
                    """.trimIndent(),
                MapSqlParameterSource()
                    .addValue("ownerUserId", ownerUserId)
                    .addValue("limit", limit)
                    .addValue("afterUpdatedAt", after?.updatedAt)
                    .addValue("afterRunId", after?.runId),
            ) { row, _ -> row.toRunV3() }
        } catch (error: ActorCapabilityDeniedException) {
            throw AutomationAccessDeniedException(error)
        } catch (error: DataAccessException) {
            throw translate(error)
        }
    }

    @Transactional
    override fun readRunV3(
        ownerUserId: String,
        runId: String,
    ): AutomationRunDetailV3Projection {
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
                    runId,
                ),
            )
            val run =
                jdbc
                    .query(
                        RUN_V3_SELECT + " WHERE run.user_id=:ownerUserId AND run.run_id=:runId",
                        mapOf("ownerUserId" to ownerUserId, "runId" to runId),
                    ) { row, _ -> row.toRunV3() }
                    .singleOrNull() ?: throw AutomationNotFoundException()
            return AutomationRunDetailV3Projection(run, readCandidateScreeningsV3(jdbc, runId))
        } catch (error: ActorCapabilityDeniedException) {
            throw AutomationAccessDeniedException(error)
        } catch (error: DataAccessException) {
            throw translate(error)
        }
    }

    @Transactional
    override fun readPositionsV3(ownerUserId: String): List<AutomationPositionV3Projection> {
        try {
            val jdbc = jdbc()
            actorRlsScope.open(
                jdbc,
                ownerUserId,
                ActorCapabilityBinding.target(
                    "LIST_AUTOMATION_POSITIONS",
                    "AUTOMATION_POSITION_LIST",
                    ownerUserId,
                    ActorCapabilityRolePolicy.OWNER,
                ),
            )
            return jdbc.query(
                """
                SELECT position_id,account_id,symbol,quantity,entry_average_fill_price_krw,
                       entry_session,expiry_session,policy_id,policy_version,stop_loss_bps,
                       take_profit_bps,max_holding_sessions,atr_period,atr_multiplier_milli,
                       model_sell_enabled,peak_price_krw,atr_as_of_session,trailing_stop_krw,
                       status,exit_reason,bot_owned,short_allowed,created_at,closed_at
                FROM automation_positions
                WHERE user_id=:ownerUserId AND max_holding_sessions IS NOT NULL
                  AND status IN ('OPEN','EXIT_PENDING')
                ORDER BY entry_session,symbol,position_id LIMIT 5
                """.trimIndent(),
                mapOf("ownerUserId" to ownerUserId),
            ) { row, _ ->
                AutomationPositionV3Projection(
                    positionId = row.getString("position_id"),
                    accountId = row.getString("account_id"),
                    symbol = row.getString("symbol"),
                    quantity = row.getLong("quantity"),
                    entryAverageFillPriceKrw = row.getLong("entry_average_fill_price_krw"),
                    entrySession = row.getObject("entry_session", LocalDate::class.java),
                    expirySession = row.getObject("expiry_session", LocalDate::class.java),
                    policyId = row.getString("policy_id"),
                    policyVersion = row.getInt("policy_version"),
                    stopLossBps = row.getInt("stop_loss_bps"),
                    takeProfitBps = row.getInt("take_profit_bps"),
                    maxHoldingSessions = row.getInt("max_holding_sessions"),
                    atrPeriod = row.getInt("atr_period"),
                    atrMultiplierMilli = row.getInt("atr_multiplier_milli"),
                    modelSellEnabled = row.getBoolean("model_sell_enabled"),
                    peakPriceKrw = row.getLong("peak_price_krw"),
                    atrAsOfSession = row.getObject("atr_as_of_session", LocalDate::class.java),
                    trailingStopKrw = row.longOrNull("trailing_stop_krw"),
                    status = row.getString("status"),
                    exitReason = row.getString("exit_reason"),
                    botOwned = row.getBoolean("bot_owned"),
                    shortAllowed = row.getBoolean("short_allowed"),
                    createdAt = row.getObject("created_at", OffsetDateTime::class.java),
                    closedAt = row.getObject("closed_at", OffsetDateTime::class.java),
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
        targetKind: String = "AUTOMATION",
        block: (NamedParameterJdbcTemplate) -> T,
    ): T =
        try {
            val jdbc = jdbc()
            actorRlsScope.open(
                jdbc,
                ownerUserId,
                ActorCapabilityBinding(
                    operation = operation,
                    targetKind = targetKind,
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

    private fun readStatusV2(
        jdbc: NamedParameterJdbcTemplate,
        ownerUserId: String,
    ): AutomationStatusV2Projection {
        val policy = readCurrentPolicyV2(jdbc, ownerUserId)
        val control =
            jdbc
                .query(
                    """
                    SELECT control_state,version,brokerage_mode,account_id,certification_status,
                           EXISTS (
                             SELECT 1 FROM automation_runs run
                             WHERE run.user_id=:ownerUserId AND run.state NOT IN (
                               'NEWS_VETOED','CANCELLED_UNFILLED','COMPLETED',
                               'SKIPPED_NO_ACTION','SKIPPED_DATA_UNAVAILABLE','SKIPPED_LATE_START','HALTED'
                             )
                           ) active_run,
                           facts.unresolved_reconciliation unresolved,
                           facts.principle_configured principle_configured,
                           (SELECT count(*) FROM automation_positions position
                            WHERE position.user_id=:ownerUserId AND position.account_id=control.account_id
                              AND position.status IN ('OPEN','EXIT_PENDING')) open_count,
                           EXISTS (
                             SELECT 1 FROM automation_activation_gate gate
                             WHERE gate.user_id=:ownerUserId AND gate.certification_status='VALID'
                           ) certification_ready,
                           EXISTS (
                             SELECT 1 FROM automation_activation_gate gate
                             WHERE gate.user_id=:ownerUserId AND gate.clean_release_binding
                           ) release_binding_clean,
                           EXISTS (
                             SELECT 1 FROM automation_activation_gate gate
                             WHERE gate.user_id=:ownerUserId AND gate.real_team_b_pointer_active
                           ) real_team_b_pointer_active,
                           control.policy_id policy_binding_id,
                           control.policy_version policy_binding_version
                    FROM automation_control control
                    CROSS JOIN LATERAL p1_automation_status_facts_v2(
                      :ownerUserId,control.account_id
                    ) facts
                    WHERE control.user_id=:ownerUserId AND control.brokerage_mode='KIS_MOCK'
                    """.trimIndent(),
                    mapOf("ownerUserId" to ownerUserId),
                ) { row, _ ->
                    StatusRow(
                        controlState = row.getString("control_state"),
                        version = row.getInt("version"),
                        brokerageMode = row.getString("brokerage_mode"),
                        accountId = row.getString("account_id"),
                        certificationStatus = row.getString("certification_status"),
                        activeRun = row.getBoolean("active_run"),
                        unresolved = row.getBoolean("unresolved"),
                        openCount = row.getInt("open_count"),
                        certificationReady = row.getBoolean("certification_ready"),
                        releaseBindingClean = row.getBoolean("release_binding_clean"),
                        realTeamBPointerActive = row.getBoolean("real_team_b_pointer_active"),
                        principleConfigured = row.getBoolean("principle_configured"),
                        policyBindingId = row.getString("policy_binding_id"),
                        policyBindingVersion = row.getInt("policy_binding_version").takeUnless { row.wasNull() },
                    )
                }.singleOrNull()
        val killSwitchActive =
            jdbc.queryForObject(
                "SELECT COALESCE((SELECT active FROM public.read_kill_switch_gate()),true)",
                emptyMap<String, Any>(),
                Boolean::class.java,
            ) ?: true
        val accountId = control?.accountId
        val riskBalanceReady =
            if (accountId == null) {
                false
            } else {
                jdbc.queryForObject(
                    "SELECT p1_automation_risk_balance_projection_v2(:ownerUserId,:accountId) IS NOT NULL",
                    mapOf("ownerUserId" to ownerUserId, "accountId" to accountId),
                    Boolean::class.java,
                ) ?: false
            }
        val blockers =
            buildList {
                if (accountId == null) add("ACCOUNT_NOT_CONFIGURED")
                if (policy == null) add("POLICY_NOT_CONFIGURED")
                val boundPolicyId = control?.policyBindingId
                if (boundPolicyId != null &&
                    (
                        boundPolicyId != policy?.policyId ||
                            control.policyBindingVersion != policy.version
                    )
                ) {
                    add("POLICY_VERSION_DRIFT")
                }
                if (control != null && !control.principleConfigured) add("PRINCIPLE_NOT_CONFIGURED")
                if (control != null && !control.realTeamBPointerActive) add("REAL_TEAM_B_POINTER_INACTIVE")
                if (control != null && !control.releaseBindingClean) add("RELEASE_BINDING_UNCLEAN")
                if (control != null && !control.certificationReady) add("CERTIFICATION_INVALID")
                if (killSwitchActive) add("KILL_SWITCH_ACTIVE")
                if (control?.unresolved == true) add("UNRESOLVED_RECONCILIATION")
                if (control?.controlState == "HALTED") add("CONTROL_HALTED")
                if (!riskBalanceReady) add("BLOCKED_INCOMPLETE_RISK_BALANCE")
            }
        val state = control?.controlState ?: "DISARMED"
        return AutomationStatusV2Projection(
            controlState = state,
            projectionState =
                when {
                    state == "HALTED" -> "HALTED"
                    control?.activeRun == true -> "RUNNING"
                    else -> state
                },
            controlVersion = control?.version ?: 1,
            brokerageMode = control?.brokerageMode ?: "KIS_MOCK",
            accountId = accountId,
            policy = policy,
            killSwitchActive = killSwitchActive,
            certificationStatus = control?.certificationStatus ?: "REQUIRED",
            openPositionCount = control?.openCount ?: 0,
            unresolvedReconciliation = control?.unresolved ?: false,
            canArm = state == "DISARMED" && blockers.isEmpty(),
            blockers = blockers,
        )
    }

    private fun readStatusV3(
        jdbc: NamedParameterJdbcTemplate,
        ownerUserId: String,
    ): AutomationStatusV3Projection {
        val base = readStatusV2(jdbc, ownerUserId)
        val policy = readCurrentPolicyV3(jdbc, ownerUserId)
        val marketHistoryStatus =
            jdbc.queryForObject(
                "SELECT p1_read_automation_market_history_status_owner_v1(:ownerUserId)",
                mapOf("ownerUserId" to ownerUserId),
                String::class.java,
            ) ?: "EMPTY"
        val counts =
            jdbc.queryForMap(
                """
                SELECT
                  count(*) FILTER (WHERE status IN ('OPEN','EXIT_PENDING')) active_count,
                  count(*) FILTER (WHERE status IN ('OPEN','EXIT_PENDING')
                    AND max_holding_sessions IS NULL) legacy_count
                FROM automation_positions WHERE user_id=:ownerUserId
                """.trimIndent(),
                mapOf("ownerUserId" to ownerUserId),
            )
        val activeCount = (counts["active_count"] as Number).toInt()
        val legacyCount = (counts["legacy_count"] as Number).toInt()
        val aiSettings =
            jdbc
                .query(
                    """
                    SELECT provider,ai_judgement_enabled,thinking_level,daily_generate_call_cap
                    FROM strong_llm_owner_settings WHERE owner_user_id=:ownerUserId
                    """.trimIndent(),
                    mapOf("ownerUserId" to ownerUserId),
                ) { row, _ ->
                    AutomationAiSettingsSnapshot(
                        provider = row.getString("provider"),
                        enabled = row.getBoolean("ai_judgement_enabled"),
                        thinkingLevel = row.getString("thinking_level"),
                        dailyGenerateCallCap = row.getInt("daily_generate_call_cap"),
                    )
                }.singleOrNull() ?: AutomationAiSettingsSnapshot("vertex", false, "low", 50)
        val primaryCredentialReady =
            jdbc
                .query(
                    "SELECT slot FROM read_strong_llm_owner_key_last4_v1(:ownerUserId)",
                    mapOf("ownerUserId" to ownerUserId),
                ) { row, _ -> row.getString("slot") }
                .contains("PRIMARY")
        val credentialReady = aiSettings.provider == "vertex" || primaryCredentialReady
        val aiProviderReady = !aiSettings.enabled || (credentialReady && aiSettings.dailyGenerateCallCap >= 3)
        val blockers =
            buildList {
                addAll(base.blockers)
                if (policy == null) add("POLICY_V3_NOT_CONFIGURED")
                if (legacyCount > 0) add("LEGACY_POSITION_PRESENT")
                if (marketHistoryStatus != "READY") add("MARKET_DATA_CATCHUP_REQUIRED")
                if (!aiProviderReady) add("AI_PROVIDER_NOT_READY")
            }.distinct()
        return AutomationStatusV3Projection(
            controlState = base.controlState,
            projectionState = base.projectionState,
            controlVersion = base.controlVersion,
            accountId = base.accountId,
            policy = policy,
            aiJudgementEnabled = aiSettings.enabled,
            thinkingLevel = aiSettings.thinkingLevel,
            marketHistoryStatus = marketHistoryStatus,
            killSwitchActive = base.killSwitchActive,
            certificationStatus = base.certificationStatus,
            openPositionCount = activeCount,
            legacyOpenPositionCount = legacyCount,
            unresolvedReconciliation = base.unresolvedReconciliation,
            canArm = base.controlState == "DISARMED" && blockers.isEmpty(),
            blockers = blockers,
        )
    }

    private fun readCurrentPolicyV2(
        jdbc: NamedParameterJdbcTemplate,
        ownerUserId: String,
    ): AutomationPolicyV2Projection? =
        jdbc
            .query(
                """
                SELECT policy_id,version,risk_profile,capital_limit_krw,stop_loss_bps,take_profit_bps,created_at
                FROM automation_policy_versions WHERE user_id=:ownerUserId
                ORDER BY version DESC LIMIT 1
                """.trimIndent(),
                mapOf("ownerUserId" to ownerUserId),
            ) { row, _ ->
                val createdAt = row.getObject("created_at", OffsetDateTime::class.java)
                AutomationPolicyV2Projection(
                    policyId = row.getString("policy_id"),
                    version = row.getInt("version"),
                    presetId = row.getString("risk_profile").lowercase(),
                    capitalLimitKrw = row.getLong("capital_limit_krw"),
                    stopLossBps = row.getInt("stop_loss_bps"),
                    takeProfitBps = row.getInt("take_profit_bps"),
                    createdAt = createdAt,
                    updatedAt = createdAt,
                )
            }.singleOrNull()

    private fun readCurrentPolicyV3(
        jdbc: NamedParameterJdbcTemplate,
        ownerUserId: String,
    ): AutomationPolicyV3Projection? =
        jdbc
            .query(
                """
                SELECT policy_id,version,risk_profile,capital_limit_krw,stop_loss_bps,
                       take_profit_bps,max_holding_sessions,atr_period,atr_multiplier_milli,
                       model_sell_enabled,created_at
                FROM automation_policy_versions
                WHERE user_id=:ownerUserId
                  AND version=(SELECT max(version) FROM automation_policy_versions WHERE user_id=:ownerUserId)
                  AND max_holding_sessions IS NOT NULL
                """.trimIndent(),
                mapOf("ownerUserId" to ownerUserId),
            ) { row, _ ->
                val timestamp = row.getObject("created_at", OffsetDateTime::class.java)
                AutomationPolicyV3Projection(
                    policyId = row.getString("policy_id"),
                    version = row.getInt("version"),
                    presetId = row.getString("risk_profile").lowercase(),
                    capitalLimitKrw = row.getLong("capital_limit_krw"),
                    stopLossBps = row.getInt("stop_loss_bps"),
                    takeProfitBps = row.getInt("take_profit_bps"),
                    maxHoldingSessions = row.getInt("max_holding_sessions"),
                    atrPeriod = row.getInt("atr_period"),
                    atrMultiplierMilli = row.getInt("atr_multiplier_milli"),
                    modelSellEnabled = row.getBoolean("model_sell_enabled"),
                    createdAt = timestamp,
                    updatedAt = timestamp,
                )
            }.singleOrNull()

    private fun decodePolicy(json: String): AutomationPolicyV2Projection {
        val node = objectMapper.readTree(json)
        val timestamp = OffsetDateTime.parse(node.path("updatedAt").stringValue())
        return AutomationPolicyV2Projection(
            policyId = node.path("policyId").stringValue(),
            version = node.path("version").intValue(),
            presetId = node.path("riskProfile").stringValue().lowercase(),
            capitalLimitKrw = node.path("capitalLimitKrw").longValue(),
            stopLossBps = node.path("stopLossBps").intValue(),
            takeProfitBps = node.path("takeProfitBps").intValue(),
            createdAt = timestamp,
            updatedAt = timestamp,
        )
    }

    private fun decodePolicyV3(json: String): AutomationPolicyV3Projection {
        val node = objectMapper.readTree(json)
        return AutomationPolicyV3Projection(
            policyId = node.path("policyId").stringValue(),
            version = node.path("version").intValue(),
            presetId = node.path("presetId").stringValue(),
            capitalLimitKrw = node.path("capitalLimitKrw").longValue(),
            stopLossBps = node.path("stopLossBps").intValue(),
            takeProfitBps = node.path("takeProfitBps").intValue(),
            maxHoldingSessions = node.path("maxHoldingSessions").intValue(),
            atrPeriod = node.path("atrPeriod").intValue(),
            atrMultiplierMilli = node.path("atrMultiplierMilli").intValue(),
            modelSellEnabled = node.path("modelSellEnabled").booleanValue(),
            createdAt = OffsetDateTime.parse(node.path("createdAt").stringValue()),
            updatedAt = OffsetDateTime.parse(node.path("updatedAt").stringValue()),
        )
    }

    private fun decodeControl(json: String): AutomationControlProjection =
        objectMapper.readValue(json, AutomationControlProjection::class.java)

    private fun readCandidateScreeningsV3(
        jdbc: NamedParameterJdbcTemplate,
        runId: String,
    ): List<AutomationCandidateScreeningV3Projection> {
        val evidence =
            jdbc
                .query(
                    """
                    SELECT symbol,citation_id,source_id,source_type,source_event_date,age_warning,
                           uri_sha256,bounded_quote,quote_sha256,verified
                    FROM automation_candidate_evidence WHERE run_id=:runId ORDER BY symbol,citation_id
                    """.trimIndent(),
                    mapOf("runId" to runId),
                ) { row, _ ->
                    AutomationCandidateEvidenceV3Projection(
                        symbol = row.getString("symbol"),
                        citationId = row.getString("citation_id"),
                        sourceId = row.getString("source_id"),
                        sourceType = row.getString("source_type"),
                        sourceEventDate = row.getObject("source_event_date", LocalDate::class.java),
                        ageWarning = row.getBoolean("age_warning"),
                        uriSha256 = row.getString("uri_sha256"),
                        boundedQuote = row.getString("bounded_quote"),
                        quoteSha256 = row.getString("quote_sha256"),
                        verified = row.getBoolean("verified"),
                    )
                }.groupBy { it.symbol }
        return jdbc.query(
            """
            SELECT symbol,status,verdict,score_bps,reason
            FROM automation_candidate_screenings WHERE run_id=:runId ORDER BY symbol
            """.trimIndent(),
            mapOf("runId" to runId),
        ) { row, _ ->
            val symbol = row.getString("symbol")
            AutomationCandidateScreeningV3Projection(
                symbol = symbol,
                status = row.getString("status"),
                verdict = row.getString("verdict"),
                score = java.math.BigDecimal(row.getInt("score_bps")).movePointLeft(4),
                reason = row.getString("reason"),
                evidence = evidence[symbol].orEmpty(),
            )
        }
    }

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
            "P1B01" -> AutomationBlockedException("BLOCKED_INCOMPLETE_RISK_BALANCE", error)
            "P1L01" -> AutomationBlockedException("LEGACY_POSITION_PRESENT", error)
            "P1M01" -> AutomationBlockedException("MARKET_DATA_CATCHUP_REQUIRED", error)
            "P1A01" -> AutomationBlockedException("AI_PROVIDER_NOT_READY", error)
            "23505" -> AutomationIdempotencyConflictException()
            "40001" -> AutomationConflictException(error)
            "P0002" -> AutomationNotFoundException()
            "42501" -> AutomationAccessDeniedException(error)
            else -> AutomationStorageException(error)
        }

    private fun ResultSet.longOrNull(column: String): Long? = getObject(column, Long::class.javaObjectType)

    private fun ResultSet.intOrNull(column: String): Int? = getObject(column, Int::class.javaObjectType)

    private fun ResultSet.toRunV3(): AutomationRunV3Projection =
        AutomationRunV3Projection(
            runId = getString("run_id"),
            sessionDate = getObject("session_date", LocalDate::class.java),
            state = getString("state"),
            brokerageMode = getString("brokerage_mode"),
            selectedSymbol = getString("selected_symbol"),
            selectedSide = getString("selected_side"),
            policyId = getString("policy_id"),
            policyVersion = intOrNull("policy_version"),
            orderQuantity = longOrNull("quantity"),
            filledQuantity = longOrNull("filled_quantity"),
            leavesQuantity = longOrNull("leaves_quantity"),
            limitPriceKrw = longOrNull("limit_price_krw"),
            estimatedAmountKrw = longOrNull("estimated_amount_krw"),
            exitReason = getString("exit_reason"),
            physicalSubmitCount = getInt("physical_submit_count"),
            providerCalls = getInt("effective_provider_calls"),
            screeningProviderCallCount = getInt("screening_provider_call_count"),
            groundingQueryCount = getInt("grounding_query_count"),
            judgeCallCount = getInt("judge_call_count"),
            evidenceCount = getInt("evidence_count"),
            evidenceSetSha256 = getString("evidence_set_sha256"),
            aiSettingsSha256 = getString("ai_settings_sha256"),
            startedAt = getObject("started_at", OffsetDateTime::class.java),
            updatedAt = getObject("updated_at", OffsetDateTime::class.java),
        )

    private data class StatusRow(
        val controlState: String,
        val version: Int,
        val brokerageMode: String,
        val accountId: String,
        val certificationStatus: String,
        val activeRun: Boolean,
        val unresolved: Boolean,
        val openCount: Int,
        val certificationReady: Boolean,
        val releaseBindingClean: Boolean,
        val realTeamBPointerActive: Boolean,
        val principleConfigured: Boolean,
        val policyBindingId: String?,
        val policyBindingVersion: Int?,
    )

    private data class AutomationAiSettingsSnapshot(
        val provider: String,
        val enabled: Boolean,
        val thinkingLevel: String,
        val dailyGenerateCallCap: Int,
    )

    private companion object {
        val RUN_V3_SELECT =
            """
            SELECT run.run_id,run.session_date,run.state,run.brokerage_mode,
                   run.selected_symbol,run.selected_side,run.policy_id,run.policy_version,
                   reservation.quantity,reservation.filled_quantity,reservation.leaves_quantity,
                   reservation.limit_price_krw,reservation.estimated_amount_krw,
                   reservation.exit_reason,run.physical_submit_count,
                   COALESCE(usage.provider_call_count,run.provider_calls) effective_provider_calls,
                   COALESCE(usage.screening_provider_call_count,0) screening_provider_call_count,
                   COALESCE(usage.grounding_query_count,0) grounding_query_count,
                   COALESCE(ai.judge_call_count,0) judge_call_count,
                   (SELECT count(*) FROM automation_candidate_evidence evidence
                    WHERE evidence.run_id=run.run_id) evidence_count,
                   usage.evidence_set_sha256,run.ai_settings_sha256,
                   run.started_at,run.updated_at
            FROM automation_runs run
            LEFT JOIN automation_order_reservations reservation ON reservation.run_id=run.run_id
            LEFT JOIN automation_v3_usage usage ON usage.run_id=run.run_id
            LEFT JOIN LATERAL (
              SELECT judgement.judge_call_count FROM automation_ai_judgements judgement
              WHERE judgement.run_id=run.run_id
              ORDER BY judgement.checkpoint_version DESC LIMIT 1
            ) ai ON true
            """.trimIndent() + "\n"
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
