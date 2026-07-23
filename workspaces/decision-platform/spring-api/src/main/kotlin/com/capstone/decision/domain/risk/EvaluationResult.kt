package com.capstone.decision.domain.risk

import java.math.BigDecimal

enum class EvaluationAction {
    ALLOW,
    WARN,
    HOLD,
    BLOCK,
}

enum class PublicIssueCode {
    BALANCE_PARTIAL,
    BALANCE_STALE,
    BROKERAGE_UNAVAILABLE,
    DISCLOSURE_PARTIAL,
    DISCLOSURE_PROVIDER_ERROR,
    INSTRUMENT_METADATA_UNAVAILABLE,
    MARGIN_CONTEXT_UNAVAILABLE,
    NEWS_EVIDENCE_UNAVAILABLE,
    PORTFOLIO_CONTEXT_UNAVAILABLE,
    PRICE_MISSING,
    PRICE_STALE,
    PRINCIPLE_CONTEXT_UNAVAILABLE,
    RISK_SNAPSHOT_MISSING,
    RISK_SNAPSHOT_VERSION_MISMATCH,
    SOURCE_DEADLINE_EXCEEDED,
}

enum class PublicEvidenceCode {
    MODEL_ABSTAINED,
    NOT_APPLICABLE_V1,
    OPTIONAL_EVIDENCE_ERROR,
    OPTIONAL_EVIDENCE_INCOMPLETE,
    OPTIONAL_EVIDENCE_MISSING,
    OPTIONAL_EVIDENCE_STALE,
}

enum class EvidenceDisposition {
    ABSTAIN,
    NOT_APPLICABLE,
}

data class Violation(
    val order: Int,
    val ruleId: String,
    val severity: RuleSeverity,
    val message: String,
    val metricValue: BigDecimal,
    val threshold: BigDecimal,
) {
    init {
        require(order in 1..14)
        requireBounded(ruleId, "Violation rule ID", EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        requireBounded(message, "Violation message", EvaluationBounds.MAX_SAFE_MESSAGE_CHARS)
    }
}

data class EvaluationIssue(
    val order: Int,
    val ruleId: String,
    val publicCode: PublicIssueCode,
    val internalCause: MetricIssueCode,
    val message: String,
    val source: String,
) {
    init {
        require(order in 1..14)
        requireBounded(ruleId, "Issue rule ID", EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        requireBounded(message, "Issue message", EvaluationBounds.MAX_SAFE_MESSAGE_CHARS)
        requireBounded(source, "Issue source", EvaluationBounds.MAX_ID_OR_CODE_CHARS)
    }
}

data class EvaluationWarning(
    val order: Int,
    val ruleId: String,
    val publicCode: PublicEvidenceCode,
    val internalCause: MetricIssueCode,
    val message: String,
    val source: String,
) {
    init {
        require(order in 1..14)
        requireBounded(ruleId, "Warning rule ID", EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        requireBounded(message, "Warning message", EvaluationBounds.MAX_SAFE_MESSAGE_CHARS)
        requireBounded(source, "Warning source", EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(publicCode != PublicEvidenceCode.NOT_APPLICABLE_V1) {
            "NOT_APPLICABLE_V1 cannot be emitted as a warning."
        }
    }
}

data class Abstention(
    val order: Int,
    val ruleId: String,
    val publicCode: PublicEvidenceCode,
    val internalCause: MetricIssueCode,
    val disposition: EvidenceDisposition,
    val message: String,
    val component: String,
) {
    init {
        require(order in 1..14)
        requireBounded(ruleId, "Abstention rule ID", EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        requireBounded(message, "Abstention message", EvaluationBounds.MAX_SAFE_MESSAGE_CHARS)
        requireBounded(component, "Abstention component", EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(
            (disposition == EvidenceDisposition.NOT_APPLICABLE) ==
                (publicCode == PublicEvidenceCode.NOT_APPLICABLE_V1),
        ) {
            "NOT_APPLICABLE disposition and public code must be paired."
        }
        require(
            disposition != EvidenceDisposition.NOT_APPLICABLE ||
                internalCause == MetricIssueCode.NOT_APPLICABLE,
        ) {
            "NOT_APPLICABLE disposition requires the matching internal cause."
        }
    }
}

sealed interface RuleOutcome {
    data class Passed(
        val order: Int,
        val ruleId: String,
    ) : RuleOutcome

    data class Violated(
        val violation: Violation,
    ) : RuleOutcome

    data class Hold(
        val issue: EvaluationIssue,
    ) : RuleOutcome

    data class Warning(
        val warning: EvaluationWarning,
    ) : RuleOutcome

    data class Abstained(
        val abstention: Abstention,
    ) : RuleOutcome

    data class NotApplicable(
        val order: Int,
        val ruleId: String,
        val reason: String,
    ) : RuleOutcome
}

data class EvaluationResult(
    val action: EvaluationAction,
    val violations: List<Violation>,
    val issues: List<EvaluationIssue>,
    val warnings: List<EvaluationWarning>,
    val abstentions: List<Abstention>,
) {
    init {
        require(violations.size <= EvaluationBounds.MAX_VIOLATIONS)
        require(issues.size <= EvaluationBounds.MAX_ISSUES)
        require(warnings.size <= EvaluationBounds.MAX_WARNINGS)
        require(abstentions.size <= EvaluationBounds.MAX_ABSTENTIONS)
        require(violations == violations.sortedWith(VIOLATION_ORDER)) {
            "Evaluation violations must use canonical order."
        }
        require(issues == issues.sortedWith(ISSUE_ORDER)) {
            "Evaluation issues must use canonical order."
        }
        require(warnings == warnings.sortedWith(WARNING_ORDER)) {
            "Evaluation warnings must use canonical order."
        }
        require(abstentions == abstentions.sortedWith(ABSTENTION_ORDER)) {
            "Evaluation abstentions must use canonical order."
        }
        val warningPairs =
            warnings
                .groupingBy { warning -> warning.ruleId to warning.publicCode }
                .eachCount()
        val abstentionPairs =
            abstentions
                .filter { abstention -> abstention.disposition == EvidenceDisposition.ABSTAIN }
                .groupingBy { abstention -> abstention.ruleId to abstention.publicCode }
                .eachCount()
        require(warningPairs == abstentionPairs) {
            "Optional evidence warnings and abstentions must be paired by rule ID and code."
        }
        val expectedAction =
            when {
                violations.any { violation -> violation.severity == RuleSeverity.BLOCK } -> EvaluationAction.BLOCK
                issues.isNotEmpty() -> EvaluationAction.HOLD
                violations.isNotEmpty() ||
                    warnings.isNotEmpty() ||
                    abstentions.any { abstention -> abstention.disposition == EvidenceDisposition.ABSTAIN } ->
                    EvaluationAction.WARN
                else -> EvaluationAction.ALLOW
            }
        require(action == expectedAction) {
            "Evaluation action violated BLOCK, HOLD, WARN, ALLOW precedence."
        }
    }
}

// 결과 우선순위와 배열 정렬을 한 곳에서 고정해 port 완료 순서가 wire 결과를 바꾸지 못하게 한다.
class DeterministicAggregator {
    fun aggregate(outcomes: List<RuleOutcome>): EvaluationResult {
        val violations =
            outcomes
                .filterIsInstance<RuleOutcome.Violated>()
                .map(RuleOutcome.Violated::violation)
                .sortedWith(VIOLATION_ORDER)
                .requireWithin(EvaluationBounds.MAX_VIOLATIONS, "violations")
        val issues =
            outcomes
                .filterIsInstance<RuleOutcome.Hold>()
                .map(RuleOutcome.Hold::issue)
                .sortedWith(ISSUE_ORDER)
                .requireWithin(EvaluationBounds.MAX_ISSUES, "issues")
        val warnings =
            outcomes
                .filterIsInstance<RuleOutcome.Warning>()
                .map(RuleOutcome.Warning::warning)
                .sortedWith(WARNING_ORDER)
                .requireWithin(EvaluationBounds.MAX_WARNINGS, "warnings")
        val explicitAbstentions =
            outcomes
                .filterIsInstance<RuleOutcome.Abstained>()
                .map(RuleOutcome.Abstained::abstention)
        val notApplicableAbstentions =
            outcomes
                .filterIsInstance<RuleOutcome.NotApplicable>()
                .map {
                    Abstention(
                        order = it.order,
                        ruleId = it.ruleId,
                        publicCode = PublicEvidenceCode.NOT_APPLICABLE_V1,
                        internalCause = MetricIssueCode.NOT_APPLICABLE,
                        disposition = EvidenceDisposition.NOT_APPLICABLE,
                        message = "Rule is not applicable to this evaluation.",
                        component = it.ruleId,
                    )
                }
        val abstentions =
            (explicitAbstentions + notApplicableAbstentions)
                .sortedWith(ABSTENTION_ORDER)
                .requireWithin(EvaluationBounds.MAX_ABSTENTIONS, "abstentions")

        val action =
            when {
                violations.any { it.severity == RuleSeverity.BLOCK } -> EvaluationAction.BLOCK
                issues.isNotEmpty() -> EvaluationAction.HOLD
                violations.isNotEmpty() ||
                    warnings.isNotEmpty() ||
                    abstentions.any { it.disposition == EvidenceDisposition.ABSTAIN } ->
                    EvaluationAction.WARN
                else -> EvaluationAction.ALLOW
            }
        return EvaluationResult(action, violations, issues, warnings, abstentions)
    }

    private fun <T> List<T>.requireWithin(
        maximum: Int,
        name: String,
    ): List<T> {
        require(size <= maximum) { "Evaluation $name exceeded the S2.2 contract bound." }
        return this
    }
}

private val VIOLATION_ORDER =
    compareBy<Violation>(
        Violation::order,
        Violation::ruleId,
        { violation -> violation.severity.name },
        Violation::message,
        { violation -> CanonicalJson.decimal(violation.metricValue) },
        { violation -> CanonicalJson.decimal(violation.threshold) },
    )
private val ISSUE_ORDER =
    compareBy<EvaluationIssue>(
        EvaluationIssue::order,
        EvaluationIssue::ruleId,
        { issue -> issue.publicCode.name },
        EvaluationIssue::source,
        EvaluationIssue::message,
        { issue -> issue.internalCause.name },
    )
private val WARNING_ORDER =
    compareBy<EvaluationWarning>(
        EvaluationWarning::order,
        EvaluationWarning::ruleId,
        { warning -> warning.publicCode.name },
        EvaluationWarning::source,
        EvaluationWarning::message,
        { warning -> warning.internalCause.name },
    )
private val ABSTENTION_ORDER =
    compareBy<Abstention>(
        Abstention::order,
        Abstention::ruleId,
        { abstention -> abstention.publicCode.name },
        Abstention::component,
        { abstention -> abstention.disposition.name },
        Abstention::message,
        { abstention -> abstention.internalCause.name },
    )

private fun requireBounded(
    value: String,
    name: String,
    maximum: Int,
) {
    require(value.isNotBlank() && value.length <= maximum) { "$name is outside the S2.2 contract bound." }
}
