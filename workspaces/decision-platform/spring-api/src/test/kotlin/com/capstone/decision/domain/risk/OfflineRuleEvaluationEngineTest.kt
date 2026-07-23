package com.capstone.decision.domain.risk

import com.capstone.decision.domain.principle.EvidenceRequirement
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import java.math.BigDecimal
import java.time.Instant

class OfflineRuleEvaluationEngineTest {
    private val engine = OfflineRuleEvaluationEngine()

    @Test
    fun `fourteen dispositions separate real violations holds abstentions and n-a`() {
        val metrics =
            passingMetrics().toMutableMap().apply {
                this[MetricKey.ORDER_AMOUNT_KRW] = availableWhole(500_001, MetricUnit.KRW)
                this[MetricKey.ASSET_WEIGHT] = MetricCell.Missing(MetricIssueCode.SOURCE_MISSING)
                this[MetricKey.NEGATIVE_NEWS_SCORE] = MetricCell.Error(MetricIssueCode.SOURCE_ERROR)
            }

        val result = engine.evaluate(canonicalRuleSet(), MetricSnapshot.fixture(metrics = metrics))

        assertEquals(EvaluationAction.BLOCK, result.action)
        assertEquals(listOf("max_single_order_amount"), result.violations.map(Violation::ruleId))
        assertEquals(listOf("max_position_per_asset"), result.issues.map(EvaluationIssue::ruleId))
        assertEquals(listOf("negative_news_guard"), result.warnings.map(EvaluationWarning::ruleId))
        assertEquals(
            listOf("negative_news_guard", "ad_leading_room_guard"),
            result.abstentions.map(Abstention::ruleId),
        )
        assertEquals(EvidenceDisposition.ABSTAIN, result.abstentions.first().disposition)
        assertEquals(EvidenceDisposition.NOT_APPLICABLE, result.abstentions.last().disposition)
    }

    @Test
    fun `disabled optional evidence is n-a without warning even when metric is absent`() {
        val rules =
            canonicalRules().map { rule ->
                if (rule.ruleId == "disclosure_risk_guard") rule.copy(enabled = false) else rule
            }
        val metrics = passingMetrics() - MetricKey.DISCLOSURE_RISK_SCORE

        val result =
            engine.evaluate(
                CanonicalEvaluationRuleSet.of(rules),
                MetricSnapshot.fixture(metrics = metrics),
            )

        assertEquals(EvaluationAction.ALLOW, result.action)
        assertEquals(emptyList<EvaluationWarning>(), result.warnings)
        assertEquals(
            listOf("disclosure_risk_guard", "ad_leading_room_guard"),
            result.abstentions.map(Abstention::ruleId),
        )
        assertEquals(EvidenceDisposition.NOT_APPLICABLE, result.abstentions.first().disposition)
    }

    @Test
    fun `partial or structurally shifted rule set is rejected before evaluation`() {
        val partial = canonicalRules().dropLast(1)
        val shifted = canonicalRules().map { if (it.order == 10) it.copy(order = 11) else it }

        assertThrows(IllegalArgumentException::class.java) {
            CanonicalEvaluationRuleSet.of(partial)
        }
        assertThrows(IllegalArgumentException::class.java) {
            CanonicalEvaluationRuleSet.of(shifted)
        }
    }

    @Test
    fun `freshness transition changes both semantic hash and evaluation action`() {
        val ready = MetricSnapshot.fixture(evaluationAsOf = NOW, metrics = passingMetrics())
        val readyAsset = ready.metric(MetricKey.ASSET_WEIGHT) as MetricCell.Available
        val stale =
            ready.copy(
                metrics =
                    ready.metrics +
                        (
                            MetricKey.ASSET_WEIGHT to
                                readyAsset.copy(freshUntil = NOW.minusNanos(1))
                        ),
            )
        val hashes = SnapshotHashService()

        assertEquals(EvaluationAction.ALLOW, engine.evaluate(canonicalRuleSet(), ready).action)
        assertEquals(EvaluationAction.HOLD, engine.evaluate(canonicalRuleSet(), stale).action)
        org.junit.jupiter.api.Assertions.assertNotEquals(
            hashes.semanticInputHash(ready),
            hashes.semanticInputHash(stale),
        )
    }

    private fun canonicalRuleSet(): CanonicalEvaluationRuleSet = CanonicalEvaluationRuleSet.of(canonicalRules())

    private fun canonicalRules(): List<CandidateRule> =
        listOf(
            thresholdRule(1, "max_position_per_asset", MetricKey.ASSET_WEIGHT, "<=", "0.15", RuleSeverity.BLOCK),
            thresholdRule(2, "max_gold_etf_etn_weight", MetricKey.GOLD_ETF_ETN_WEIGHT, "<=", "0.30", RuleSeverity.BLOCK),
            thresholdRule(3, "max_single_order_amount", MetricKey.ORDER_AMOUNT_KRW, "<=", "500000", RuleSeverity.BLOCK),
            thresholdRule(4, "daily_loss_guard", MetricKey.DAILY_LOSS_RATE, ">=", "-0.03", RuleSeverity.BLOCK),
            thresholdRule(5, "mdd_guard", MetricKey.MDD, ">=", "-0.15", RuleSeverity.BLOCK),
            thresholdRule(6, "max_daily_orders", MetricKey.DAILY_ORDER_COUNT, "<=", "3", RuleSeverity.WARN),
            thresholdRule(
                7,
                "negative_news_guard",
                MetricKey.NEGATIVE_NEWS_SCORE,
                "<=",
                "0.70",
                RuleSeverity.WARN,
                EvidenceRequirement.OPTIONAL,
            ),
            thresholdRule(
                8,
                "disclosure_risk_guard",
                MetricKey.DISCLOSURE_RISK_SCORE,
                "<=",
                "0.70",
                RuleSeverity.WARN,
                EvidenceRequirement.OPTIONAL,
            ),
            thresholdRule(
                9,
                "high_volatility_guard",
                MetricKey.ANNUALIZED_VOLATILITY,
                "<=",
                "0.35",
                RuleSeverity.BLOCK,
                origin = RuleOrigin.SYSTEM_MANAGED,
            ),
            metaRule(10, "data_freshness_guard", RuleExecutionKind.READINESS),
            thresholdRule(
                11,
                "hmm_risk_off_guard",
                MetricKey.HMM_RISK_OFF_PROBABILITY,
                "<=",
                "0.65",
                RuleSeverity.WARN,
                EvidenceRequirement.OPTIONAL,
                RuleOrigin.SYSTEM_MANAGED,
            ),
            thresholdRule(
                12,
                "mean_reversion_warning",
                MetricKey.MEAN_REVERSION_Z_SCORE,
                "<=",
                "2",
                RuleSeverity.WARN,
                EvidenceRequirement.OPTIONAL,
                RuleOrigin.SYSTEM_MANAGED,
            ),
            thresholdRule(
                13,
                "etf_etn_risk_check",
                MetricKey.ETF_ETN_RISK_SCORE,
                "<=",
                "0.70",
                RuleSeverity.WARN,
                EvidenceRequirement.OPTIONAL,
                RuleOrigin.SYSTEM_MANAGED,
            ),
            metaRule(14, "ad_leading_room_guard", RuleExecutionKind.NOT_APPLICABLE),
        )

    private fun passingMetrics(): Map<MetricKey, MetricCell<MetricValue>> =
        mapOf(
            MetricKey.ASSET_WEIGHT to availableDecimal("0.15", MetricUnit.RATIO),
            MetricKey.GOLD_ETF_ETN_WEIGHT to availableDecimal("0.30", MetricUnit.RATIO),
            MetricKey.ORDER_AMOUNT_KRW to availableWhole(500_000, MetricUnit.KRW),
            MetricKey.DAILY_LOSS_RATE to availableDecimal("-0.03", MetricUnit.RATIO),
            MetricKey.MDD to availableDecimal("-0.15", MetricUnit.RATIO),
            MetricKey.DAILY_ORDER_COUNT to availableWhole(3, MetricUnit.COUNT),
            MetricKey.NEGATIVE_NEWS_SCORE to availableDecimal("0.70", MetricUnit.RATIO),
            MetricKey.DISCLOSURE_RISK_SCORE to availableDecimal("0.70", MetricUnit.RATIO),
            MetricKey.ANNUALIZED_VOLATILITY to availableDecimal("0.35", MetricUnit.RATIO),
            MetricKey.HMM_RISK_OFF_PROBABILITY to availableDecimal("0.65", MetricUnit.RATIO),
            MetricKey.MEAN_REVERSION_Z_SCORE to availableDecimal("2", MetricUnit.ABS_Z_SCORE),
            MetricKey.ETF_ETN_RISK_SCORE to availableDecimal("0.70", MetricUnit.RATIO),
        )

    private fun thresholdRule(
        order: Int,
        ruleId: String,
        metric: MetricKey,
        operator: String,
        threshold: String,
        severity: RuleSeverity,
        requirement: EvidenceRequirement = EvidenceRequirement.REQUIRED,
        origin: RuleOrigin = RuleOrigin.PUBLIC_PRINCIPLE,
    ): CandidateRule =
        CandidateRule(
            order = order,
            ruleId = ruleId,
            metricKey = metric,
            operator = RuleOperator.fromWire(operator),
            threshold = BigDecimal(threshold),
            thresholdScale = BigDecimal(threshold).scale().coerceAtLeast(0),
            severity = severity,
            evidenceRequirement = requirement,
            enabled = true,
            applicable = true,
            origin = origin,
        )

    // meta disposition은 threshold evaluator에 전달되지 않으며 catalog order/kind만 canonical set에서 검증한다.
    private fun metaRule(
        order: Int,
        ruleId: String,
        executionKind: RuleExecutionKind,
    ): CandidateRule =
        CandidateRule(
            order = order,
            ruleId = ruleId,
            metricKey = MetricKey.ASSET_WEIGHT,
            operator = RuleOperator.LESS_THAN_OR_EQUAL,
            threshold = BigDecimal.ZERO,
            thresholdScale = 0,
            severity = RuleSeverity.WARN,
            evidenceRequirement = EvidenceRequirement.OPTIONAL,
            enabled = true,
            applicable = true,
            origin = RuleOrigin.SYSTEM_MANAGED,
            executionKind = executionKind,
        )

    private fun availableWhole(
        value: Long,
        unit: MetricUnit,
    ): MetricCell.Available<MetricValue> = available(MetricValue.Whole(value, unit))

    private fun availableDecimal(
        value: String,
        unit: MetricUnit,
    ): MetricCell.Available<MetricValue> {
        val decimal = BigDecimal(value)
        return available(MetricValue.Decimal(decimal, decimal.scale().coerceAtLeast(0), unit))
    }

    private fun available(value: MetricValue): MetricCell.Available<MetricValue> =
        MetricCell.Available(
            value = value,
            observedAt = NOW.minusSeconds(1),
            retrievedAt = NOW,
            freshUntil = NOW.plusSeconds(1),
            source = MetricSource.INTERNAL,
            sourceRef = "c".repeat(64),
            sourceVersion = "fixture-v1",
        )

    companion object {
        private val NOW = Instant.parse("2030-01-02T03:04:05Z")
    }
}
