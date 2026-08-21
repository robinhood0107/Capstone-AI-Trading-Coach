package com.capstone.decision.api.financialengineering

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ApiResponse
import com.capstone.decision.api.common.ApiResponseFactory
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.api.common.RequestIds
import com.capstone.decision.application.financialengineering.FinancialEngineeringService
import com.capstone.decision.application.financialengineering.ImpliedVolatilityCommand
import com.capstone.decision.application.financialengineering.OptionNumericCommand
import io.swagger.v3.oas.annotations.Hidden
import io.swagger.v3.oas.annotations.Operation
import jakarta.servlet.http.HttpServletRequest
import org.springframework.http.MediaType
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

data class BlackScholesRequestDto(
    val contractId: String,
    val valuationAt: String,
    val spot: Double,
    val volatility: Double,
    val riskFreeRate: Double,
    val dividendYield: Double,
)

data class GreeksRequestDto(
    val contractId: String,
    val valuationAt: String,
    val spot: Double,
    val volatility: Double,
    val riskFreeRate: Double,
    val dividendYield: Double,
)

data class ImpliedVolatilityRequestDto(
    val contractId: String,
    val valuationAt: String,
    val spot: Double,
    val marketPrice: Double,
    val riskFreeRate: Double,
    val dividendYield: Double,
    val maxIterations: Int = 100,
)

data class ContractProvenanceDto(
    val termsId: String,
    val sourceUrl: String,
    val sourceHash: String,
    val multiplier: Double,
    val exerciseStyle: String = "EUROPEAN",
    val settlementType: String = "CASH",
    val timezone: String = "Asia/Seoul",
)

data class BlackScholesResponseDto(
    val contractId: String = "s6-4-bsm-response.v1",
    val measure: String = "Q_DISCOUNTED_VALUE",
    val discountedValue: Double,
    val timeToMaturityYears: Double,
    val provenance: ContractProvenanceDto,
)

data class GreeksResponseDto(
    val contractId: String = "s6-4-greeks-response.v1",
    val measure: String = "Q_DISCOUNTED_VALUE",
    val valuationDelta: Double,
    val conservativeRiskDelta: Double,
    val gamma: Double,
    val vegaPerUnitVolatility: Double,
    val vegaPerVolPoint: Double,
    val calendarThetaPerYear: Double,
    val calendarThetaPerDay: Double,
    val rhoPerUnitRate: Double,
    val rhoPerRatePoint: Double,
    val timeToMaturityYears: Double,
    val provenance: ContractProvenanceDto,
)

data class ImpliedVolatilityResponseDto(
    val contractId: String = "s6-4-iv-response.v1",
    val impliedVolatility: Double,
    val solver: String = "BOUNDED_BISECTION_0.0001_5.0",
    val measure: String = "Q_DISCOUNTED_VALUE",
    val timeToMaturityYears: Double,
    val provenance: ContractProvenanceDto,
)

@RestController
@Hidden
@RequestMapping(
    "/api/v1/financial-engineering/options",
    produces = [MediaType.APPLICATION_JSON_VALUE],
)
class FinancialEngineeringController(
    private val service: FinancialEngineeringService,
) {
    @Operation(summary = "trusted contract terms와 ACT/365F로 European BSM 교육 valuation을 계산한다.")
    @PostMapping("/black-scholes", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun blackScholes(
        @RequestBody body: BlackScholesRequestDto,
        request: HttpServletRequest,
    ): ApiResponse<BlackScholesResponseDto> {
        validate(body.contractId, body.spot, body.volatility, body.riskFreeRate, body.dividendYield)
        val context = service.context(body.contractId, body.valuationAt)
        val value =
            service.calculate { port ->
                port.blackScholes(
                    numeric(context.terms.optionRight, body.spot, context.terms.strike, context.tau, body),
                )
            }
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            BlackScholesResponseDto(
                discountedValue = value,
                timeToMaturityYears = context.tau,
                provenance = context.provenance(),
            ),
        )
    }

    @Operation(summary = "valuation Delta와 명시적 단위의 European BSM Greeks를 계산한다.")
    @PostMapping("/greeks", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun greeks(
        @RequestBody body: GreeksRequestDto,
        request: HttpServletRequest,
    ): ApiResponse<GreeksResponseDto> {
        validate(body.contractId, body.spot, body.volatility, body.riskFreeRate, body.dividendYield)
        val context = service.context(body.contractId, body.valuationAt)
        val value =
            service.calculate { port ->
                port.greeks(
                    OptionNumericCommand(
                        context.terms.optionRight,
                        body.spot,
                        context.terms.strike,
                        context.tau,
                        body.riskFreeRate,
                        body.dividendYield,
                        body.volatility,
                    ),
                )
            }
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            GreeksResponseDto(
                valuationDelta = value.delta,
                conservativeRiskDelta = if (context.terms.optionRight == "CALL") 1.0 else -1.0,
                gamma = value.gamma,
                vegaPerUnitVolatility = value.vegaPerUnitVolatility,
                vegaPerVolPoint = value.vegaPerVolPoint,
                calendarThetaPerYear = value.calendarThetaPerYear,
                calendarThetaPerDay = value.calendarThetaPerDay,
                rhoPerUnitRate = value.rhoPerUnitRate,
                rhoPerRatePoint = value.rhoPerRatePoint,
                timeToMaturityYears = context.tau,
                provenance = context.provenance(),
            ),
        )
    }

    @Operation(summary = "discounted no-arbitrage bounds 안에서 bounded bisection IV를 계산한다.")
    @PostMapping("/implied-volatility", consumes = [MediaType.APPLICATION_JSON_VALUE])
    fun impliedVolatility(
        @RequestBody body: ImpliedVolatilityRequestDto,
        request: HttpServletRequest,
    ): ApiResponse<ImpliedVolatilityResponseDto> {
        validate(body.contractId, body.spot, body.marketPrice, body.riskFreeRate, body.dividendYield)
        if (body.maxIterations !in 1..1_000) invalid("maxIterations")
        val context = service.context(body.contractId, body.valuationAt)
        val value =
            service.calculate { port ->
                port.impliedVolatility(
                    ImpliedVolatilityCommand(
                        context.terms.optionRight,
                        body.spot,
                        context.terms.strike,
                        context.tau,
                        body.riskFreeRate,
                        body.dividendYield,
                        body.marketPrice,
                        body.maxIterations,
                    ),
                )
            }
        return ApiResponseFactory.success(
            RequestIds.currentOrCreate(request),
            ImpliedVolatilityResponseDto(
                impliedVolatility = value,
                timeToMaturityYears = context.tau,
                provenance = context.provenance(),
            ),
        )
    }

    private fun numeric(
        right: String,
        spot: Double,
        strike: Double,
        tau: Double,
        body: BlackScholesRequestDto,
    ) = OptionNumericCommand(right, spot, strike, tau, body.riskFreeRate, body.dividendYield, body.volatility)

    private fun validate(
        contractId: String,
        positiveOne: Double,
        positiveTwo: Double,
        vararg finite: Double,
    ) {
        if (contractId.isBlank() || contractId.length > 128) invalid("contractId")
        if (!positiveOne.isFinite() || positiveOne <= 0.0 || !positiveTwo.isFinite() || positiveTwo <= 0.0) {
            invalid("numericInput")
        }
        if (finite.any { !it.isFinite() }) invalid("numericInput")
    }

    private fun com.capstone.decision.application.financialengineering.ValuationContext.provenance() =
        ContractProvenanceDto(
            terms.termsId,
            terms.sourceUrl,
            terms.sourceHash,
            terms.multiplier,
        )

    private fun invalid(field: String): Nothing = throw ApiException(ErrorCode.VALIDATION_ERROR, details = mapOf("field" to field))
}
