package com.capstone.decision.api.automation

import com.capstone.decision.application.automation.AutomationCapitalPolicyProjection
import com.capstone.decision.application.automation.AutomationCapitalStatusProjection
import io.swagger.v3.oas.annotations.media.Schema
import java.time.LocalDate
import java.time.OffsetDateTime

@Schema(name = "AutomationCapitalPolicyV1", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class AutomationCapitalPolicyResponse(
    val contractId: String = "automation-capital-policy.v1",
    val version: Int,
    val reinvestRealizedPnl: Boolean,
    val cashBufferBps: Int,
    val rebalanceDeviationBps: Int,
    val minimumAdjustmentKrw: Long,
    val maxOrdersPerSession: Int,
    val effectiveFromSession: LocalDate,
    val transitionStartedAt: OffsetDateTime,
)

@Schema(name = "PutAutomationCapitalPolicyV1Request", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
class PutAutomationCapitalPolicyRequestSchema(
    val reinvestRealizedPnl: Boolean,
    val expectedVersion: Int,
)

fun AutomationCapitalPolicyProjection.toCapitalPolicyResponse() =
    AutomationCapitalPolicyResponse(
        version = version,
        reinvestRealizedPnl = reinvestRealizedPnl,
        cashBufferBps = cashBufferBps,
        rebalanceDeviationBps = rebalanceDeviationBps,
        minimumAdjustmentKrw = minimumAdjustmentKrw,
        maxOrdersPerSession = maxOrdersPerSession,
        effectiveFromSession = effectiveFromSession,
        transitionStartedAt = transitionStartedAt,
    )

data class AutomationCapitalPositionResponse(
    val symbol: String,
    val currentQuantity: Long,
    val targetQuantity: Long?,
    val currentMarketValueKrw: Long?,
    val targetMarketValueKrw: Long,
    val currentWeightBps: Long?,
    val targetWeightBps: Long,
    val valuationStatus: String,
)

data class AutomationCapitalStatusResponse(
    val contractId: String = "automation-capital-status.v1",
    val policyVersion: Int,
    val reinvestRealizedPnl: Boolean,
    val configuredCapitalKrw: Long,
    val realizedPnlSinceTransitionKrw: Long,
    val brokerBuyableCashKrw: Long,
    val botPositionMarketValueKrw: Long,
    val reservedBuyCashKrw: Long,
    val allocationCapKrw: Long,
    val investableCapKrw: Long,
    val availableBuyCashKrw: Long,
    val targetPerPositionKrw: Long,
    val existingBotPositionsAdopted: Int,
    val valuationMissingCount: Int,
    val unusedCashReason: String?,
    val positions: List<AutomationCapitalPositionResponse>,
    val asOf: OffsetDateTime,
)

fun AutomationCapitalStatusProjection.toCapitalStatusResponse() =
    AutomationCapitalStatusResponse(
        policyVersion = policyVersion,
        reinvestRealizedPnl = reinvestRealizedPnl,
        configuredCapitalKrw = configuredCapitalKrw,
        realizedPnlSinceTransitionKrw = realizedPnlSinceTransitionKrw,
        brokerBuyableCashKrw = brokerBuyableCashKrw,
        botPositionMarketValueKrw = botPositionMarketValueKrw,
        reservedBuyCashKrw = reservedBuyCashKrw,
        allocationCapKrw = allocationCapKrw,
        investableCapKrw = investableCapKrw,
        availableBuyCashKrw = availableBuyCashKrw,
        targetPerPositionKrw = targetPerPositionKrw,
        existingBotPositionsAdopted = existingBotPositionsAdopted,
        valuationMissingCount = valuationMissingCount,
        unusedCashReason = unusedCashReason,
        positions =
            positions.map {
                AutomationCapitalPositionResponse(
                    it.symbol,
                    it.currentQuantity,
                    it.targetQuantity,
                    it.currentMarketValueKrw,
                    it.targetMarketValueKrw,
                    it.currentWeightBps,
                    it.targetWeightBps,
                    it.valuationStatus,
                )
            },
        asOf = asOf,
    )
