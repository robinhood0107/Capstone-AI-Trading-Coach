package com.capstone.decision.application.financialengineering

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ErrorCode
import org.springframework.beans.factory.ObjectProvider
import org.springframework.stereotype.Service
import tools.jackson.databind.ObjectMapper
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime

data class OptionContractTerms(
    val termsId: String,
    val optionRight: String,
    val strike: Double,
    val lastTradingAt: Instant,
    val multiplier: Double,
    val effectiveFrom: Instant,
    val effectiveTo: Instant?,
    val sourceUrl: String,
    val sourceHash: String,
)

data class OptionNumericCommand(
    val optionRight: String,
    val spot: Double,
    val strike: Double,
    val tau: Double,
    val riskFreeRate: Double,
    val dividendYield: Double,
    val volatility: Double,
)

data class ImpliedVolatilityCommand(
    val optionRight: String,
    val spot: Double,
    val strike: Double,
    val tau: Double,
    val riskFreeRate: Double,
    val dividendYield: Double,
    val marketPrice: Double,
    val maxIterations: Int,
)

data class GreeksResult(
    val delta: Double,
    val gamma: Double,
    val vegaPerUnitVolatility: Double,
    val vegaPerVolPoint: Double,
    val calendarThetaPerYear: Double,
    val calendarThetaPerDay: Double,
    val rhoPerUnitRate: Double,
    val rhoPerRatePoint: Double,
)

interface FinancialEngineeringNumericPort {
    fun blackScholes(command: OptionNumericCommand): Double

    fun greeks(command: OptionNumericCommand): GreeksResult

    fun impliedVolatility(command: ImpliedVolatilityCommand): Double
}

class FinancialEngineeringUnavailableException : RuntimeException()

class FinancialEngineeringValidationException(
    val reason: String,
) : RuntimeException()

data class ValuationContext(
    val terms: OptionContractTerms,
    val tau: Double,
)

@Service
class FinancialEngineeringService(
    private val objectMapper: ObjectMapper,
    private val numericPortProvider: ObjectProvider<FinancialEngineeringNumericPort>,
) {
    private val termsById: Map<String, OptionContractTerms> by lazy(::loadTerms)

    fun context(
        contractId: String,
        valuationAtText: String,
    ): ValuationContext {
        val terms = termsById[contractId] ?: validation("contractId")
        val valuationAt = parseInstant(valuationAtText)
        if (valuationAt < terms.effectiveFrom || (terms.effectiveTo != null && valuationAt >= terms.effectiveTo)) {
            validation("valuationAt")
        }
        if (valuationAt >= terms.lastTradingAt) validation("valuationAt")
        val elapsedSeconds = Duration.between(valuationAt, terms.lastTradingAt).toNanos() / 1_000_000_000.0
        val tau = elapsedSeconds / ACT_365F_SECONDS
        if (!tau.isFinite() || tau <= 0.0) validation("valuationAt")
        return ValuationContext(terms, tau)
    }

    fun <T> calculate(block: (FinancialEngineeringNumericPort) -> T): T =
        try {
            block(numericPortProvider.getIfAvailable() ?: throw FinancialEngineeringUnavailableException())
        } catch (error: FinancialEngineeringValidationException) {
            throw ApiException(ErrorCode.VALIDATION_ERROR, details = mapOf("reason" to error.reason))
        } catch (_: FinancialEngineeringUnavailableException) {
            throw ApiException(ErrorCode.PYTHON_SERVICE_UNAVAILABLE)
        }

    private fun loadTerms(): Map<String, OptionContractTerms> {
        val resource =
            javaClass.classLoader.getResourceAsStream(TERMS_RESOURCE)
                ?: throw IllegalStateException("Trusted option contract terms are unavailable.")
        return resource.use { input ->
            val entries = objectMapper.readTree(input).path("entries")
            require(entries.isArray)
            entries.values().asSequence().associate { node ->
                require(node.path("contractId").stringValue() == "option_contract_terms.v1")
                require(node.path("timezone").stringValue() == "Asia/Seoul")
                require(node.path("exerciseStyle").stringValue() == "EUROPEAN")
                require(node.path("settlementType").stringValue() == "CASH")
                val terms =
                    OptionContractTerms(
                        termsId = node.path("termsId").stringValue(),
                        optionRight = node.path("optionRight").stringValue(),
                        strike = node.path("strike").doubleValue(),
                        lastTradingAt = OffsetDateTime.parse(node.path("lastTradingAt").stringValue()).toInstant(),
                        multiplier = node.path("multiplier").doubleValue(),
                        effectiveFrom = OffsetDateTime.parse(node.path("effectiveFrom").stringValue()).toInstant(),
                        effectiveTo =
                            node
                                .path("effectiveTo")
                                .takeUnless { it.isNull }
                                ?.stringValue()
                                ?.let(OffsetDateTime::parse)
                                ?.toInstant(),
                        sourceUrl = node.path("sourceUrl").stringValue(),
                        sourceHash = node.path("sourceHash").stringValue(),
                    )
                require(terms.optionRight in setOf("CALL", "PUT"))
                require(terms.strike > 0.0 && terms.multiplier > 0.0)
                require(SHA256.matches(terms.sourceHash) && terms.sourceUrl.startsWith("https://"))
                terms.termsId to terms
            }
        }
    }

    private fun parseInstant(text: String): Instant =
        try {
            OffsetDateTime.parse(text).toInstant()
        } catch (_: Exception) {
            validation("valuationAt")
        }

    private fun validation(field: String): Nothing = throw ApiException(ErrorCode.VALIDATION_ERROR, details = mapOf("field" to field))

    private companion object {
        const val ACT_365F_SECONDS = 31_536_000.0
        const val TERMS_RESOURCE = "contracts/option-contract-terms.v1.json"
        val SHA256 = Regex("^[0-9a-f]{64}$")
    }
}
