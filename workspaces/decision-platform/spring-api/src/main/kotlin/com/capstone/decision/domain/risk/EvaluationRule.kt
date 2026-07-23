package com.capstone.decision.domain.risk

import com.capstone.decision.domain.principle.EvidenceRequirement
import java.math.BigDecimal

enum class RuleOperator(
    val wireValue: String,
) {
    LESS_THAN_OR_EQUAL("<="),
    GREATER_THAN_OR_EQUAL(">="),
    ;

    companion object {
        fun fromWire(value: String): RuleOperator =
            entries.singleOrNull { it.wireValue == value }
                ?: throw IllegalArgumentException("Unsupported rule operator.")
    }
}

enum class RuleSeverity {
    WARN,
    BLOCK,
}

enum class RuleOrigin {
    PUBLIC_PRINCIPLE,
    SYSTEM_MANAGED,
}

enum class RuleExecutionKind {
    THRESHOLD,
    READINESS,
    NOT_APPLICABLE,
}

data class CandidateRule(
    val order: Int,
    val ruleId: String,
    val metricKey: MetricKey,
    val operator: RuleOperator,
    val threshold: BigDecimal,
    val thresholdScale: Int,
    val severity: RuleSeverity,
    val evidenceRequirement: EvidenceRequirement,
    val enabled: Boolean,
    val applicable: Boolean,
    val origin: RuleOrigin = RuleOrigin.PUBLIC_PRINCIPLE,
    val executionKind: RuleExecutionKind = RuleExecutionKind.THRESHOLD,
) {
    init {
        require(order in 1..14)
        require(ruleId.isNotBlank() && ruleId.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(thresholdScale in 0..18)
        require(threshold.stripTrailingZeros().scale().coerceAtLeast(0) <= thresholdScale)
    }

    fun enabledReady(): EnabledReadyRule {
        require(enabled && applicable && executionKind == RuleExecutionKind.THRESHOLD)
        return EnabledReadyRule(
            order = order,
            ruleId = ruleId,
            metricKey = metricKey,
            operator = operator,
            threshold = threshold,
            thresholdScale = thresholdScale,
            severity = severity,
            evidenceRequirement = evidenceRequirement,
        )
    }
}

// 타입 이름 자체가 disabled/HOLD/ABSTAIN/N/A가 evaluator에 들어오지 않았음을 나타낸다.
data class EnabledReadyRule(
    val order: Int,
    val ruleId: String,
    val metricKey: MetricKey,
    val operator: RuleOperator,
    val threshold: BigDecimal,
    val thresholdScale: Int,
    val severity: RuleSeverity,
    val evidenceRequirement: EvidenceRequirement,
)

// canonical resource에서 조립된 14개 disposition 전체만 engine에 전달해 rule 누락 ALLOW를 막는다.
class CanonicalEvaluationRuleSet private constructor(
    val rules: List<CandidateRule>,
) {
    companion object {
        fun of(rules: List<CandidateRule>): CanonicalEvaluationRuleSet {
            val canonical = rules.sortedWith(compareBy(CandidateRule::order, CandidateRule::ruleId))
            require(canonical.size == 14) { "Canonical evaluation rule set must contain fourteen rules." }
            require(canonical.map(CandidateRule::order) == (1..14).toList()) {
                "Canonical evaluation orders are invalid."
            }
            require(canonical.map(CandidateRule::ruleId).distinct().size == 14) {
                "Canonical evaluation rule IDs are invalid."
            }
            require(canonical.count { it.executionKind == RuleExecutionKind.THRESHOLD } == 12)
            require(canonical.count { it.executionKind == RuleExecutionKind.READINESS } == 1)
            require(canonical.count { it.executionKind == RuleExecutionKind.NOT_APPLICABLE } == 1)
            require(canonical.take(8).all { it.origin == RuleOrigin.PUBLIC_PRINCIPLE })
            require(canonical.drop(8).all { it.origin == RuleOrigin.SYSTEM_MANAGED })
            require(canonical.take(8).all { it.executionKind == RuleExecutionKind.THRESHOLD })
            require(canonical[8].executionKind == RuleExecutionKind.THRESHOLD)
            require(canonical[9].executionKind == RuleExecutionKind.READINESS)
            require(canonical.subList(10, 13).all { it.executionKind == RuleExecutionKind.THRESHOLD })
            require(canonical[13].executionKind == RuleExecutionKind.NOT_APPLICABLE)
            require(canonical.take(6).all { it.evidenceRequirement == EvidenceRequirement.REQUIRED }) {
                "Hard public rule evidence must be required."
            }
            require(canonical[8].evidenceRequirement == EvidenceRequirement.REQUIRED) {
                "Hard system volatility evidence must be required."
            }
            return CanonicalEvaluationRuleSet(canonical)
        }
    }
}
