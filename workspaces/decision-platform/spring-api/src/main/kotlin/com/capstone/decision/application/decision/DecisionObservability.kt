package com.capstone.decision.application.decision

import com.capstone.decision.domain.risk.PublicIssueCode
import io.micrometer.core.instrument.Counter
import io.micrometer.core.instrument.MeterRegistry
import io.micrometer.core.instrument.Timer
import org.slf4j.LoggerFactory
import org.slf4j.MDC
import org.springframework.stereotype.Component
import java.time.Duration

enum class DecisionMetricOutcome {
    ALLOW,
    WARN,
    HOLD,
    BLOCK,
    ERROR,
}

enum class DecisionMetricMode {
    GUIDE,
    STRICT,
    UNPINNED,
}

/**
 * Decision 지표와 로그는 닫힌 enum tag와 reference-only 식별자만 받아 사용자·계좌·원문 증거 유출을 막는다.
 */
@Component
class DecisionObservability(
    private val meterRegistry: MeterRegistry,
) {
    fun recordTimer(
        outcome: DecisionMetricOutcome,
        mode: DecisionMetricMode,
        duration: Duration,
    ) {
        runCatching {
            Timer
                .builder(EVALUATE_TIMER)
                .tags(
                    "outcome",
                    outcome.name,
                    "mode",
                    mode.name,
                ).register(meterRegistry)
                .record(duration)
        }
    }

    fun recordPersisted(
        projection: DecisionProjection,
        requestId: String,
    ) {
        runCatching {
            // issues는 evaluator가 canonical order로 확정했으므로 code 문자열을 다시 정렬하지 않는다.
            val reason =
                projection.riskDecision.issues
                    .firstOrNull()
                    ?.code
            if (projection.riskDecision.decision == DecisionMetricOutcome.HOLD.name) {
                require(reason in FAIL_CLOSED_REASONS) {
                    "Persisted HOLD must expose an allowlisted public issue code."
                }
                Counter
                    .builder(FAIL_CLOSED_COUNTER)
                    .tag("reason", requireNotNull(reason))
                    .register(meterRegistry)
                    .increment()
            }
            log
                .atInfo()
                .addKeyValue("requestId", requestId)
                .addKeyValue("evaluationId", projection.riskDecision.evaluationId)
                .addKeyValue("decisionId", projection.decisionId)
                .addKeyValue("trace_id", MDC.get(TRACE_ID_MDC_KEY) ?: TRACE_UNAVAILABLE)
                .addKeyValue("span_id", MDC.get(SPAN_ID_MDC_KEY) ?: TRACE_UNAVAILABLE)
                .addKeyValue("outcome", projection.riskDecision.decision)
                .addKeyValue("publicReason", reason ?: NO_PUBLIC_REASON)
                .addKeyValue("mode", projection.mode)
                .addKeyValue("principleVersion", projection.principleVersion)
                .addKeyValue("catalogVersion", projection.riskDecision.catalogVersion)
                .addKeyValue("semanticInputHash", projection.riskDecision.semanticInputHash)
                .addKeyValue("snapshotArtifactHash", projection.riskDecision.snapshotArtifactHash)
                .log("decision.evaluated")
        }
    }

    companion object {
        const val EVALUATE_TIMER = "decision.evaluate"
        const val FAIL_CLOSED_COUNTER = "decision.fail_closed"
        const val TRACE_ID_MDC_KEY = "trace_id"
        const val SPAN_ID_MDC_KEY = "span_id"
        const val TRACE_UNAVAILABLE = "unavailable"
        const val NO_PUBLIC_REASON = "NONE"
        val FAIL_CLOSED_REASONS: Set<String> = PublicIssueCode.entries.map { it.name }.toSet()
        private val log = LoggerFactory.getLogger(DecisionObservability::class.java)
    }
}
