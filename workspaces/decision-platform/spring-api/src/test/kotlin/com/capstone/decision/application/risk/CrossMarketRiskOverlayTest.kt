package com.capstone.decision.application.risk

import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketAvailability
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketDecisionInput
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketEvidenceMode
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketExposure
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketExposureClassification
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketInputUnavailableException
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketOverlayConfig
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketOverlayStatus
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketRiskOverlay
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketRiskPort
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketRiskSnapshot
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketRuntimeMode
import com.capstone.decision.application.risk.crossmarket.v2.CrossMarketStorageMode
import com.capstone.decision.application.risk.port.EvaluationSourceRequest
import com.capstone.decision.application.risk.port.PortfolioContextRef
import com.capstone.decision.domain.risk.EvaluationAction
import com.capstone.decision.domain.risk.EvaluationIssue
import com.capstone.decision.domain.risk.EvaluationResult
import com.capstone.decision.domain.risk.MetricIssueCode
import com.capstone.decision.domain.risk.OrderIntentSnapshot
import com.capstone.decision.domain.risk.PortfolioSource
import com.capstone.decision.domain.risk.PublicIssueCode
import com.capstone.decision.domain.risk.RuleSeverity
import com.capstone.decision.domain.risk.Violation
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import java.math.BigDecimal
import java.time.Instant
import java.util.UUID
import java.util.concurrent.atomic.AtomicInteger

class CrossMarketRiskOverlayTest {
    @Test
    fun `OFF performs zero overlay evaluations and zero source calls`() {
        val calls = AtomicInteger()
        val overlay = CrossMarketRiskOverlay(countingPort(calls), CrossMarketOverlayConfig())

        val result = overlay.evaluate(request(), ALLOW)

        assertThat(result.finalAction).isEqualTo(EvaluationAction.ALLOW)
        assertThat(result.status).isEqualTo(CrossMarketOverlayStatus.NOT_EVALUATED)
        assertThat(calls).hasValue(0)
    }

    @Test
    fun `SHADOW observes trigger but never changes action`() {
        val overlay = CrossMarketRiskOverlay(port(input()), config(CrossMarketRuntimeMode.SHADOW))
        val result = overlay.evaluate(request(), ALLOW)
        assertThat(result.finalAction).isEqualTo(EvaluationAction.ALLOW)
        assertThat(result.status).isEqualTo(CrossMarketOverlayStatus.OBSERVED)
    }

    @Test
    fun `WARN_ONLY changes only fresh eligible new BUY ALLOW to WARN`() {
        val overlay = CrossMarketRiskOverlay(port(input()), config(CrossMarketRuntimeMode.WARN_ONLY))

        assertThat(overlay.evaluate(request(), ALLOW).finalAction).isEqualTo(EvaluationAction.WARN)
        assertThat(overlay.evaluate(request(side = "SELL"), ALLOW).finalAction).isEqualTo(EvaluationAction.ALLOW)
        val existing = input(classification = CrossMarketExposureClassification.EXISTING_POSITION)
        assertThat(
            CrossMarketRiskOverlay(port(existing), config(CrossMarketRuntimeMode.WARN_ONLY))
                .evaluate(request(), ALLOW)
                .finalAction,
        ).isEqualTo(EvaluationAction.ALLOW)
        assertThat(overlay.evaluate(request(), HOLD).finalAction).isEqualTo(EvaluationAction.HOLD)
        assertThat(overlay.evaluate(request(), BLOCK).finalAction).isEqualTo(EvaluationAction.BLOCK)
    }

    @Test
    fun `SHADOW-authored snapshot cannot be elevated by WARN_ONLY runtime config`() {
        val shadowOnly = input(runtimeMode = CrossMarketRuntimeMode.SHADOW)
        val result =
            CrossMarketRiskOverlay(port(shadowOnly), config(CrossMarketRuntimeMode.WARN_ONLY))
                .evaluate(request(), ALLOW)
        assertThat(result.status).isEqualTo(CrossMarketOverlayStatus.UNAVAILABLE)
        assertThat(result.finalAction).isEqualTo(EvaluationAction.ALLOW)
    }

    @Test
    fun `missing stale and mismatched exposure remain unavailable without HOLD or BLOCK`() {
        val missing =
            object : CrossMarketRiskPort {
                override fun load(request: EvaluationSourceRequest): CrossMarketDecisionInput =
                    throw CrossMarketInputUnavailableException("MISSING")
            }
        assertThat(
            CrossMarketRiskOverlay(missing, config(CrossMarketRuntimeMode.WARN_ONLY))
                .evaluate(request(), ALLOW)
                .finalAction,
        ).isEqualTo(EvaluationAction.ALLOW)

        val stale = input(staleAt = NOW.minusSeconds(1))
        val staleResult =
            CrossMarketRiskOverlay(port(stale), config(CrossMarketRuntimeMode.WARN_ONLY)).evaluate(request(), ALLOW)
        assertThat(staleResult.status).isEqualTo(CrossMarketOverlayStatus.STALE)
        assertThat(staleResult.finalAction).isEqualTo(EvaluationAction.ALLOW)

        val mismatch = input(exposureAvailableAt = NOW.minusSeconds(1))
        val mismatchResult =
            CrossMarketRiskOverlay(port(mismatch), config(CrossMarketRuntimeMode.WARN_ONLY)).evaluate(request(), ALLOW)
        assertThat(mismatchResult.status).isEqualTo(CrossMarketOverlayStatus.UNAVAILABLE)
        assertThat(mismatchResult.finalAction).isEqualTo(EvaluationAction.ALLOW)
    }

    @Test
    fun `missing frozen threshold is unavailable without source call or fallback`() {
        val calls = AtomicInteger()
        val overlay =
            CrossMarketRiskOverlay(
                countingPort(calls),
                CrossMarketOverlayConfig(mode = CrossMarketRuntimeMode.WARN_ONLY),
            )
        val result = overlay.evaluate(request(), ALLOW)
        assertThat(result.status).isEqualTo(CrossMarketOverlayStatus.UNAVAILABLE)
        assertThat(result.finalAction).isEqualTo(EvaluationAction.ALLOW)
        assertThat(calls).hasValue(0)
    }

    @Test
    fun `equivalent threshold scales retain WARN authority`() {
        val overlay =
            CrossMarketRiskOverlay(
                port(input(threshold = BigDecimal("95.00"))),
                config(CrossMarketRuntimeMode.WARN_ONLY, BigDecimal("95.0")),
            )

        val result = overlay.evaluate(request(), ALLOW)

        assertThat(result.status).isEqualTo(CrossMarketOverlayStatus.WARNED)
        assertThat(result.finalAction).isEqualTo(EvaluationAction.WARN)
    }

    @Test
    fun `invalid stored projection fails safe as unavailable`() {
        val invalid =
            object : CrossMarketRiskPort {
                override fun load(request: EvaluationSourceRequest): CrossMarketDecisionInput =
                    throw IllegalArgumentException("stored projection is invalid")
            }

        val result =
            CrossMarketRiskOverlay(invalid, config(CrossMarketRuntimeMode.WARN_ONLY))
                .evaluate(request(), ALLOW)

        assertThat(result.status).isEqualTo(CrossMarketOverlayStatus.UNAVAILABLE)
        assertThat(result.finalAction).isEqualTo(EvaluationAction.ALLOW)
    }

    @Test
    fun `ENFORCED P1 activation is rejected`() {
        assertThatThrownBy { CrossMarketOverlayConfig(mode = CrossMarketRuntimeMode.ENFORCED) }
            .isInstanceOf(IllegalArgumentException::class.java)
            .hasMessageContaining("MODE_NOT_APPROVED")
    }

    private fun config(
        mode: CrossMarketRuntimeMode,
        threshold: BigDecimal = THRESHOLD,
    ) = CrossMarketOverlayConfig(mode, threshold, THRESHOLD_HASH, CONFIG_HASH)

    private fun port(value: CrossMarketDecisionInput) =
        object : CrossMarketRiskPort {
            override fun load(request: EvaluationSourceRequest): CrossMarketDecisionInput = value
        }

    private fun countingPort(calls: AtomicInteger) =
        object : CrossMarketRiskPort {
            override fun load(request: EvaluationSourceRequest): CrossMarketDecisionInput {
                calls.incrementAndGet()
                return input()
            }
        }

    private fun input(
        classification: CrossMarketExposureClassification = CrossMarketExposureClassification.NEW_BUY,
        staleAt: Instant = NOW.plusSeconds(3600),
        exposureAvailableAt: Instant = AVAILABLE_AT,
        runtimeMode: CrossMarketRuntimeMode = CrossMarketRuntimeMode.WARN_ONLY,
        threshold: BigDecimal = THRESHOLD,
    ) = CrossMarketDecisionInput(
        snapshot =
            CrossMarketRiskSnapshot(
                UUID.fromString("11111111-1111-4111-8111-111111111111"),
                OWNER_SCOPE,
                "005930",
                AVAILABLE_AT,
                staleAt,
                CrossMarketEvidenceMode.SYNTHETIC_FIXTURE,
                CrossMarketStorageMode.STORED_SNAPSHOT,
                runtimeMode,
                CrossMarketAvailability.AVAILABLE,
                BigDecimal("98.125"),
                threshold,
                THRESHOLD_HASH,
                CONFIG_HASH,
                "4".repeat(64),
                "5".repeat(64),
            ),
        exposure = CrossMarketExposure("005930", classification, exposureAvailableAt, "6".repeat(64)),
    )

    private fun request(side: String = "BUY") =
        EvaluationSourceRequest(
            actorUserId = "usr_fixture",
            portfolioContext = PortfolioContextRef("ctx_fixture", PortfolioSource.INTERNAL_PAPER, OWNER_SCOPE),
            orderIntent =
                OrderIntentSnapshot("005930", side, "MARKET", 1, 10_000, 10_000, "1d", "strategy_fixture"),
            evaluationAsOf = NOW,
        )

    private companion object {
        val NOW: Instant = Instant.parse("2026-08-21T08:10:00Z")
        val AVAILABLE_AT: Instant = NOW.minusSeconds(60)
        val THRESHOLD = BigDecimal("97.5")
        val OWNER_SCOPE = "2".repeat(64)
        val THRESHOLD_HASH = "1".repeat(64)
        val CONFIG_HASH = "3".repeat(64)
        val ALLOW = EvaluationResult(EvaluationAction.ALLOW, emptyList(), emptyList(), emptyList(), emptyList())
        val HOLD =
            EvaluationResult(
                EvaluationAction.HOLD,
                emptyList(),
                listOf(
                    EvaluationIssue(
                        1,
                        "system.daily-loss-rate",
                        PublicIssueCode.PRICE_MISSING,
                        MetricIssueCode.SOURCE_MISSING,
                        "Stored source is unavailable.",
                        "fixture",
                    ),
                ),
                emptyList(),
                emptyList(),
            )
        val BLOCK =
            EvaluationResult(
                EvaluationAction.BLOCK,
                listOf(Violation(1, "system.daily-loss-rate", RuleSeverity.BLOCK, "Blocked.", BigDecimal.ONE, BigDecimal.ZERO)),
                emptyList(),
                emptyList(),
                emptyList(),
            )
    }
}
