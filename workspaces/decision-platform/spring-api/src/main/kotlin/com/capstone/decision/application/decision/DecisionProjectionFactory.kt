package com.capstone.decision.application.decision

import com.capstone.decision.application.risk.OfflinePortfolioEvaluation
import com.capstone.decision.domain.decision.DecisionOutcomePolicy
import com.capstone.decision.domain.principle.EvidenceRequirement
import com.capstone.decision.domain.risk.MetricCell
import com.capstone.decision.domain.risk.MetricKey
import com.capstone.decision.domain.risk.MetricValue
import org.springframework.stereotype.Component
import tools.jackson.databind.ObjectMapper
import java.time.Duration
import java.time.Instant

/**
 * offline evaluator 결과를 S2.3 persisted response 한 모양으로 만들며 POST/GET/replay가 같은 projection을 공유한다.
 */
@Component
class DecisionProjectionFactory(
    private val objectMapper: ObjectMapper,
) {
    private val outcomePolicy = DecisionOutcomePolicy()

    fun create(
        decisionId: String,
        createdAt: Instant,
        configuredValidity: Duration,
        evaluation: OfflinePortfolioEvaluation,
    ): DecisionProjection {
        val snapshot = requireNotNull(evaluation.snapshot)
        val semanticInputHash = requireNotNull(evaluation.semanticInputHash)
        val snapshotArtifactHash = requireNotNull(evaluation.snapshotArtifactHash)
        val enforced = outcomePolicy.apply(evaluation.principle.mode, evaluation.result.action)
        val validUntil = validUntil(createdAt, configuredValidity, evaluation)
        val disclosureRiskItem =
            (snapshot.metric(MetricKey.DISCLOSURE_RISK_SCORE) as? MetricCell.Available<MetricValue>)
                ?.let { disclosureMetric ->
                    snapshot.disclosureEvidence?.let { evidence ->
                        val violation =
                            evaluation.result.violations.singleOrNull {
                                it.ruleId == DISCLOSURE_RULE_ID
                            }
                        DecisionRiskItemProjection(
                            metric = MetricKey.DISCLOSURE_RISK_SCORE.wireName,
                            value = disclosureMetric.value.asBigDecimal(),
                            severity = violation?.severity?.name ?: "ALLOW",
                            source = "OPENDART",
                            eventCodes = evidence.eventCodes.sorted(),
                            mappingVersion = evidence.mappingVersion,
                            sourceRefs = evidence.sourceRefs.sorted(),
                        )
                    }
                }
        val riskDecision =
            RiskDecisionProjection(
                schemaVersion = RISK_DECISION_SCHEMA_VERSION,
                evaluationId = snapshot.evaluationId,
                decisionId = decisionId,
                validUntil = validUntil,
                catalogVersion = snapshot.systemRuleCatalogVersion,
                readinessPolicyVersion = snapshot.readinessPolicyVersion,
                decision = enforced.outcome.name,
                mode = evaluation.principle.mode.name,
                canSubmitOrder = enforced.canSubmitOrder,
                principleVersionId = evaluation.principle.principleVersionId.value,
                principleVersion = evaluation.principle.version,
                portfolioSource = evaluation.portfolioSource.name,
                semanticInputHash = semanticInputHash,
                snapshotArtifactHash = snapshotArtifactHash,
                violations =
                    evaluation.result.violations.map { violation ->
                        DecisionViolationProjection(
                            ruleId = violation.ruleId,
                            severity = violation.severity.name,
                            message = violation.message,
                            metricValue = violation.metricValue,
                            threshold = violation.threshold,
                        )
                    },
                issues =
                    evaluation.result.issues.map { issue ->
                        DecisionIssueProjection(
                            ruleId = issue.ruleId,
                            code = issue.publicCode.name,
                            message = issue.message,
                            source = issue.source,
                        )
                    },
                warnings =
                    evaluation.result.warnings.map { warning ->
                        DecisionWarningProjection(
                            ruleId = warning.ruleId,
                            code = warning.publicCode.name,
                            message = warning.message,
                            source = warning.source,
                        )
                    },
                abstentions =
                    evaluation.result.abstentions.map { abstention ->
                        DecisionAbstentionProjection(
                            ruleId = abstention.ruleId,
                            code = abstention.publicCode.name,
                            disposition = abstention.disposition.name,
                            message = abstention.message,
                            component = abstention.component,
                        )
                    },
                riskItems = listOfNotNull(disclosureRiskItem),
            )
        return DecisionProjection(
            decisionId = decisionId,
            createdAt = createdAt,
            validUntil = validUntil,
            principleId = evaluation.principle.principleId.value,
            principleVersionId = evaluation.principle.principleVersionId.value,
            principleVersion = evaluation.principle.version,
            portfolioSource = evaluation.portfolioSource.name,
            mode = evaluation.principle.mode.name,
            enforcementAction = enforced.enforcementAction.name,
            riskDecision = riskDecision,
        )
    }

    fun canonicalJson(projection: DecisionProjection): String = objectMapper.writeValueAsString(projection)

    fun fromCanonicalJson(value: String): DecisionProjection = objectMapper.readValue(value, DecisionProjection::class.java)

    private fun validUntil(
        evaluationAsOf: Instant,
        configuredValidity: Duration,
        evaluation: OfflinePortfolioEvaluation,
    ): Instant {
        val snapshot = requireNotNull(evaluation.snapshot)
        val hardMetricKeys =
            HARD_READINESS_METRICS +
                evaluation.principle.rules
                    .filter { it.enabled && it.evidenceRequirement == EvidenceRequirement.REQUIRED }
                    .map { MetricKey.fromWire(it.metric) }
        val boundaries =
            hardMetricKeys
                .mapNotNull { key ->
                    (snapshot.metric(key) as? MetricCell.Available<MetricValue>)?.freshUntil
                }.filterNot { it.isBefore(evaluationAsOf) }
        val selected =
            (boundaries + evaluationAsOf.plus(configuredValidity)).minOrNull()
                ?: evaluationAsOf.plus(configuredValidity)
        // PostgreSQL timestamptz의 microsecond 정밀도에서도 생성 즉시 만료된 Decision을 저장하지 않는다.
        return if (selected.isAfter(evaluationAsOf)) {
            selected
        } else {
            evaluationAsOf.plus(MINIMUM_PERSISTED_VALIDITY)
        }
    }

    private companion object {
        const val RISK_DECISION_SCHEMA_VERSION = "s2-2-risk-decision/v1"
        const val DISCLOSURE_RULE_ID = "disclosure_risk_guard"
        val MINIMUM_PERSISTED_VALIDITY: Duration = Duration.ofNanos(1_000)
        val HARD_READINESS_METRICS =
            setOf(
                MetricKey.CURRENT_PRICE_KRW,
                MetricKey.OWNER_POSITION_QUANTITY,
                MetricKey.PORTFOLIO_EQUITY_KRW,
                MetricKey.MARGIN_REQUIREMENT_KRW,
                MetricKey.DAILY_LOSS_RATE,
                MetricKey.MDD,
                MetricKey.ANNUALIZED_VOLATILITY,
            )
    }
}
