package com.capstone.decision.infrastructure.risk

import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketDecisionInput
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketRiskOverlay
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketRiskPort
import com.capstone.decision.application.risk.port.EvaluationSourceRequest
import com.capstone.decision.application.risk.port.PortfolioContextRef
import com.capstone.decision.domain.risk.EvaluationAction
import com.capstone.decision.domain.risk.EvaluationResult
import com.capstone.decision.domain.risk.OrderIntentSnapshot
import com.capstone.decision.domain.risk.PortfolioSource
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.springframework.boot.test.context.runner.ApplicationContextRunner
import java.time.Instant
import java.util.concurrent.atomic.AtomicInteger

class CrossMarketRuntimeConfigurationTest {
    @Test
    fun `default runtime is OFF and performs no source load`() {
        val calls = AtomicInteger()
        runner(countingPort(calls)).run { context ->
            assertThat(context).hasNotFailed()
            val result = context.getBean(CrossMarketRiskOverlay::class.java).evaluate(request(), ALLOW)
            assertThat(result.finalAction).isEqualTo(EvaluationAction.ALLOW)
            assertThat(calls).hasValue(0)
        }
    }

    @Test
    fun `non-OFF runtime requires the complete immutable threshold tuple`() {
        runner(unavailablePort())
            .withPropertyValues("app.cross-market.overlay.mode=WARN_ONLY")
            .run { context ->
                assertThat(context).hasFailed()
                assertThat(context.startupFailure).hasRootCauseMessage("FROZEN_THRESHOLD_REQUIRED")
            }

        runner(unavailablePort())
            .withPropertyValues(
                "app.cross-market.overlay.mode=SHADOW",
                "app.cross-market.overlay.threshold-percentile=97.5",
                "app.cross-market.overlay.threshold-artifact-hash=${"1".repeat(64)}",
            ).run { context ->
                assertThat(context).hasFailed()
                assertThat(context.startupFailure).hasRootCauseMessage("FROZEN_THRESHOLD_CONFIG_INCOMPLETE")
            }
    }

    @Test
    fun `approved tuple composes while ENFORCED is rejected`() {
        runner(unavailablePort())
            .withPropertyValues(
                "app.cross-market.overlay.mode=WARN_ONLY",
                "app.cross-market.overlay.threshold-percentile=97.5",
                "app.cross-market.overlay.threshold-artifact-hash=${"1".repeat(64)}",
                "app.cross-market.overlay.config-hash=${"2".repeat(64)}",
            ).run { context ->
                assertThat(context).hasNotFailed()
                assertThat(context).hasSingleBean(CrossMarketRiskOverlay::class.java)
            }

        runner(unavailablePort())
            .withPropertyValues(
                "app.cross-market.overlay.mode=ENFORCED",
                "app.cross-market.overlay.threshold-percentile=99",
                "app.cross-market.overlay.threshold-artifact-hash=${"1".repeat(64)}",
                "app.cross-market.overlay.config-hash=${"2".repeat(64)}",
            ).run { context ->
                assertThat(context).hasFailed()
                assertThat(context.startupFailure).hasRootCauseMessage("MODE_NOT_APPROVED")
            }
    }

    private fun runner(port: CrossMarketRiskPort) =
        ApplicationContextRunner()
            .withUserConfiguration(CrossMarketRuntimeConfiguration::class.java)
            .withBean(CrossMarketRiskPort::class.java, { port })

    private fun countingPort(calls: AtomicInteger) =
        object : CrossMarketRiskPort {
            override fun load(request: EvaluationSourceRequest): CrossMarketDecisionInput {
                calls.incrementAndGet()
                throw AssertionError("OFF must not load a stored snapshot")
            }
        }

    private fun unavailablePort() =
        object : CrossMarketRiskPort {
            override fun load(request: EvaluationSourceRequest): CrossMarketDecisionInput =
                throw AssertionError("configuration tests must not load stored data")
        }

    private fun request() =
        EvaluationSourceRequest(
            actorUserId = "usr_fixture",
            portfolioContext = PortfolioContextRef("ctx_fixture", PortfolioSource.INTERNAL_PAPER, "2".repeat(64)),
            orderIntent = OrderIntentSnapshot("005930", "BUY", "MARKET", 1, 10_000, 10_000, "1d", "strategy_fixture"),
            evaluationAsOf = Instant.parse("2026-08-21T08:10:00Z"),
        )

    private companion object {
        val ALLOW = EvaluationResult(EvaluationAction.ALLOW, emptyList(), emptyList(), emptyList(), emptyList())
    }
}
