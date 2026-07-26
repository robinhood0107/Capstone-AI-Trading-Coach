package com.capstone.decision.api.risk

import com.capstone.decision.application.risk.KillSwitchPublicState
import com.capstone.decision.application.risk.PortfolioRiskProjection
import io.swagger.v3.oas.annotations.media.Schema
import java.math.BigDecimal
import java.time.Instant

@Schema(name = "S24KillSwitchState", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class KillSwitchStateDto(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val active: Boolean,
    @field:Schema(
        requiredMode = Schema.RequiredMode.REQUIRED,
        allowableValues = [
            "USER_MANUAL_STOP",
            "OPERATOR_MANUAL_STOP",
            "DATA_FRESHNESS_STOP",
            "BROKERAGE_FAILURE_STOP",
            "DEMO_SAFETY_STOP",
            "ADMIN_RESUME",
            "INITIAL_STATE",
        ],
    )
    val reasonClass: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time")
    val changedAt: Instant,
)

@Schema(name = "S24PortfolioRisk", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class PortfolioRiskDto(
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
    val dataFreshness: PortfolioRiskFreshnessDto,
)

@Schema(name = "S24PortfolioRiskFreshness", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class PortfolioRiskFreshnessDto(
    val priceFresh: Boolean?,
    val signalFresh: Boolean?,
    val ragFresh: Boolean?,
)

@Schema(name = "S24RiskErrorResponse")
class S24RiskErrorResponseSchema

fun KillSwitchPublicState.toDto(): KillSwitchStateDto =
    KillSwitchStateDto(
        active = active,
        reasonClass = reasonClass.name,
        changedAt = changedAt,
    )

fun PortfolioRiskProjection.toDto(): PortfolioRiskDto =
    PortfolioRiskDto(
        asOf = asOf,
        portfolioValue = portfolioValue,
        dailyPnlRate = dailyPnlRate,
        mdd = mdd,
        var95 = var95,
        cvar95 = cvar95,
        realizedVolatility20d = realizedVolatility20d,
        annualizedVolatility20d = annualizedVolatility20d,
        hmmRegime = hmmRegime,
        hmmRegimeProbability = hmmRegimeProbability,
        killSwitchActive = killSwitchActive,
        dataFreshness =
            PortfolioRiskFreshnessDto(
                priceFresh = dataFreshness.priceFresh,
                signalFresh = dataFreshness.signalFresh,
                ragFresh = dataFreshness.ragFresh,
            ),
    )
