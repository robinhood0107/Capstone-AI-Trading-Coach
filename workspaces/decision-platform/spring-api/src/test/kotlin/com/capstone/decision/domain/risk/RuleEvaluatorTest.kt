package com.capstone.decision.domain.risk

import com.capstone.decision.domain.principle.EvidenceRequirement
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.MethodSource
import java.math.BigDecimal
import java.time.Instant
import java.util.stream.Stream

class RuleEvaluatorTest {
    private val evaluator = RuleEvaluator()

    @ParameterizedTest(name = "{0}")
    @MethodSource("thresholdCases")
    fun `all twelve threshold rules pass equality and apply contract epsilon`(case: ThresholdCase) {
        val below = case.threshold.subtract(case.epsilon)
        val equal = case.threshold
        val above = case.threshold.add(case.epsilon)

        val belowResult = evaluator.evaluate(case.rule(), readySnapshot(case.metricKey, below))
        val equalResult = evaluator.evaluate(case.rule(), readySnapshot(case.metricKey, equal))
        val aboveResult = evaluator.evaluate(case.rule(), readySnapshot(case.metricKey, above))

        assertNull(equalResult)
        when (case.operator) {
            RuleOperator.LESS_THAN_OR_EQUAL -> {
                assertNull(belowResult)
                assertEquals(case.ruleId, aboveResult?.ruleId)
                assertEquals(above, aboveResult?.metricValue)
            }

            RuleOperator.GREATER_THAN_OR_EQUAL -> {
                assertEquals(case.ruleId, belowResult?.ruleId)
                assertEquals(below, belowResult?.metricValue)
                assertNull(aboveResult)
            }
        }
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("thresholdCases")
    fun `violation always carries non-null metric and threshold`(case: ThresholdCase) {
        val violatingValue =
            when (case.operator) {
                RuleOperator.LESS_THAN_OR_EQUAL -> case.threshold.add(case.epsilon)
                RuleOperator.GREATER_THAN_OR_EQUAL -> case.threshold.subtract(case.epsilon)
            }

        val violation = evaluator.evaluate(case.rule(), readySnapshot(case.metricKey, violatingValue))

        assertEquals(case.threshold, violation?.threshold)
        assertEquals(violatingValue, violation?.metricValue)
        assertTrue(violation?.message?.isNotBlank() == true)
    }

    private fun readySnapshot(
        key: MetricKey,
        value: BigDecimal,
    ): ReadyMetricSnapshot {
        val metricValue =
            when (key.unit) {
                MetricUnit.KRW,
                MetricUnit.COUNT,
                MetricUnit.QUANTITY,
                -> MetricValue.Whole(value.longValueExact(), key.unit)

                MetricUnit.RATIO,
                MetricUnit.SCORE,
                MetricUnit.ABS_Z_SCORE,
                -> MetricValue.Decimal(value, declaredScale = value.scale().coerceAtLeast(0), unit = key.unit)
            }
        return ReadyMetricSnapshot.of(
            evaluationAsOf = EVALUATION_AS_OF,
            metrics =
                mapOf(
                    key to
                        MetricCell.Available(
                            value = metricValue,
                            observedAt = EVALUATION_AS_OF.minusSeconds(1),
                            retrievedAt = EVALUATION_AS_OF,
                            freshUntil = EVALUATION_AS_OF.plusSeconds(1),
                            source = MetricSource.INTERNAL,
                            sourceRef = SOURCE_REF,
                            sourceVersion = "fixture-v1",
                        ),
                ),
        )
    }

    data class ThresholdCase(
        val ruleId: String,
        val metricKey: MetricKey,
        val operator: RuleOperator,
        val threshold: BigDecimal,
        val epsilon: BigDecimal,
        val severity: RuleSeverity,
        val evidenceRequirement: EvidenceRequirement,
        val thresholdScale: Int,
    ) {
        override fun toString(): String = ruleId

        fun rule(): EnabledReadyRule =
            EnabledReadyRule(
                order = RULE_ORDER.getValue(ruleId),
                ruleId = ruleId,
                metricKey = metricKey,
                operator = operator,
                threshold = threshold,
                thresholdScale = thresholdScale,
                severity = severity,
                evidenceRequirement = evidenceRequirement,
            )
    }

    companion object {
        private val EVALUATION_AS_OF = Instant.parse("2030-01-02T03:04:05Z")
        private const val SOURCE_REF = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        private val RULE_ORDER =
            listOf(
                "max_position_per_asset",
                "max_gold_etf_etn_weight",
                "max_single_order_amount",
                "daily_loss_guard",
                "mdd_guard",
                "max_daily_orders",
                "negative_news_guard",
                "disclosure_risk_guard",
                "high_volatility_guard",
                "hmm_risk_off_guard",
                "mean_reversion_warning",
                "etf_etn_risk_check",
            ).withIndex().associate { (index, ruleId) -> ruleId to index + 1 }

        @JvmStatic
        fun thresholdCases(): Stream<ThresholdCase> =
            Stream.of(
                case("max_single_order_amount", MetricKey.ORDER_AMOUNT_KRW, "<=", "500000", "1", "BLOCK", "REQUIRED", 0),
                case("max_position_per_asset", MetricKey.ASSET_WEIGHT, "<=", "0.15", "0.0001", "BLOCK", "REQUIRED", 4),
                case("max_gold_etf_etn_weight", MetricKey.GOLD_ETF_ETN_WEIGHT, "<=", "0.30", "0.0001", "BLOCK", "REQUIRED", 4),
                case("daily_loss_guard", MetricKey.DAILY_LOSS_RATE, ">=", "-0.03", "0.0001", "BLOCK", "REQUIRED", 4),
                case("mdd_guard", MetricKey.MDD, ">=", "-0.15", "0.0001", "BLOCK", "REQUIRED", 4),
                case("max_daily_orders", MetricKey.DAILY_ORDER_COUNT, "<=", "3", "1", "WARN", "REQUIRED", 0),
                case("negative_news_guard", MetricKey.NEGATIVE_NEWS_SCORE, "<=", "0.70", "0.0001", "WARN", "OPTIONAL", 4),
                case("disclosure_risk_guard", MetricKey.DISCLOSURE_RISK_SCORE, "<=", "0.70", "0.0001", "WARN", "OPTIONAL", 4),
                case("high_volatility_guard", MetricKey.ANNUALIZED_VOLATILITY, "<=", "0.35", "0.0001", "BLOCK", "REQUIRED", 4),
                case("hmm_risk_off_guard", MetricKey.HMM_RISK_OFF_PROBABILITY, "<=", "0.65", "0.0001", "WARN", "OPTIONAL", 4),
                case("mean_reversion_warning", MetricKey.MEAN_REVERSION_Z_SCORE, "<=", "2", "0.0001", "WARN", "OPTIONAL", 4),
                case("etf_etn_risk_check", MetricKey.ETF_ETN_RISK_SCORE, "<=", "0.70", "0.0001", "WARN", "OPTIONAL", 4),
            )

        private fun case(
            ruleId: String,
            metricKey: MetricKey,
            operator: String,
            threshold: String,
            epsilon: String,
            severity: String,
            requirement: String,
            thresholdScale: Int,
        ): ThresholdCase =
            ThresholdCase(
                ruleId = ruleId,
                metricKey = metricKey,
                operator = RuleOperator.fromWire(operator),
                threshold = BigDecimal(threshold),
                epsilon = BigDecimal(epsilon),
                severity = RuleSeverity.valueOf(severity),
                evidenceRequirement = EvidenceRequirement.valueOf(requirement),
                thresholdScale = thresholdScale,
            )
    }
}
