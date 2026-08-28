package com.capstone.decision.application.automation

import java.time.LocalDate
import java.time.OffsetDateTime

data class AutomationControlProjection(
    val controlState: String,
    val projectionState: String,
    val version: Int,
    val brokerageMode: String,
    val principleId: String,
    val strategyId: String,
    val killSwitchActive: Boolean,
    val certificationStatus: String,
)

data class AutomationRunProjection(
    val runId: String,
    val sessionDate: LocalDate,
    val state: String,
    val brokerageMode: String,
    val selectedSymbol: String?,
    val selectedSide: String?,
    val physicalSubmitCount: Int,
    val vertexCallCount: Int,
    val providerCalls: Int,
    val startedAt: OffsetDateTime,
    val updatedAt: OffsetDateTime,
)

data class AutomationRunCursor(
    val updatedAt: OffsetDateTime,
    val runId: String,
)

data class AutomationRunPage(
    val items: List<AutomationRunProjection>,
    val nextCursor: String?,
)

data class AutomationPolicyV2Projection(
    val policyId: String,
    val version: Int,
    val presetId: String,
    val capitalLimitKrw: Long,
    val stopLossBps: Int,
    val takeProfitBps: Int,
    val createdAt: OffsetDateTime,
    val updatedAt: OffsetDateTime,
)

data class AutomationStatusV2Projection(
    val controlState: String,
    val projectionState: String,
    val controlVersion: Int,
    val brokerageMode: String,
    val accountId: String?,
    val policy: AutomationPolicyV2Projection?,
    val killSwitchActive: Boolean,
    val certificationStatus: String,
    val openPositionCount: Int,
    val unresolvedReconciliation: Boolean,
    val canArm: Boolean,
    val blockers: List<String>,
)

data class AutomationRunV2Projection(
    val runId: String,
    val sessionDate: LocalDate,
    val state: String,
    val brokerageMode: String,
    val selectedSymbol: String?,
    val selectedSide: String?,
    val policyId: String?,
    val policyVersion: Int?,
    val orderQuantity: Long?,
    val filledQuantity: Long?,
    val leavesQuantity: Long?,
    val limitPriceKrw: Long?,
    val estimatedAmountKrw: Long?,
    val exitReason: String?,
    val physicalSubmitCount: Int,
    val providerCalls: Int,
    val startedAt: OffsetDateTime,
    val updatedAt: OffsetDateTime,
)

data class AutomationRunV2Page(
    val items: List<AutomationRunV2Projection>,
    val nextCursor: String?,
)

data class AutomationPositionV2Projection(
    val positionId: String,
    val accountId: String,
    val symbol: String,
    val quantity: Long,
    val entryAverageFillPriceKrw: Long,
    val entrySession: LocalDate,
    val expirySession: LocalDate,
    val policyId: String,
    val policyVersion: Int,
    val stopLossBps: Int,
    val takeProfitBps: Int,
    val status: String,
    val exitReason: String?,
    val exitAverageFillPriceKrw: Long?,
    val realizedPnlKrw: Long?,
    val botOwned: Boolean,
    val shortAllowed: Boolean,
    val createdAt: OffsetDateTime,
    val closedAt: OffsetDateTime?,
)

data class AutomationPositionV2Page(
    val items: List<AutomationPositionV2Projection>,
    val nextCursor: String?,
)

data class PutAutomationPolicyV2Command(
    val capitalLimitKrw: Long,
    val stopLossBps: Int,
    val takeProfitBps: Int,
    val expectedVersion: Int,
)

data class ArmAutomationV2Command(
    val accountId: String,
    val policyId: String,
    val expectedPolicyVersion: Int,
    val expectedControlVersion: Int,
)

data class ArmAutomationCommand(
    val brokerageMode: String,
    val accountId: String,
    val principleId: String,
    val strategyId: String,
    val expectedVersion: Int,
)

data class DisarmAutomationCommand(
    val expectedVersion: Int,
)

interface AutomationRepository {
    fun status(ownerUserId: String): AutomationControlProjection

    fun arm(
        ownerUserId: String,
        command: ArmAutomationCommand,
        scopeHash: String,
        requestHash: String,
    ): AutomationControlProjection

    fun disarm(
        ownerUserId: String,
        command: DisarmAutomationCommand,
        scopeHash: String,
        requestHash: String,
    ): AutomationControlProjection

    fun listRuns(
        ownerUserId: String,
        limit: Int,
        after: AutomationRunCursor?,
    ): List<AutomationRunProjection>

    fun statusV2(ownerUserId: String): AutomationStatusV2Projection

    fun putPolicyV2(
        ownerUserId: String,
        command: PutAutomationPolicyV2Command,
        scopeHash: String,
        requestHash: String,
    ): AutomationPolicyV2Projection

    fun armV2(
        ownerUserId: String,
        command: ArmAutomationV2Command,
        scopeHash: String,
        requestHash: String,
    ): AutomationStatusV2Projection

    fun listRunsV2(
        ownerUserId: String,
        limit: Int,
        after: AutomationRunCursor?,
    ): List<AutomationRunV2Projection>

    fun listPositionsV2(ownerUserId: String): List<AutomationPositionV2Projection>
}

class AutomationConflictException(
    cause: Throwable? = null,
) : RuntimeException("Automation state conflict.", cause)

class AutomationIdempotencyConflictException : RuntimeException("Automation idempotency conflict.")

class AutomationNotFoundException : RuntimeException("Automation dependency was not found.")

class AutomationAccessDeniedException(
    cause: Throwable? = null,
) : RuntimeException("Automation access was denied.", cause)

class AutomationStorageException(
    cause: Throwable,
) : RuntimeException("Automation storage is unavailable.", cause)

class AutomationBlockedException(
    val reason: String,
    cause: Throwable? = null,
) : RuntimeException("Automation is blocked: $reason", cause)
