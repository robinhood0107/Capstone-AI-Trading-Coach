package com.capstone.decision.domain.risk

import com.capstone.decision.domain.principle.EvidenceRequirement

sealed interface RuleReadiness {
    data class Ready(
        val rule: EnabledReadyRule,
        val snapshot: ReadyMetricSnapshot,
    ) : RuleReadiness

    data class Hold(
        val issue: EvaluationIssue,
    ) : RuleReadiness

    data class Abstain(
        val warning: EvaluationWarning,
        val abstention: Abstention,
    ) : RuleReadiness

    data class NotApplicable(
        val reason: String,
    ) : RuleReadiness
}

// hard evidence는 fail-closed HOLD, optional evidence는 국소 ABSTAIN으로 분리하고 evaluator 호출 여부를 결정한다.
class MetricReadinessPolicy {
    fun classify(
        rule: CandidateRule,
        snapshot: MetricSnapshot,
    ): RuleReadiness {
        if (!rule.enabled) {
            return RuleReadiness.NotApplicable("RULE_DISABLED")
        }
        if (!rule.applicable) {
            return RuleReadiness.NotApplicable("CONTEXT_NOT_APPLICABLE")
        }
        if (rule.executionKind == RuleExecutionKind.NOT_APPLICABLE) {
            return RuleReadiness.NotApplicable("SOURCE_NOT_AVAILABLE")
        }
        require(rule.executionKind == RuleExecutionKind.THRESHOLD) {
            "Readiness meta-rule is not a threshold evaluator input."
        }

        return when (val cell = snapshot.metric(rule.metricKey)) {
            is MetricCell.Available ->
                if (cell.observedAt.isAfter(snapshot.evaluationAsOf)) {
                    unavailable(rule, MetricIssueCode.SOURCE_FUTURE_TIMESTAMP)
                } else if (snapshot.evaluationAsOf.isAfter(cell.freshUntil)) {
                    unavailable(rule, MetricIssueCode.SOURCE_STALE)
                } else {
                    RuleReadiness.Ready(
                        rule = rule.enabledReady(),
                        snapshot = ReadyMetricSnapshot.single(snapshot.evaluationAsOf, rule.metricKey, cell),
                    )
                }

            is MetricCell.Missing -> unavailable(rule, cell.reason)
            is MetricCell.Stale -> unavailable(rule, cell.reason)
            is MetricCell.Error -> unavailable(rule, cell.reason)
            is MetricCell.Incomplete -> unavailable(rule, cell.reason)
            is MetricCell.Abstained -> unavailable(rule, cell.reason)
            // 적용 가능 여부는 rule context가 결정한다. 적용 중인 source가 N/A를 반환해 hard rule을 우회할 수 없다.
            is MetricCell.NotApplicable -> unavailable(rule, cell.reason)
        }
    }

    private fun unavailable(
        rule: CandidateRule,
        reason: MetricIssueCode,
    ): RuleReadiness =
        if (rule.evidenceRequirement == EvidenceRequirement.REQUIRED) {
            RuleReadiness.Hold(
                EvaluationIssue(
                    order = rule.order,
                    ruleId = rule.ruleId,
                    publicCode = requiredIssueCode(rule.metricKey, reason),
                    internalCause = reason,
                    message = "Required evaluation input is unavailable.",
                    source = rule.metricKey.wireName,
                ),
            )
        } else {
            RuleReadiness.Abstain(
                warning =
                    EvaluationWarning(
                        order = rule.order,
                        ruleId = rule.ruleId,
                        publicCode = optionalEvidenceCode(reason),
                        internalCause = reason,
                        message = "Optional evaluation evidence is unavailable.",
                        source = rule.metricKey.wireName,
                    ),
                abstention =
                    Abstention(
                        order = rule.order,
                        ruleId = rule.ruleId,
                        publicCode = optionalEvidenceCode(reason),
                        internalCause = reason,
                        disposition = EvidenceDisposition.ABSTAIN,
                        message = "Optional evaluation evidence was not used.",
                        component = rule.ruleId,
                    ),
            )
        }

    private fun optionalEvidenceCode(reason: MetricIssueCode): PublicEvidenceCode =
        when (reason) {
            MetricIssueCode.MODEL_ABSTAINED -> PublicEvidenceCode.MODEL_ABSTAINED
            MetricIssueCode.SOURCE_STALE,
            MetricIssueCode.SOURCE_FUTURE_TIMESTAMP,
            -> PublicEvidenceCode.OPTIONAL_EVIDENCE_STALE

            MetricIssueCode.SOURCE_ERROR -> PublicEvidenceCode.OPTIONAL_EVIDENCE_ERROR
            MetricIssueCode.SOURCE_INCOMPLETE,
            MetricIssueCode.SOURCE_OVERSIZED,
            -> PublicEvidenceCode.OPTIONAL_EVIDENCE_INCOMPLETE
            MetricIssueCode.SOURCE_MISSING,
            MetricIssueCode.NOT_APPLICABLE,
            MetricIssueCode.PORTFOLIO_CONTEXT_UNAVAILABLE,
            MetricIssueCode.BROKERAGE_UNAVAILABLE,
            MetricIssueCode.PAPER_PORTFOLIO_UNAVAILABLE,
            MetricIssueCode.DISCLOSURE_UNAVAILABLE,
            -> PublicEvidenceCode.OPTIONAL_EVIDENCE_MISSING
        }

    private fun requiredIssueCode(
        metricKey: MetricKey,
        reason: MetricIssueCode,
    ): PublicIssueCode =
        when (metricKey) {
            MetricKey.ORDER_AMOUNT_KRW,
            MetricKey.CURRENT_PRICE_KRW,
            ->
                if (reason == MetricIssueCode.SOURCE_STALE || reason == MetricIssueCode.SOURCE_FUTURE_TIMESTAMP) {
                    PublicIssueCode.PRICE_STALE
                } else {
                    PublicIssueCode.PRICE_MISSING
                }

            MetricKey.ASSET_WEIGHT,
            MetricKey.GOLD_ETF_ETN_WEIGHT,
            MetricKey.OWNER_POSITION_QUANTITY,
            MetricKey.PORTFOLIO_EQUITY_KRW,
            ->
                when (reason) {
                    MetricIssueCode.SOURCE_STALE,
                    MetricIssueCode.SOURCE_FUTURE_TIMESTAMP,
                    -> PublicIssueCode.BALANCE_STALE

                    MetricIssueCode.SOURCE_INCOMPLETE,
                    MetricIssueCode.SOURCE_OVERSIZED,
                    -> PublicIssueCode.BALANCE_PARTIAL
                    else -> PublicIssueCode.BROKERAGE_UNAVAILABLE
                }

            MetricKey.DAILY_LOSS_RATE,
            MetricKey.MDD,
            MetricKey.ANNUALIZED_VOLATILITY,
            ->
                if (reason == MetricIssueCode.SOURCE_INCOMPLETE || reason == MetricIssueCode.SOURCE_OVERSIZED) {
                    PublicIssueCode.RISK_SNAPSHOT_VERSION_MISMATCH
                } else {
                    PublicIssueCode.RISK_SNAPSHOT_MISSING
                }

            MetricKey.NEGATIVE_NEWS_SCORE -> PublicIssueCode.NEWS_EVIDENCE_UNAVAILABLE
            MetricKey.DISCLOSURE_RISK_SCORE ->
                if (reason == MetricIssueCode.SOURCE_INCOMPLETE || reason == MetricIssueCode.SOURCE_OVERSIZED) {
                    PublicIssueCode.DISCLOSURE_PARTIAL
                } else {
                    PublicIssueCode.DISCLOSURE_PROVIDER_ERROR
                }

            MetricKey.ETF_ETN_RISK_SCORE -> PublicIssueCode.INSTRUMENT_METADATA_UNAVAILABLE
            MetricKey.MARGIN_REQUIREMENT_KRW -> PublicIssueCode.MARGIN_CONTEXT_UNAVAILABLE
            MetricKey.DAILY_ORDER_COUNT -> PublicIssueCode.PORTFOLIO_CONTEXT_UNAVAILABLE
            MetricKey.HMM_RISK_OFF_PROBABILITY,
            MetricKey.MEAN_REVERSION_Z_SCORE,
            -> PublicIssueCode.RISK_SNAPSHOT_MISSING
        }
}
