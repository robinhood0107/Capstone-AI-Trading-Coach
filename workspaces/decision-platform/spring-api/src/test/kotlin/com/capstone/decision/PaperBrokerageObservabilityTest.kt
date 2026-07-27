package com.capstone.decision

import com.capstone.decision.application.brokerage.paper.PaperBrokerageObservability
import com.capstone.decision.application.brokerage.paper.PaperMetricPriceBasis
import com.capstone.decision.application.brokerage.paper.PaperRejectionReason
import io.micrometer.core.instrument.simple.SimpleMeterRegistry
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.extension.ExtendWith
import org.slf4j.MDC
import org.springframework.boot.test.system.CapturedOutput
import org.springframework.boot.test.system.OutputCaptureExtension
import java.time.Duration

@ExtendWith(OutputCaptureExtension::class)
class PaperBrokerageObservabilityTest {
    @Test
    fun `paper metric tag는 basis와 rejection enum allowlist만 사용한다`(output: CapturedOutput) {
        val registry = SimpleMeterRegistry()
        val observability = PaperBrokerageObservability(registry)

        observability.recordFilled(
            duration = Duration.ofMillis(12),
            basis = PaperMetricPriceBasis.LAST_QUOTE,
            orderId = "ord_paper_${"a".repeat(32)}",
            decisionId = "dec_${"b".repeat(32)}",
            requestId = "req-safe",
        )
        observability.recordRejected(PaperRejectionReason.DATA_STALE)

        assertEquals(1L, registry.find(PaperBrokerageObservability.FILL_TIMER).timer()?.count())
        assertEquals(
            1.0,
            registry
                .find(PaperBrokerageObservability.PRICE_BASIS_COUNTER)
                .tag("basis", "LAST_QUOTE")
                .counter()
                ?.count(),
        )
        assertEquals(
            1.0,
            registry
                .find(PaperBrokerageObservability.REJECTED_COUNTER)
                .tag("reason", "DATA_STALE")
                .counter()
                ?.count(),
        )
        registry.meters.forEach { meter ->
            assertTrue(meter.id.tags.all { it.key in setOf("basis", "reason") })
            assertFalse(meter.id.tags.any { it.value.contains("acct_") })
        }
        assertTrue(output.out.contains("paper.fill"))
        assertTrue(output.out.contains("INTERNAL_PAPER"))
        assertFalse(output.out.contains("accountNumber", ignoreCase = true))
        assertFalse(output.out.contains("raw-provider"))
        assertNull(MDC.get("orderId"))
        assertNull(MDC.get("decisionId"))
    }

    @Test
    fun `paper observability enum은 계약된 bounded 값만 가진다`() {
        assertEquals(
            setOf("LAST_QUOTE", "PREVIOUS_CLOSE"),
            PaperMetricPriceBasis.entries.map { it.name }.toSet(),
        )
        assertEquals(
            setOf(
                "VALIDATION",
                "NOT_FOUND",
                "DECISION_EXPIRED",
                "CONFLICT",
                "IDEMPOTENCY_CONFLICT",
                "IDEMPOTENCY_IN_PROGRESS",
                "DATA_STALE",
                "RISK_BLOCKED",
                "RISK_UNAVAILABLE",
                "BROKERAGE_UNAVAILABLE",
            ),
            PaperRejectionReason.entries.map { it.name }.toSet(),
        )
    }
}
