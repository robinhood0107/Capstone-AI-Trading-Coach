package com.capstone.decision.application.brokerage.paper

import io.micrometer.core.instrument.Counter
import io.micrometer.core.instrument.MeterRegistry
import io.micrometer.core.instrument.Timer
import org.slf4j.LoggerFactory
import org.slf4j.MDC
import org.springframework.stereotype.Component
import java.time.Duration

enum class PaperRejectionReason {
    VALIDATION,
    NOT_FOUND,
    DECISION_EXPIRED,
    CONFLICT,
    IDEMPOTENCY_CONFLICT,
    IDEMPOTENCY_IN_PROGRESS,
    DATA_STALE,
    RISK_BLOCKED,
    RISK_UNAVAILABLE,
    BROKERAGE_UNAVAILABLE,
}

enum class PaperMetricPriceBasis {
    LAST_QUOTE,
    PREVIOUS_CLOSE,
}

/**
 * paper 관측값은 닫힌 enum tag와 reference id만 사용한다.
 * 계좌·금액·raw key·provider payload는 metric과 stable log에 전달하지 않는다.
 */
@Component
class PaperBrokerageObservability(
    private val meterRegistry: MeterRegistry,
) {
    fun recordFilled(
        duration: Duration,
        basis: PaperMetricPriceBasis,
        orderId: String,
        decisionId: String,
        requestId: String,
    ) {
        runCatching {
            Timer
                .builder(FILL_TIMER)
                .register(meterRegistry)
                .record(duration)
            Counter
                .builder(PRICE_BASIS_COUNTER)
                .tag("basis", basis.name)
                .register(meterRegistry)
                .increment()
            MDC.putCloseable("orderId", orderId).use {
                MDC.putCloseable("decisionId", decisionId).use {
                    log
                        .atInfo()
                        .addKeyValue("requestId", requestId)
                        .addKeyValue("mode", "INTERNAL_PAPER")
                        .addKeyValue("basis", basis.name)
                        .log("paper.fill")
                }
            }
        }
    }

    fun recordRejected(reason: PaperRejectionReason) {
        runCatching {
            Counter
                .builder(REJECTED_COUNTER)
                .tag("reason", reason.name)
                .register(meterRegistry)
                .increment()
        }
    }

    companion object {
        const val FILL_TIMER = "brokerage.paper.fill"
        const val REJECTED_COUNTER = "brokerage.paper.rejected"
        const val PRICE_BASIS_COUNTER = "brokerage.paper.price_basis"
        private val log = LoggerFactory.getLogger(PaperBrokerageObservability::class.java)
    }
}
