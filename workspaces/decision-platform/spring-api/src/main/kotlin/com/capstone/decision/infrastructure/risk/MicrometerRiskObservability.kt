package com.capstone.decision.infrastructure.risk

import com.capstone.decision.application.risk.KillSwitchGatePort
import com.capstone.decision.application.risk.KillSwitchMutationResult
import com.capstone.decision.application.risk.RiskObservationPort
import com.capstone.decision.domain.risk.KillSwitchActorRole
import io.micrometer.core.instrument.Counter
import io.micrometer.core.instrument.Gauge
import io.micrometer.core.instrument.MeterRegistry
import io.micrometer.core.instrument.Timer
import org.slf4j.LoggerFactory
import java.time.Duration

/**
 * DB-backed gauge와 닫힌 enum label만 노출하며, 관측 실패는 이미 commit된 업무 결과를 뒤집지 않는다.
 */
class MicrometerRiskObservability(
    private val meterRegistry: MeterRegistry,
    gatePort: KillSwitchGatePort,
) : RiskObservationPort {
    init {
        Gauge
            .builder(KILL_SWITCH_STATE_GAUGE, gatePort) { port ->
                runCatching { if (port.readGate().active) 1.0 else 0.0 }
                    // 권한 원본을 읽지 못한 상태를 안전한 정지 방향으로 표현한다.
                    .getOrDefault(1.0)
            }.description("Current DB-backed global Kill Switch state")
            .register(meterRegistry)
    }

    override fun recordKillSwitchChanged(
        result: KillSwitchMutationResult,
        actorRole: KillSwitchActorRole,
        requestId: String,
    ) {
        if (!result.changed) {
            return
        }
        runCatching {
            Counter
                .builder(KILL_SWITCH_CHANGED_COUNTER)
                .tags(
                    "previous",
                    result.previousActive.toString(),
                    "next",
                    result.state.active.toString(),
                    "reasonClass",
                    result.state.reasonClass.name,
                    "actorRole",
                    actorRole.name,
                ).register(meterRegistry)
                .increment()
            if (result.invalidatedDecisionCount > 0) {
                Counter
                    .builder(DECISION_INVALIDATED_COUNTER)
                    .tag("reasonClass", "KILL_SWITCH_ACTIVATED")
                    .register(meterRegistry)
                    .increment(result.invalidatedDecisionCount.toDouble())
            }
            log
                .atInfo()
                .addKeyValue("requestId", requestId)
                .addKeyValue("previous", result.previousActive)
                .addKeyValue("next", result.state.active)
                .addKeyValue("reasonClass", result.state.reasonClass.name)
                .addKeyValue("actorRole", actorRole.name)
                .addKeyValue("generation", result.generation)
                .addKeyValue("invalidatedDecisionCount", result.invalidatedDecisionCount)
                .log("risk.kill_switch.changed")
        }
    }

    override fun recordPortfolioQuery(duration: Duration) {
        runCatching {
            Timer
                .builder(PORTFOLIO_QUERY_TIMER)
                .register(meterRegistry)
                .record(duration)
        }
    }

    companion object {
        const val KILL_SWITCH_CHANGED_COUNTER = "risk.kill_switch.changed"
        const val KILL_SWITCH_STATE_GAUGE = "risk.kill_switch.state"
        const val PORTFOLIO_QUERY_TIMER = "risk.portfolio.query"
        const val DECISION_INVALIDATED_COUNTER = "decision.invalidated"
        private val log = LoggerFactory.getLogger(MicrometerRiskObservability::class.java)
    }
}
