package com.capstone.decision

import com.capstone.decision.application.decision.DecisionIssueProjection
import com.capstone.decision.application.decision.DecisionMetricMode
import com.capstone.decision.application.decision.DecisionMetricOutcome
import com.capstone.decision.application.decision.DecisionObservability
import com.capstone.decision.application.decision.DecisionProjection
import com.capstone.decision.application.decision.DecisionRiskItemProjection
import com.capstone.decision.application.decision.RiskDecisionProjection
import com.capstone.decision.domain.risk.PublicIssueCode
import io.micrometer.core.instrument.simple.SimpleMeterRegistry
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.extension.ExtendWith
import org.springframework.boot.test.system.CapturedOutput
import org.springframework.boot.test.system.OutputCaptureExtension
import java.math.BigDecimal
import java.time.Duration
import java.time.Instant

@ExtendWith(OutputCaptureExtension::class)
class DecisionObservabilityTest {
    @Test
    fun `metric enums and tags are the exact bounded contract`() {
        assertEquals(
            setOf("ALLOW", "WARN", "HOLD", "BLOCK", "ERROR"),
            DecisionMetricOutcome.entries.map { it.name }.toSet(),
        )
        assertEquals(
            setOf("GUIDE", "STRICT", "UNKNOWN"),
            DecisionMetricMode.entries.map { it.name }.toSet(),
        )
        assertEquals(
            PublicIssueCode.entries.map { it.name }.toSet(),
            DecisionObservability.FAIL_CLOSED_REASONS,
        )
    }

    @Test
    fun `evaluated HOLD records one timer and one allowlisted fail closed counter without sensitive values`(
        output: CapturedOutput,
    ) {
        val registry = SimpleMeterRegistry()
        val observability = DecisionObservability(registry)
        val projection = holdProjection()

        observability.recordTimer(DecisionMetricOutcome.HOLD, DecisionMetricMode.GUIDE, Duration.ofMillis(25))
        observability.recordPersisted(projection, "req-safe")

        assertEquals(
            1L,
            registry
                .find("decision.evaluate")
                .tags("outcome", "HOLD", "mode", "GUIDE")
                .timer()
                ?.count(),
        )
        assertEquals(
            1.0,
            registry
                .find("decision.fail_closed")
                .tag("reason", PublicIssueCode.BROKERAGE_UNAVAILABLE.name)
                .counter()
                ?.count(),
        )
        registry.meters.forEach { meter ->
            assertTrue(meter.id.tags.all { it.key in setOf("outcome", "mode", "reason") })
            assertFalse(meter.id.tags.any { it.value.contains("secret", ignoreCase = true) })
        }

        assertTrue(output.out.contains("req-safe"))
        assertTrue(output.out.contains(projection.decisionId))
        assertTrue(output.out.contains(projection.riskDecision.evaluationId))
        assertTrue(output.out.contains(projection.riskDecision.semanticInputHash))
        assertFalse(output.out.contains("usr-secret"))
        assertFalse(output.out.contains("acct-secret"))
        assertFalse(output.out.contains("raw-provider-payload"))
        assertFalse(output.out.contains("source-ref-secret"))
    }

    @Test
    fun `technical failure records ERROR once and does not increment fail closed`() {
        val registry = SimpleMeterRegistry()
        val observability = DecisionObservability(registry)

        observability.recordTimer(DecisionMetricOutcome.ERROR, DecisionMetricMode.UNKNOWN, Duration.ofMillis(10))

        assertEquals(
            1L,
            registry
                .find("decision.evaluate")
                .tags("outcome", "ERROR", "mode", "UNKNOWN")
                .timer()
                ?.count(),
        )
        assertTrue(registry.find("decision.fail_closed").meters().isEmpty())
    }

    private fun holdProjection(): DecisionProjection {
        val createdAt = Instant.parse("2031-02-03T04:05:06Z")
        return DecisionProjection(
            decisionId = "dec_observability",
            createdAt = createdAt,
            validUntil = createdAt,
            principleId = "prn_observability",
            principleVersionId = "prv_observability",
            principleVersion = 3,
            portfolioSource = "KIS_MOCK",
            mode = "GUIDE",
            enforcementAction = "RE_EVALUATE",
            riskDecision =
                RiskDecisionProjection(
                    schemaVersion = "s2-2-risk-decision/v1",
                    evaluationId = "evl_observability",
                    decisionId = "dec_observability",
                    validUntil = createdAt,
                    catalogVersion = 1,
                    readinessPolicyVersion = "s2-3-readiness/v1",
                    decision = "HOLD",
                    mode = "GUIDE",
                    canSubmitOrder = false,
                    principleVersionId = "prv_observability",
                    principleVersion = 3,
                    portfolioSource = "KIS_MOCK",
                    semanticInputHash = "a".repeat(64),
                    snapshotArtifactHash = "b".repeat(64),
                    violations = emptyList(),
                    issues =
                        listOf(
                            DecisionIssueProjection(
                                ruleId = "balance_guard",
                                code = PublicIssueCode.BROKERAGE_UNAVAILABLE.name,
                                message = "usr-secret acct-secret raw-provider-payload",
                                source = "KIS_MOCK",
                            ),
                        ),
                    warnings = emptyList(),
                    abstentions = emptyList(),
                    riskItems =
                        listOf(
                            DecisionRiskItemProjection(
                                metric = "disclosure_risk_score",
                                value = BigDecimal("0.2"),
                                severity = "ALLOW",
                                source = "OPENDART",
                                eventCodes = listOf("CAPITAL_EVENT"),
                                mappingVersion = "mapping-v1",
                                sourceRefs = listOf("source-ref-secret"),
                            ),
                        ),
                ),
        )
    }
}
