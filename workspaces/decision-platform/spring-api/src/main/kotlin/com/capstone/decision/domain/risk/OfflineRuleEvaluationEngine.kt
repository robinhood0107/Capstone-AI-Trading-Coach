package com.capstone.decision.domain.risk

// catalog의 12 threshold + readiness matrix + N/A disposition을 deterministic offline 결과로 합친다.
class OfflineRuleEvaluationEngine(
    private val readinessPolicy: MetricReadinessPolicy = MetricReadinessPolicy(),
    private val ruleEvaluator: RuleEvaluator = RuleEvaluator(),
    private val aggregator: DeterministicAggregator = DeterministicAggregator(),
) {
    fun evaluate(
        ruleSet: CanonicalEvaluationRuleSet,
        snapshot: MetricSnapshot,
    ): EvaluationResult {
        val outcomes =
            ruleSet.rules
                .flatMap { rule ->
                    when (rule.executionKind) {
                        RuleExecutionKind.READINESS ->
                            listOf(RuleOutcome.Passed(rule.order, rule.ruleId))

                        RuleExecutionKind.NOT_APPLICABLE ->
                            listOf(RuleOutcome.NotApplicable(rule.order, rule.ruleId, "SOURCE_NOT_AVAILABLE"))

                        RuleExecutionKind.THRESHOLD -> thresholdOutcomes(rule, snapshot)
                    }
                }
        return aggregator.aggregate(outcomes)
    }

    private fun thresholdOutcomes(
        rule: CandidateRule,
        snapshot: MetricSnapshot,
    ): List<RuleOutcome> =
        when (val readiness = readinessPolicy.classify(rule, snapshot)) {
            is RuleReadiness.Ready -> {
                val violation = ruleEvaluator.evaluate(readiness.rule, readiness.snapshot)
                listOf(
                    violation?.let(RuleOutcome::Violated)
                        ?: RuleOutcome.Passed(rule.order, rule.ruleId),
                )
            }

            is RuleReadiness.Hold -> listOf(RuleOutcome.Hold(readiness.issue))
            is RuleReadiness.Abstain ->
                listOf(
                    RuleOutcome.Warning(readiness.warning),
                    RuleOutcome.Abstained(readiness.abstention),
                )

            is RuleReadiness.NotApplicable ->
                listOf(RuleOutcome.NotApplicable(rule.order, rule.ruleId, readiness.reason))
        }
}
