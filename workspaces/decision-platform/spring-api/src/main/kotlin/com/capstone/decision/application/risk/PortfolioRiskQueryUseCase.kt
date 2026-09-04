package com.capstone.decision.application.risk

import com.capstone.decision.application.risk.port.PortfolioContextPort
import com.capstone.decision.application.risk.port.PortfolioContextRef
import com.capstone.decision.application.risk.port.PortfolioContextResolution
import com.capstone.decision.application.risk.port.PortfolioContextUnavailableReason
import com.capstone.decision.domain.risk.MetricCell
import com.capstone.decision.domain.risk.MetricIssueCode
import com.capstone.decision.domain.risk.MetricValue
import com.capstone.decision.domain.risk.PortfolioSource
import java.math.BigDecimal
import java.time.Clock
import java.time.Duration
import java.time.Instant

data class PortfolioRiskProjection(
    val asOf: Instant,
    val portfolioValue: Long?,
    val dailyPnlRate: BigDecimal?,
    val mdd: BigDecimal?,
    val var95: BigDecimal?,
    val cvar95: BigDecimal?,
    val realizedVolatility20d: BigDecimal?,
    val annualizedVolatility20d: BigDecimal?,
    val hmmRegime: String?,
    val hmmRegimeProbability: BigDecimal?,
    val killSwitchActive: Boolean,
    val dataFreshness: PortfolioRiskFreshness,
)

data class PortfolioRiskFreshness(
    val priceFresh: Boolean?,
    val signalFresh: Boolean?,
    val ragFresh: Boolean?,
)

data class PortfolioRiskWarning(
    val code: String,
    val fields: List<String>,
)

data class PortfolioRiskResult(
    val projection: PortfolioRiskProjection,
    val warnings: List<PortfolioRiskWarning>,
)

// 실제 계좌 관측이 내부 장부보다 우선한다.
private val PORTFOLIO_SOURCE_PRECEDENCE =
    listOf(PortfolioSource.KIS_MOCK, PortfolioSource.INTERNAL_PAPER)

class PortfolioRiskQueryUseCase(
    private val portfolioContextPort: PortfolioContextPort,
    private val snapshotAssembler: MetricSnapshotAssembler,
    private val killSwitchQueryPort: KillSwitchQueryPort,
    private val clock: Clock,
    private val observationPort: RiskObservationPort = RiskObservationPort.NONE,
) {
    fun get(actorUserId: String): PortfolioRiskResult {
        val startedAt = System.nanoTime()
        try {
            return query(actorUserId)
        } catch (exception: KillSwitchUnavailableException) {
            throw exception
        } catch (exception: Exception) {
            throw KillSwitchUnavailableException(exception)
        } finally {
            // read 결과나 fail-closed 오류를 관측성 backend 장애가 덮어쓰지 않게 한다.
            runCatching {
                observationPort.recordPortfolioQuery(Duration.ofNanos(System.nanoTime() - startedAt))
            }
        }
    }

    private fun query(actorUserId: String): PortfolioRiskResult {
        val asOf = clock.instant()
        val contextSelection = selectSingleContext(actorUserId)
        val assembly =
            contextSelection.context?.let { context ->
                snapshotAssembler.assemblePortfolioRisk(
                    PortfolioRiskAssemblyRequest(
                        actorUserId = actorUserId,
                        evaluationAsOf = asOf,
                        portfolioContext = context,
                    ),
                )
            } ?: missingAssembly()
        val gate =
            try {
                killSwitchQueryPort.readPublicState()
            } catch (exception: KillSwitchUnavailableException) {
                throw exception
            } catch (exception: Exception) {
                throw KillSwitchUnavailableException(exception)
            }
        val warnings =
            buildList {
                contextSelection.warning?.let(::add)
                unavailableWarning(assembly.portfolioValue, listOf("portfolioValue"))?.let(::add)
                add(
                    PortfolioRiskWarning(
                        code = "MISSING_SOURCE",
                        fields =
                            listOf(
                                "dailyPnlRate",
                                "mdd",
                                "var95",
                                "cvar95",
                                "realizedVolatility20d",
                                "annualizedVolatility20d",
                                "hmmRegime",
                                "hmmRegimeProbability",
                                "signalFresh",
                                "ragFresh",
                            ),
                    ),
                )
            }.distinctBy { it.code to it.fields }
        return PortfolioRiskResult(
            projection =
                PortfolioRiskProjection(
                    asOf = asOf,
                    portfolioValue =
                        assembly.portfolioValue.wholeOrNull()
                            ?: assembly.portfolioValueLastKnown.wholeOrNull(),
                    // Runtime loss guards do not support public performance metrics.
                    dailyPnlRate = null,
                    mdd = null,
                    var95 = null,
                    cvar95 = null,
                    realizedVolatility20d = null,
                    annualizedVolatility20d = null,
                    hmmRegime = null,
                    hmmRegimeProbability = null,
                    killSwitchActive = gate.active,
                    dataFreshness =
                        PortfolioRiskFreshness(
                            priceFresh = assembly.latestPrice.freshness(),
                            signalFresh = null,
                            ragFresh = null,
                        ),
                ),
            warnings = warnings,
        )
    }

    private fun selectSingleContext(actorUserId: String): ContextSelection {
        for (source in PORTFOLIO_SOURCE_PRECEDENCE) {
            when (val resolution = portfolioContextPort.resolve(actorUserId, source)) {
                is PortfolioContextResolution.Available -> return ContextSelection(resolution.context, null)
                is PortfolioContextResolution.Unavailable ->
                    if (resolution.reason == PortfolioContextUnavailableReason.CONFLICT) {
                        return ContextSelection(
                            context = null,
                            warning = PortfolioRiskWarning("PORTFOLIO_CONTEXT_CONFLICT", listOf("portfolioValue")),
                        )
                    }
            }
        }
        return ContextSelection(
            context = null,
            warning = PortfolioRiskWarning("MISSING_SOURCE", listOf("portfolioValue")),
        )
    }

    private fun missingAssembly(): PortfolioRiskAssembly {
        val missing = MetricCell.Missing(MetricIssueCode.PORTFOLIO_CONTEXT_UNAVAILABLE)
        return PortfolioRiskAssembly(missing, missing, missing, missing, missing, missing)
    }

    private fun unavailableWarning(
        cell: MetricCell<MetricValue>,
        fields: List<String>,
    ): PortfolioRiskWarning? =
        if (cell is MetricCell.Available) {
            null
        } else {
            PortfolioRiskWarning(cell.publicReason(), fields)
        }

    private fun MetricCell<MetricValue>.wholeOrNull(): Long? = ((this as? MetricCell.Available)?.value as? MetricValue.Whole)?.value

    private fun MetricCell<MetricValue>.freshness(): Boolean? =
        when (this) {
            is MetricCell.Available -> true
            is MetricCell.Stale -> false
            else -> null
        }

    private fun MetricCell<MetricValue>.publicReason(): String =
        when (this) {
            is MetricCell.Stale -> "STALE_SOURCE"
            else -> "MISSING_SOURCE"
        }

    private data class ContextSelection(
        val context: PortfolioContextRef?,
        val warning: PortfolioRiskWarning?,
    )
}
