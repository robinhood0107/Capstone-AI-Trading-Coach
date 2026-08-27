package com.capstone.decision.api.automation

import com.capstone.decision.application.automation.AutomationControlProjection
import com.capstone.decision.application.automation.AutomationRunProjection
import io.swagger.v3.oas.annotations.media.ArraySchema
import io.swagger.v3.oas.annotations.media.Schema
import java.time.LocalDate
import java.time.OffsetDateTime

@Schema(name = "AutomationControl", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class AutomationControlResponse(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["automation-control.v1"])
    val contractId: String = "automation-control.v1",
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["DISARMED", "ARMED", "HALTED"])
    val controlState: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["DISARMED", "ARMED", "RUNNING", "HALTED"])
    val projectionState: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1")
    val version: Int,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["KIS_MOCK", "INTERNAL_PAPER"])
    val brokerageMode: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, pattern = "^prc_[A-Za-z0-9_-]{8,96}$")
    val principleId: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, pattern = "^strategy_[A-Za-z0-9_-]{8,96}$")
    val strategyId: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val killSwitchActive: Boolean,
    @field:Schema(
        requiredMode = Schema.RequiredMode.REQUIRED,
        allowableValues = ["NOT_REQUIRED_INTERNAL_PAPER", "REQUIRED", "VALID", "EXPIRED", "INVALID"],
    )
    val certificationStatus: String,
)

@Schema(name = "AutomationRun", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class AutomationRunResponse(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["automation-run.v1"])
    val contractId: String = "automation-run.v1",
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, pattern = "^auto_run_[A-Za-z0-9_-]{8,96}$")
    val runId: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date")
    val sessionDate: LocalDate,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val state: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["KIS_MOCK", "INTERNAL_PAPER"])
    val brokerageMode: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, nullable = true, pattern = "^[0-9]{6}$")
    val selectedSymbol: String?,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, nullable = true, allowableValues = ["BUY", "SELL"])
    val selectedSide: String?,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "0", maximum = "1")
    val physicalSubmitCount: Int,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "0", maximum = "1")
    val vertexCallCount: Int,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "0", maximum = "16")
    val providerCalls: Int,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time")
    val startedAt: OffsetDateTime,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time")
    val updatedAt: OffsetDateTime,
)

@Schema(name = "AutomationRunPage", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class AutomationRunPageResponse(
    @field:ArraySchema(arraySchema = Schema(requiredMode = Schema.RequiredMode.REQUIRED))
    val items: List<AutomationRunResponse>,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, nullable = true, maxLength = 512)
    val nextCursor: String?,
)

@Schema(name = "ArmAutomationRequest", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
class ArmAutomationRequestSchema(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["KIS_MOCK", "INTERNAL_PAPER"])
    val brokerageMode: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, pattern = "^acct_[A-Za-z0-9_-]{8,96}$")
    val accountId: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, pattern = "^prc_[A-Za-z0-9_-]{8,96}$")
    val principleId: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, pattern = "^strategy_[A-Za-z0-9_-]{8,96}$")
    val strategyId: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1")
    val expectedVersion: Int,
)

@Schema(name = "DisarmAutomationRequest", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
class DisarmAutomationRequestSchema(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1")
    val expectedVersion: Int,
)

@Schema(name = "P1AutomationErrorResponse")
class P1AutomationErrorResponseSchema

fun AutomationControlProjection.toResponse(): AutomationControlResponse =
    AutomationControlResponse(
        controlState = controlState,
        projectionState = projectionState,
        version = version,
        brokerageMode = brokerageMode,
        principleId = principleId,
        strategyId = strategyId,
        killSwitchActive = killSwitchActive,
        certificationStatus = certificationStatus,
    )

fun AutomationRunProjection.toResponse(): AutomationRunResponse =
    AutomationRunResponse(
        runId = runId,
        sessionDate = sessionDate,
        state = state,
        brokerageMode = brokerageMode,
        selectedSymbol = selectedSymbol,
        selectedSide = selectedSide,
        physicalSubmitCount = physicalSubmitCount,
        vertexCallCount = vertexCallCount,
        providerCalls = providerCalls,
        startedAt = startedAt,
        updatedAt = updatedAt,
    )
