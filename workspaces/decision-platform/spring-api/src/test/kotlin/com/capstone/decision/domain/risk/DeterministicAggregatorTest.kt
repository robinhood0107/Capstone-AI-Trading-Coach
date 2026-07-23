package com.capstone.decision.domain.risk

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import java.math.BigDecimal

class DeterministicAggregatorTest {
    private val aggregator = DeterministicAggregator()

    @Test
    fun `action precedence is block over hold over warn over allow`() {
        val warning = warning(12, "mean_reversion_warning", PublicEvidenceCode.MODEL_ABSTAINED)
        val abstention = abstention(12, "mean_reversion_warning")
        val issue = issue(2, "max_position_per_asset")
        val block =
            Violation(
                order = 1,
                ruleId = "max_single_order_amount",
                severity = RuleSeverity.BLOCK,
                message = "Rule threshold exceeded.",
                metricValue = BigDecimal("500001"),
                threshold = BigDecimal("500000"),
            )

        assertEquals(EvaluationAction.ALLOW, aggregator.aggregate(emptyList()).action)
        assertEquals(
            EvaluationAction.WARN,
            aggregator
                .aggregate(
                    listOf(
                        RuleOutcome.Warning(warning),
                        RuleOutcome.Abstained(abstention),
                    ),
                ).action,
        )
        assertEquals(
            EvaluationAction.HOLD,
            aggregator
                .aggregate(
                    listOf(
                        RuleOutcome.Warning(warning),
                        RuleOutcome.Abstained(abstention),
                        RuleOutcome.Hold(issue),
                    ),
                ).action,
        )
        assertEquals(
            EvaluationAction.BLOCK,
            aggregator
                .aggregate(
                    listOf(
                        RuleOutcome.Warning(warning),
                        RuleOutcome.Abstained(abstention),
                        RuleOutcome.Hold(issue),
                        RuleOutcome.Violated(block),
                    ),
                ).action,
        )
    }

    @Test
    fun `result collections use stable rule order and code regardless of input order`() {
        val outcomes =
            listOf(
                RuleOutcome.Warning(
                    warning(12, "mean_reversion_warning", PublicEvidenceCode.OPTIONAL_EVIDENCE_STALE),
                ),
                RuleOutcome.Abstained(
                    abstention(
                        12,
                        "mean_reversion_warning",
                        PublicEvidenceCode.OPTIONAL_EVIDENCE_STALE,
                    ),
                ),
                RuleOutcome.Abstained(
                    abstention(11, "hmm_risk_off_guard"),
                ),
                RuleOutcome.Warning(
                    warning(11, "hmm_risk_off_guard", PublicEvidenceCode.MODEL_ABSTAINED),
                ),
                RuleOutcome.Hold(
                    issue(3, "max_gold_etf_etn_weight", PublicIssueCode.BALANCE_STALE),
                ),
                RuleOutcome.Hold(
                    issue(2, "max_position_per_asset"),
                ),
                RuleOutcome.NotApplicable(14, "ad_leading_room_guard", "SOURCE_NOT_AVAILABLE"),
            )

        val forward = aggregator.aggregate(outcomes)
        val reverse = aggregator.aggregate(outcomes.reversed())

        assertEquals(forward, reverse)
        assertEquals(
            listOf("max_position_per_asset", "max_gold_etf_etn_weight"),
            forward.issues.map(EvaluationIssue::ruleId),
        )
        assertEquals(listOf("hmm_risk_off_guard", "mean_reversion_warning"), forward.warnings.map(EvaluationWarning::ruleId))
        assertEquals(
            listOf("hmm_risk_off_guard", "mean_reversion_warning", "ad_leading_room_guard"),
            forward.abstentions.map(Abstention::ruleId),
        )
        assertEquals(EvidenceDisposition.NOT_APPLICABLE, forward.abstentions.last().disposition)
    }

    @Test
    fun `same outcomes remain byte-equivalent across one hundred shuffled completion orders`() {
        val outcomes =
            listOf(
                RuleOutcome.Warning(warning(11, "hmm_risk_off_guard", PublicEvidenceCode.MODEL_ABSTAINED)),
                RuleOutcome.Hold(issue(2, "max_gold_etf_etn_weight")),
                RuleOutcome.Abstained(abstention(11, "hmm_risk_off_guard")),
                RuleOutcome.NotApplicable(14, "ad_leading_room_guard", "SOURCE_NOT_AVAILABLE"),
            )
        val expected = aggregator.aggregate(outcomes)

        repeat(100) { seed ->
            assertEquals(expected, aggregator.aggregate(outcomes.shuffled(kotlin.random.Random(seed))))
        }
    }

    @Test
    fun `same rule and code use source and component as deterministic tie breakers`() {
        val outcomes =
            listOf("GBM", "BSM").flatMap { component ->
                listOf(
                    RuleOutcome.Warning(
                        EvaluationWarning(
                            order = 10,
                            ruleId = "data_freshness_guard",
                            publicCode = PublicEvidenceCode.OPTIONAL_EVIDENCE_MISSING,
                            internalCause = MetricIssueCode.SOURCE_MISSING,
                            message = "Optional evidence unavailable.",
                            source = component,
                        ),
                    ),
                    RuleOutcome.Abstained(
                        Abstention(
                            order = 10,
                            ruleId = "data_freshness_guard",
                            publicCode = PublicEvidenceCode.OPTIONAL_EVIDENCE_MISSING,
                            internalCause = MetricIssueCode.SOURCE_MISSING,
                            disposition = EvidenceDisposition.ABSTAIN,
                            message = "Optional evidence unavailable.",
                            component = component,
                        ),
                    ),
                )
            }
        val expected = aggregator.aggregate(outcomes)

        assertEquals(listOf("BSM", "GBM"), expected.warnings.map(EvaluationWarning::source))
        assertEquals(listOf("BSM", "GBM"), expected.abstentions.map(Abstention::component))
        repeat(100) { seed ->
            assertEquals(expected, aggregator.aggregate(outcomes.shuffled(kotlin.random.Random(seed))))
        }
    }

    @Test
    fun `not applicable abstention alone remains allow`() {
        val result =
            aggregator.aggregate(
                listOf(RuleOutcome.NotApplicable(14, "ad_leading_room_guard", "SOURCE_NOT_AVAILABLE")),
            )

        assertEquals(EvaluationAction.ALLOW, result.action)
        assertEquals(EvidenceDisposition.NOT_APPLICABLE, result.abstentions.single().disposition)
    }

    @Test
    fun `result bounds fail fast instead of silently truncating evidence`() {
        val outcomes =
            (1..15).map { index ->
                RuleOutcome.Hold(
                    issue(
                        order = (index - 1) % 14 + 1,
                        ruleId = "required_source_$index",
                    ),
                )
            }

        assertThrows<IllegalArgumentException> { aggregator.aggregate(outcomes) }
    }

    @Test
    fun `final result rejects optional warning and abstention without a bidirectional pair`() {
        assertThrows<IllegalArgumentException> {
            EvaluationResult(
                action = EvaluationAction.WARN,
                violations = emptyList(),
                issues = emptyList(),
                warnings = listOf(warning(11, "hmm_risk_off_guard", PublicEvidenceCode.MODEL_ABSTAINED)),
                abstentions = emptyList(),
            )
        }
        assertThrows<IllegalArgumentException> {
            EvaluationResult(
                action = EvaluationAction.WARN,
                violations = emptyList(),
                issues = emptyList(),
                warnings = emptyList(),
                abstentions = listOf(abstention(11, "hmm_risk_off_guard")),
            )
        }
    }

    @Test
    fun `final result action must match block hold warn allow precedence`() {
        assertThrows<IllegalArgumentException> {
            EvaluationResult(
                action = EvaluationAction.ALLOW,
                violations = emptyList(),
                issues = listOf(issue(2, "max_position_per_asset")),
                warnings = emptyList(),
                abstentions = emptyList(),
            )
        }
    }

    @Test
    fun `not-applicable code and disposition cannot contradict the wire contract`() {
        assertThrows<IllegalArgumentException> {
            warning(14, "ad_leading_room_guard", PublicEvidenceCode.NOT_APPLICABLE_V1)
        }
        assertThrows<IllegalArgumentException> {
            abstention(
                14,
                "ad_leading_room_guard",
                PublicEvidenceCode.NOT_APPLICABLE_V1,
            )
        }
        assertThrows<IllegalArgumentException> {
            Abstention(
                order = 14,
                ruleId = "ad_leading_room_guard",
                publicCode = PublicEvidenceCode.OPTIONAL_EVIDENCE_MISSING,
                internalCause = MetricIssueCode.NOT_APPLICABLE,
                disposition = EvidenceDisposition.NOT_APPLICABLE,
                message = "Rule is not applicable.",
                component = "ad_leading_room_guard",
            )
        }
    }

    private fun warning(
        order: Int,
        ruleId: String,
        code: PublicEvidenceCode = PublicEvidenceCode.OPTIONAL_EVIDENCE_STALE,
    ): EvaluationWarning =
        EvaluationWarning(
            order = order,
            ruleId = ruleId,
            publicCode = code,
            internalCause = MetricIssueCode.SOURCE_STALE,
            message = "Optional evidence unavailable.",
            source = ruleId,
        )

    private fun issue(
        order: Int,
        ruleId: String,
        code: PublicIssueCode = PublicIssueCode.BROKERAGE_UNAVAILABLE,
    ): EvaluationIssue =
        EvaluationIssue(
            order = order,
            ruleId = ruleId,
            publicCode = code,
            internalCause = MetricIssueCode.SOURCE_MISSING,
            message = "Required input unavailable.",
            source = ruleId,
        )

    private fun abstention(
        order: Int,
        ruleId: String,
        code: PublicEvidenceCode = PublicEvidenceCode.MODEL_ABSTAINED,
    ): Abstention =
        Abstention(
            order = order,
            ruleId = ruleId,
            publicCode = code,
            internalCause = MetricIssueCode.MODEL_ABSTAINED,
            disposition = EvidenceDisposition.ABSTAIN,
            message = "Model abstained.",
            component = ruleId,
        )
}
