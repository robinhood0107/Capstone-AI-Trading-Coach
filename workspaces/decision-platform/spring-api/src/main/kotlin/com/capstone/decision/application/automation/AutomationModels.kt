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
    // V2 정책은 보유 만기를 정의하지 않는다.
    val expirySession: LocalDate?,
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

data class AutomationRealizedPerformanceV2Projection(
    val closedPositionCount: Long,
    val realizedPnlKrw: Long,
    val realizedGrossKrw: Long,
    val winningPositionCount: Long,
    val losingPositionCount: Long,
)

data class AutomationPositionV2Page(
    val realizedSummary: AutomationRealizedPerformanceV2Projection,
    val items: List<AutomationPositionV2Projection>,
    val nextCursor: String?,
)

data class AutomationPolicyV3Projection(
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
    /** 동시 보유 종목 상한. 종목당 상한금액 = capitalLimitKrw / 이 값. */
    val maxOpenPositions: Int,
    /** ATR 변동성 기반 사이징의 거래당 위험(자본 대비 bps). 100 = 1%. */
    val riskPerTradeBps: Int,
    val createdAt: OffsetDateTime,
    val updatedAt: OffsetDateTime,
)

data class AutomationCapitalPolicyProjection(
    val version: Int,
    val reinvestRealizedPnl: Boolean,
    val cashBufferBps: Int,
    val rebalanceDeviationBps: Int,
    val minimumAdjustmentKrw: Long,
    val maxOrdersPerSession: Int,
    val effectiveFromSession: LocalDate,
    val transitionStartedAt: OffsetDateTime,
)

data class PutAutomationCapitalPolicyCommand(
    val reinvestRealizedPnl: Boolean,
    val expectedVersion: Int,
)

data class AutomationCapitalPositionProjection(
    val symbol: String,
    val currentQuantity: Long,
    val targetQuantity: Long?,
    val currentMarketValueKrw: Long?,
    val targetMarketValueKrw: Long,
    val currentWeightBps: Long?,
    val targetWeightBps: Long,
    val valuationStatus: String,
)

data class AutomationCapitalStatusProjection(
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
    val positions: List<AutomationCapitalPositionProjection>,
    val asOf: OffsetDateTime,
)

data class AutomationStatusV3Projection(
    val controlState: String,
    val projectionState: String,
    val controlVersion: Int,
    val accountId: String?,
    val policy: AutomationPolicyV3Projection?,
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
    val appliedPolicyVersion: Int? = null,
    val policyRecoverySourceVersion: Int? = null,
    val nextRunAt: java.time.OffsetDateTime? = null,
)

data class AutomationRunV3Projection(
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
    val screeningProviderCallCount: Int,
    val groundingQueryCount: Int,
    val judgeCallCount: Int,
    val evidenceCount: Int,
    val evidenceSetSha256: String?,
    val aiSettingsSha256: String?,
    val startedAt: OffsetDateTime,
    val updatedAt: OffsetDateTime,
)

data class AutomationRunV3Page(
    val items: List<AutomationRunV3Projection>,
    val nextCursor: String?,
)

data class AutomationRunDetailV3Projection(
    val run: AutomationRunV3Projection,
    val candidateScreenings: List<AutomationCandidateScreeningV3Projection>,
    /**
     * 후보가 어느 단계에서 왜 빠졌는지. 뉴스/AI 단계까지 간 후보만 담는 심사표와 달리
     * 규칙·LSTM·안전필터·ATR 탈락까지 포함하므로, 무주문 실행도 원인을 말할 수 있다.
     */
    val stageOutcomes: List<AutomationStageOutcomeProjection> = emptyList(),
)

data class AutomationStageOutcomeProjection(
    val stage: String,
    val symbol: String,
    val outcome: String,
    val reasonCode: String?,
    val reasonDetail: String?,
)

data class AutomationCandidateScreeningV3Projection(
    val symbol: String,
    val status: String,
    val verdict: String,
    val score: java.math.BigDecimal,
    val reason: String,
    val evidence: List<AutomationCandidateEvidenceV3Projection>,
)

data class AutomationCandidateEvidenceV3Projection(
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

data class AutomationPositionV3Projection(
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

data class PutAutomationPolicyV3Command(
    val capitalLimitKrw: Long,
    val stopLossBps: Int,
    val takeProfitBps: Int,
    val maxHoldingSessions: Int,
    val atrPeriod: Int,
    val atrMultiplierMilli: Int,
    val modelSellEnabled: Boolean,
    val maxOpenPositions: Int,
    val riskPerTradeBps: Int,
    val expectedVersion: Int,
)

data class ArmAutomationV3Command(
    val accountId: String,
    val policyId: String,
    val expectedPolicyVersion: Int,
    val expectedControlVersion: Int,
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

    fun readPositionPageV2(ownerUserId: String): AutomationPositionV2Page

    fun statusV3(ownerUserId: String): AutomationStatusV3Projection

    fun putPolicyV3(
        ownerUserId: String,
        command: PutAutomationPolicyV3Command,
        scopeHash: String,
        requestHash: String,
    ): AutomationPolicyV3Projection

    fun armV3(
        ownerUserId: String,
        command: ArmAutomationV3Command,
        scopeHash: String,
        requestHash: String,
        providerCapabilityReady: Boolean,
    ): AutomationStatusV3Projection

    fun listRunsV3(
        ownerUserId: String,
        limit: Int,
        after: AutomationRunCursor?,
    ): List<AutomationRunV3Projection>

    fun readRunV3(
        ownerUserId: String,
        runId: String,
    ): AutomationRunDetailV3Projection

    fun readPositionsV3(ownerUserId: String): List<AutomationPositionV3Projection>

    fun readCapitalPolicy(ownerUserId: String): AutomationCapitalPolicyProjection?

    fun readCapitalStatus(ownerUserId: String): AutomationCapitalStatusProjection?

    fun putCapitalPolicy(
        ownerUserId: String,
        command: PutAutomationCapitalPolicyCommand,
        scopeHash: String,
        requestHash: String,
    ): AutomationCapitalPolicyProjection
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
