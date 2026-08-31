package com.capstone.decision.api.automation

import com.capstone.decision.application.automation.AutomationCandidateEvidenceV3Projection
import com.capstone.decision.application.automation.AutomationCandidateScreeningV3Projection
import com.capstone.decision.application.automation.AutomationPolicyV3Projection
import com.capstone.decision.application.automation.AutomationPositionV3Projection
import com.capstone.decision.application.automation.AutomationRunDetailV3Projection
import com.capstone.decision.application.automation.AutomationRunV3Projection
import com.capstone.decision.application.automation.AutomationStatusV3Projection
import io.swagger.v3.oas.annotations.media.Schema
import java.math.BigDecimal
import java.time.LocalDate
import java.time.OffsetDateTime

@Schema(name = "AutomationPolicyV3", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class AutomationPolicyV3Response(
    val contractId: String = "automation-policy.v2",
    val policyId: String,
    val version: Int,
    val presetId: String,
    val capitalLimitKrw: Long,
    val stopLossBps: Int,
    val takeProfitBps: Int,
    val maxHoldingSessions: Int,
    val atrPeriod: Int,
    val atrMultiplierMilli: Int,
    val modelSellEnabled: Boolean,
    val maxOpenPositions: Int = 5,
    val maxNewOrdersPerSession: Int = 1,
    val evaluationTimeKst: String = "09:30",
    val buyCutoffTimeKst: String = "09:40",
    val cancelTimeKst: String = "15:20",
    val createdAt: OffsetDateTime,
    val updatedAt: OffsetDateTime,
)

@Schema(name = "AutomationStatusV3", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class AutomationStatusV3Response(
    val contractId: String = "automation-status.v3",
    val controlState: String,
    val projectionState: String,
    val controlVersion: Int,
    val brokerageMode: String = "KIS_MOCK",
    val accountId: String?,
    val policy: AutomationPolicyV3Response?,
    val aiJudgementEnabled: Boolean,
    val thinkingLevel: String,
    val marketHistoryStatus: String,
    val killSwitchActive: Boolean,
    val certificationStatus: String,
    val openPositionCount: Int,
    val legacyOpenPositionCount: Int,
    val unresolvedReconciliation: Boolean,
    val canArm: Boolean,
    val blockers: List<String>,
)

@Schema(name = "AutomationRunV3", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class AutomationRunV3Response(
    val contractId: String = "automation-run.v3",
    val runId: String,
    val sessionDate: LocalDate,
    val state: String,
    val brokerageMode: String,
    val policyId: String?,
    val policyVersion: Int?,
    val selectedSymbol: String?,
    val selectedSide: String?,
    val orderQuantity: Long?,
    val filledQuantity: Long?,
    val leavesQuantity: Long?,
    val limitPriceKrw: Long?,
    val estimatedAmountKrw: Long?,
    val exitReason: String?,
    val physicalSubmitCount: Int,
    val providerCalls: Int,
    val screeningProviderCallCount: Int,
    val groundingQueryCount: Int,
    val judgeCallCount: Int,
    val evidenceCount: Int,
    val evidenceSetSha256: String?,
    val aiSettingsSha256: String?,
    val startedAt: OffsetDateTime,
    val updatedAt: OffsetDateTime,
)

data class AutomationRunPageV3Response(
    val items: List<AutomationRunV3Response>,
    val nextCursor: String?,
)

data class AutomationCandidateEvidenceV3Response(
    val symbol: String,
    val citationId: String,
    val sourceId: String,
    val sourceType: String,
    val sourceEventDate: LocalDate?,
    val ageWarning: Boolean,
    val uriSha256: String,
    val boundedQuote: String,
    val quoteSha256: String,
    val verified: Boolean,
)

data class AutomationCandidateScreeningV3Response(
    val symbol: String,
    val status: String,
    val verdict: String,
    val score: BigDecimal,
    val reason: String,
    val evidence: List<AutomationCandidateEvidenceV3Response>,
)

data class AutomationRunDetailV3Response(
    val contractId: String = "automation-run-detail.v3",
    val run: AutomationRunV3Response,
    val candidateScreenings: List<AutomationCandidateScreeningV3Response>,
)

data class AutomationPositionV3Response(
    val contractId: String = "automation-position.v3",
    val positionId: String,
    val accountId: String,
    val symbol: String,
    val quantity: Long,
    val entryAverageFillPriceKrw: Long,
    val entrySession: LocalDate,
    val expirySession: LocalDate?,
    val policyId: String,
    val policyVersion: Int,
    val stopLossBps: Int,
    val takeProfitBps: Int,
    val maxHoldingSessions: Int,
    val atrPeriod: Int,
    val atrMultiplierMilli: Int,
    val modelSellEnabled: Boolean,
    val peakPriceKrw: Long,
    val atrAsOfSession: LocalDate?,
    val trailingStopKrw: Long?,
    val status: String,
    val exitReason: String?,
    val botOwned: Boolean,
    val shortAllowed: Boolean,
    val createdAt: OffsetDateTime,
    val closedAt: OffsetDateTime?,
)

data class AutomationPositionPageV3Response(
    val items: List<AutomationPositionV3Response>,
)

@Schema(name = "PutAutomationPolicyV3Request", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
class PutAutomationPolicyV3RequestSchema(
    val capitalLimitKrw: Long,
    val stopLossBps: Int,
    val takeProfitBps: Int,
    val maxHoldingSessions: Int,
    val atrPeriod: Int,
    val atrMultiplierMilli: Int,
    val modelSellEnabled: Boolean,
    val expectedVersion: Int,
)

@Schema(name = "ArmAutomationV3Request", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
class ArmAutomationV3RequestSchema(
    val accountId: String,
    val policyId: String,
    val expectedPolicyVersion: Int,
    val expectedControlVersion: Int,
)

fun AutomationPolicyV3Projection.toV3Response() =
    AutomationPolicyV3Response(
        policyId = policyId,
        version = version,
        presetId = presetId,
        capitalLimitKrw = capitalLimitKrw,
        stopLossBps = stopLossBps,
        takeProfitBps = takeProfitBps,
        maxHoldingSessions = maxHoldingSessions,
        atrPeriod = atrPeriod,
        atrMultiplierMilli = atrMultiplierMilli,
        modelSellEnabled = modelSellEnabled,
        createdAt = createdAt,
        updatedAt = updatedAt,
    )

fun AutomationStatusV3Projection.toV3Response() =
    AutomationStatusV3Response(
        controlState = controlState,
        projectionState = projectionState,
        controlVersion = controlVersion,
        accountId = accountId,
        policy = policy?.toV3Response(),
        aiJudgementEnabled = aiJudgementEnabled,
        thinkingLevel = thinkingLevel,
        marketHistoryStatus = marketHistoryStatus,
        killSwitchActive = killSwitchActive,
        certificationStatus = certificationStatus,
        openPositionCount = openPositionCount,
        legacyOpenPositionCount = legacyOpenPositionCount,
        unresolvedReconciliation = unresolvedReconciliation,
        canArm = canArm,
        blockers = blockers,
    )

fun AutomationRunV3Projection.toV3Response() =
    AutomationRunV3Response(
        runId = runId,
        sessionDate = sessionDate,
        state = state,
        brokerageMode = brokerageMode,
        policyId = policyId,
        policyVersion = policyVersion,
        selectedSymbol = selectedSymbol,
        selectedSide = selectedSide,
        orderQuantity = orderQuantity,
        filledQuantity = filledQuantity,
        leavesQuantity = leavesQuantity,
        limitPriceKrw = limitPriceKrw,
        estimatedAmountKrw = estimatedAmountKrw,
        exitReason = exitReason,
        physicalSubmitCount = physicalSubmitCount,
        providerCalls = providerCalls,
        screeningProviderCallCount = screeningProviderCallCount,
        groundingQueryCount = groundingQueryCount,
        judgeCallCount = judgeCallCount,
        evidenceCount = evidenceCount,
        evidenceSetSha256 = evidenceSetSha256,
        aiSettingsSha256 = aiSettingsSha256,
        startedAt = startedAt,
        updatedAt = updatedAt,
    )

fun AutomationCandidateEvidenceV3Projection.toV3Response() =
    AutomationCandidateEvidenceV3Response(
        symbol,
        citationId,
        sourceId,
        sourceType,
        sourceEventDate,
        ageWarning,
        uriSha256,
        boundedQuote,
        quoteSha256,
        verified,
    )

fun AutomationCandidateScreeningV3Projection.toV3Response() =
    AutomationCandidateScreeningV3Response(
        symbol,
        status,
        verdict,
        score,
        reason,
        evidence.map { it.toV3Response() },
    )

fun AutomationRunDetailV3Projection.toV3Response() =
    AutomationRunDetailV3Response(
        run = run.toV3Response(),
        candidateScreenings = candidateScreenings.map { it.toV3Response() },
    )

fun AutomationPositionV3Projection.toV3Response() =
    AutomationPositionV3Response(
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
        maxHoldingSessions = maxHoldingSessions,
        atrPeriod = atrPeriod,
        atrMultiplierMilli = atrMultiplierMilli,
        modelSellEnabled = modelSellEnabled,
        peakPriceKrw = peakPriceKrw,
        atrAsOfSession = atrAsOfSession,
        trailingStopKrw = trailingStopKrw,
        status = status,
        exitReason = exitReason,
        botOwned = botOwned,
        shortAllowed = shortAllowed,
        createdAt = createdAt,
        closedAt = closedAt,
    )
