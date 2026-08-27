package com.capstone.decision.api.automation

import com.capstone.decision.application.automation.AutomationControlProjection
import com.capstone.decision.application.automation.AutomationPolicyV2Projection
import com.capstone.decision.application.automation.AutomationPositionV2Projection
import com.capstone.decision.application.automation.AutomationRunProjection
import com.capstone.decision.application.automation.AutomationRunV2Projection
import com.capstone.decision.application.automation.AutomationStatusV2Projection
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

@Schema(name = "AutomationPolicyV2", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class AutomationPolicyV2Response(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["automation-policy.v1"])
    val contractId: String = "automation-policy.v1",
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, pattern = "^auto_pol_[0-9a-f]{32}$")
    val policyId: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1")
    val version: Int,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["conservative", "balanced", "aggressive", "custom"])
    val presetId: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "10000", maximum = "10000000000")
    val capitalLimitKrw: Long,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "100", maximum = "1500")
    val stopLossBps: Int,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "200", maximum = "3000")
    val takeProfitBps: Int,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["5"])
    val maxOpenPositions: Int = 5,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["1"])
    val maxNewOrdersPerSession: Int = 1,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["09:30"])
    val evaluationTimeKst: String = "09:30",
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["09:40"])
    val buyCutoffTimeKst: String = "09:40",
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["15:20"])
    val cancelTimeKst: String = "15:20",
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time")
    val createdAt: OffsetDateTime,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time")
    val updatedAt: OffsetDateTime,
)

@Schema(name = "AutomationStatusV2", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class AutomationStatusV2Response(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["automation-status.v2"])
    val contractId: String = "automation-status.v2",
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["DISARMED", "ARMED", "HALTED"])
    val controlState: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["DISARMED", "ARMED", "RUNNING", "HALTED"])
    val projectionState: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1")
    val controlVersion: Int,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["KIS_MOCK"])
    val brokerageMode: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, nullable = true, pattern = "^acct_[A-Za-z0-9_-]{8,96}$")
    val accountId: String?,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, nullable = true)
    val policy: AutomationPolicyV2Response?,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val killSwitchActive: Boolean,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val certificationStatus: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "0", maximum = "5")
    val openPositionCount: Int,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val unresolvedReconciliation: Boolean,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val canArm: Boolean,
    @field:ArraySchema(arraySchema = Schema(requiredMode = Schema.RequiredMode.REQUIRED), schema = Schema(maxLength = 96))
    val blockers: List<String>,
)

@Schema(name = "AutomationRunV2", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class AutomationRunV2Response(
    val contractId: String = "automation-run.v2",
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

@Schema(name = "AutomationRunPageV2", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class AutomationRunPageV2Response(
    val items: List<AutomationRunV2Response>,
    val nextCursor: String?,
)

@Schema(name = "AutomationPositionV2", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class AutomationPositionV2Response(
    val contractId: String = "automation-position.v2",
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
    val botOwned: Boolean,
    val shortAllowed: Boolean,
    val createdAt: OffsetDateTime,
    val closedAt: OffsetDateTime?,
)

@Schema(name = "AutomationPositionPageV2", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class AutomationPositionPageV2Response(
    val items: List<AutomationPositionV2Response>,
    val nextCursor: String?,
)

@Schema(name = "PutAutomationPolicyV2Request", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
class PutAutomationPolicyV2RequestSchema(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "10000", maximum = "10000000000")
    val capitalLimitKrw: Long,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "100", maximum = "1500")
    val stopLossBps: Int,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "200", maximum = "3000")
    val takeProfitBps: Int,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "0")
    val expectedVersion: Int,
)

@Schema(name = "ArmAutomationV2Request", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
class ArmAutomationV2RequestSchema(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, pattern = "^acct_[A-Za-z0-9_-]{8,96}$")
    val accountId: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, pattern = "^auto_pol_[0-9a-f]{32}$")
    val policyId: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1")
    val expectedPolicyVersion: Int,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1")
    val expectedControlVersion: Int,
)

@Schema(name = "P1AutomationV2ErrorResponse")
class P1AutomationV2ErrorResponseSchema

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

fun AutomationPolicyV2Projection.toResponse(): AutomationPolicyV2Response =
    AutomationPolicyV2Response(
        policyId = policyId,
        version = version,
        presetId = presetId,
        capitalLimitKrw = capitalLimitKrw,
        stopLossBps = stopLossBps,
        takeProfitBps = takeProfitBps,
        createdAt = createdAt,
        updatedAt = updatedAt,
    )

fun AutomationStatusV2Projection.toResponse(): AutomationStatusV2Response =
    AutomationStatusV2Response(
        controlState = controlState,
        projectionState = projectionState,
        controlVersion = controlVersion,
        brokerageMode = brokerageMode,
        accountId = accountId,
        policy = policy?.toResponse(),
        killSwitchActive = killSwitchActive,
        certificationStatus = certificationStatus,
        openPositionCount = openPositionCount,
        unresolvedReconciliation = unresolvedReconciliation,
        canArm = canArm,
        blockers = blockers,
    )

fun AutomationRunV2Projection.toResponse(): AutomationRunV2Response =
    AutomationRunV2Response(
        runId = runId,
        sessionDate = sessionDate,
        state = state,
        brokerageMode = brokerageMode,
        selectedSymbol = selectedSymbol,
        selectedSide = selectedSide,
        policyId = policyId,
        policyVersion = policyVersion,
        orderQuantity = orderQuantity,
        filledQuantity = filledQuantity,
        leavesQuantity = leavesQuantity,
        limitPriceKrw = limitPriceKrw,
        estimatedAmountKrw = estimatedAmountKrw,
        exitReason = exitReason,
        physicalSubmitCount = physicalSubmitCount,
        providerCalls = providerCalls,
        startedAt = startedAt,
        updatedAt = updatedAt,
    )

fun AutomationPositionV2Projection.toResponse(): AutomationPositionV2Response =
    AutomationPositionV2Response(
        positionId = positionId,
        accountId = accountId,
        symbol = symbol,
        quantity = quantity,
        entryAverageFillPriceKrw = entryAverageFillPriceKrw,
        entrySession = entrySession,
        expirySession = expirySession,
        policyId = policyId,
        policyVersion = policyVersion,
        stopLossBps = stopLossBps,
        takeProfitBps = takeProfitBps,
        status = status,
        exitReason = exitReason,
        botOwned = botOwned,
        shortAllowed = shortAllowed,
        createdAt = createdAt,
        closedAt = closedAt,
    )
