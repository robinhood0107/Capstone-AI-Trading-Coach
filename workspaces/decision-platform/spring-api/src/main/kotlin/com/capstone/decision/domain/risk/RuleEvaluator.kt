package com.capstone.decision.domain.risk

// READY metric과 enabled threshold rule만 비교하는 Spring-free 순수 함수다. null은 이 경계에서만 PASS를 뜻한다.
class RuleEvaluator {
    fun evaluate(
        rule: EnabledReadyRule,
        snapshot: ReadyMetricSnapshot,
    ): Violation? {
        val metric = snapshot.value(rule.metricKey)
        require(metric.unit == rule.metricKey.unit) { "Rule metric unit mismatch." }
        val value = metric.asBigDecimal()
        val violated =
            when (rule.operator) {
                RuleOperator.LESS_THAN_OR_EQUAL -> metric.compareTo(rule.threshold) > 0
                RuleOperator.GREATER_THAN_OR_EQUAL -> metric.compareTo(rule.threshold) < 0
            }
        if (!violated) {
            return null
        }
        return Violation(
            order = rule.order,
            ruleId = rule.ruleId,
            severity = rule.severity,
            message = "Rule threshold exceeded.",
            metricValue = value,
            threshold = rule.threshold,
        )
    }
}
