package com.capstone.decision.domain.risk

import com.capstone.decision.domain.principle.EvidenceRequirement
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertInstanceOf
import org.junit.jupiter.api.Test
import java.math.BigDecimal
import java.time.Instant

class MetricReadinessPolicyTest {
    @Test
    fun `oversized disclosure evidence becomes partial public HOLD without exposing internal detail`() {
        val result =
            policy.classify(
                candidate(
                    metricKey = MetricKey.DISCLOSURE_RISK_SCORE,
                    requirement = EvidenceRequirement.REQUIRED,
                ),
                snapshot(
                    MetricCell.Incomplete(MetricIssueCode.SOURCE_OVERSIZED),
                    metricKey = MetricKey.DISCLOSURE_RISK_SCORE,
                ),
            )

        val hold = assertInstanceOf(RuleReadiness.Hold::class.java, result)
        assertEquals(PublicIssueCode.DISCLOSURE_PARTIAL, hold.issue.publicCode)
        assertEquals(MetricIssueCode.SOURCE_OVERSIZED, hold.issue.internalCause)
    }

    private val policy = MetricReadinessPolicy()

    @Test
    fun `disabled and non-applicable rules never become pass or warning`() {
        assertEquals(
            RuleReadiness.NotApplicable("RULE_DISABLED"),
            policy.classify(candidate(enabled = false), snapshot(MetricCell.Missing(MetricIssueCode.SOURCE_MISSING))),
        )
        assertEquals(
            RuleReadiness.NotApplicable("CONTEXT_NOT_APPLICABLE"),
            policy.classify(
                candidate(applicable = false),
                snapshot(MetricCell.Error(MetricIssueCode.SOURCE_ERROR)),
            ),
        )
    }

    @Test
    fun `required hard metric failures hold and never reach evaluator`() {
        val unavailable =
            listOf(
                MetricCell.Missing(MetricIssueCode.SOURCE_MISSING),
                MetricCell.Stale(
                    observedAt = NOW.minusSeconds(301),
                    freshUntil = NOW.minusNanos(1),
                    reason = MetricIssueCode.SOURCE_STALE,
                ),
                MetricCell.Error(MetricIssueCode.SOURCE_ERROR),
                MetricCell.Incomplete(MetricIssueCode.SOURCE_INCOMPLETE),
                MetricCell.NotApplicable(MetricIssueCode.NOT_APPLICABLE),
            )

        unavailable.forEach { cell ->
            val result = policy.classify(candidate(requirement = EvidenceRequirement.REQUIRED), snapshot(cell))
            assertInstanceOf(RuleReadiness.Hold::class.java, result)
            assertEquals("max_position_per_asset", (result as RuleReadiness.Hold).issue.ruleId)
        }
    }

    @Test
    fun `optional evidence failures abstain with warning but never hold`() {
        val unavailable =
            listOf(
                MetricCell.Missing(MetricIssueCode.SOURCE_MISSING),
                MetricCell.Stale(
                    observedAt = NOW.minusSeconds(301),
                    freshUntil = NOW.minusNanos(1),
                    reason = MetricIssueCode.SOURCE_STALE,
                ),
                MetricCell.Error(MetricIssueCode.SOURCE_ERROR),
                MetricCell.Incomplete(MetricIssueCode.SOURCE_INCOMPLETE),
                MetricCell.Abstained(MetricIssueCode.MODEL_ABSTAINED),
                MetricCell.NotApplicable(MetricIssueCode.NOT_APPLICABLE),
            )

        unavailable.forEach { cell ->
            val result =
                policy.classify(
                    candidate(ruleId = "negative_news_guard", requirement = EvidenceRequirement.OPTIONAL),
                    snapshot(cell),
                )
            assertInstanceOf(RuleReadiness.Abstain::class.java, result)
            assertEquals("negative_news_guard", (result as RuleReadiness.Abstain).abstention.ruleId)
        }
    }

    @Test
    fun `available metric is fresh at ttl equality and stale one nanosecond later`() {
        val available =
            MetricCell.Available(
                value = MetricValue.Decimal(BigDecimal("0.15"), 2, MetricUnit.RATIO),
                observedAt = NOW.minusSeconds(300),
                retrievedAt = NOW.minusSeconds(299),
                freshUntil = NOW,
                source = MetricSource.KIS_MOCK,
                sourceRef = SOURCE_REF,
                sourceVersion = "price-v1",
            )

        assertInstanceOf(
            RuleReadiness.Ready::class.java,
            policy.classify(candidate(), snapshot(available, evaluationAsOf = NOW)),
        )
        assertInstanceOf(
            RuleReadiness.Hold::class.java,
            policy.classify(candidate(), snapshot(available, evaluationAsOf = NOW.plusNanos(1))),
        )
    }

    @Test
    fun `future observation fails closed according to evidence requirement`() {
        val future =
            MetricCell.Available(
                value = MetricValue.Decimal(BigDecimal("0.15"), 2, MetricUnit.RATIO),
                observedAt = NOW.plusNanos(1),
                retrievedAt = NOW.plusNanos(1),
                freshUntil = NOW.plusSeconds(300),
                source = MetricSource.INTERNAL,
                sourceRef = SOURCE_REF,
                sourceVersion = "risk-v1",
            )

        assertInstanceOf(
            RuleReadiness.Hold::class.java,
            policy.classify(candidate(requirement = EvidenceRequirement.REQUIRED), snapshot(future)),
        )
        assertInstanceOf(
            RuleReadiness.Abstain::class.java,
            policy.classify(candidate(requirement = EvidenceRequirement.OPTIONAL), snapshot(future)),
        )
    }

    @Test
    fun `source cannot declare an applicable required rule not applicable`() {
        assertInstanceOf(
            RuleReadiness.Hold::class.java,
            policy.classify(
                candidate(requirement = EvidenceRequirement.REQUIRED),
                snapshot(MetricCell.NotApplicable(MetricIssueCode.NOT_APPLICABLE)),
            ),
        )
    }

    private fun candidate(
        ruleId: String = "max_position_per_asset",
        metricKey: MetricKey = MetricKey.ASSET_WEIGHT,
        enabled: Boolean = true,
        applicable: Boolean = true,
        requirement: EvidenceRequirement = EvidenceRequirement.REQUIRED,
    ): CandidateRule =
        CandidateRule(
            order = 2,
            ruleId = ruleId,
            metricKey = metricKey,
            operator = RuleOperator.LESS_THAN_OR_EQUAL,
            threshold = BigDecimal("0.15"),
            thresholdScale = 4,
            severity = RuleSeverity.BLOCK,
            evidenceRequirement = requirement,
            enabled = enabled,
            applicable = applicable,
        )

    private fun snapshot(
        cell: MetricCell<MetricValue>,
        evaluationAsOf: Instant = NOW,
        metricKey: MetricKey = MetricKey.ASSET_WEIGHT,
    ): MetricSnapshot =
        MetricSnapshot.fixture(
            evaluationAsOf = evaluationAsOf,
            metrics = mapOf(metricKey to cell),
        )

    companion object {
        private val NOW = Instant.parse("2030-01-02T03:04:05Z")
        private const val SOURCE_REF = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    }
}
