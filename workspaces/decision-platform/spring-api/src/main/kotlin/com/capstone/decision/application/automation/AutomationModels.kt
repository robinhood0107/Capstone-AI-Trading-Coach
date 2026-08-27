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
