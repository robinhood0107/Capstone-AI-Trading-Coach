package com.capstone.decision.api.financialengineering

import com.capstone.decision.application.financialengineering.FinancialEngineeringNumericPort
import com.capstone.decision.application.financialengineering.FinancialEngineeringService
import com.capstone.decision.application.financialengineering.GreeksResult
import com.capstone.decision.application.financialengineering.ImpliedVolatilityCommand
import com.capstone.decision.application.financialengineering.OptionNumericCommand
import com.capstone.decision.infrastructure.grpc.FinancialEngineeringGrpcProperties
import io.kotest.matchers.doubles.shouldBeExactly
import io.kotest.matchers.shouldBe
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.springframework.beans.factory.support.StaticListableBeanFactory
import org.springframework.mock.web.MockHttpServletRequest
import tools.jackson.databind.json.JsonMapper
import java.time.Duration
import java.time.OffsetDateTime

class FinancialEngineeringControllerTest {
    private val port = RecordingPort()
    private val service =
        FinancialEngineeringService(
            JsonMapper.builder().build(),
            StaticListableBeanFactory(mapOf("numericPort" to port)).getBeanProvider(FinancialEngineeringNumericPort::class.java),
        )
    private val controller = FinancialEngineeringController(service)

    @Test
    fun `server resolves right strike and ACT 365F tau without client maturity field`() {
        val valuationAt = "2026-03-12T15:20:00+09:00"
        val result =
            controller.blackScholes(
                BlackScholesRequestDto(
                    contractId = "KOSPI200_OPTION_FIXTURE_202609_PUT_360000",
                    valuationAt = valuationAt,
                    spot = 350000.0,
                    volatility = 0.28,
                    riskFreeRate = 0.032,
                    dividendYield = 0.015,
                ),
                MockHttpServletRequest(),
            )
        val expectedSeconds =
            Duration
                .between(
                    OffsetDateTime.parse(valuationAt).toInstant(),
                    OffsetDateTime.parse("2026-09-10T15:20:00+09:00").toInstant(),
                ).seconds
        val response = requireNotNull(result.data)
        port.lastNumeric!!.optionRight shouldBe "PUT"
        port.lastNumeric!!.strike shouldBeExactly 360000.0
        port.lastNumeric!!.tau shouldBeExactly expectedSeconds / 31_536_000.0
        response.measure shouldBe "Q_DISCOUNTED_VALUE"
        response.provenance.settlementType shouldBe "CASH"
    }

    @Test
    fun `valuation at last trading instant and unknown settlement input authority fail closed`() {
        assertThrows<RuntimeException> {
            service.context(
                "KOSPI200_OPTION_FIXTURE_202609_CALL_360000",
                "2026-09-10T15:20:00+09:00",
            )
        }
        val fields =
            BlackScholesRequestDto::class.java.declaredFields
                .map { it.name }
                .toSet()
        fields.contains("finalSettlementDate") shouldBe false
        fields.contains("timeToMaturityYears") shouldBe false
    }

    @Test
    fun `greeks keeps valuation and conservative risk delta separate`() {
        val result =
            requireNotNull(
                controller
                    .greeks(
                        GreeksRequestDto(
                            "KOSPI200_OPTION_FIXTURE_202609_PUT_360000",
                            "2026-03-12T15:20:00+09:00",
                            350000.0,
                            0.28,
                            0.032,
                            0.015,
                        ),
                        MockHttpServletRequest(),
                    ).data,
            )
        result.valuationDelta shouldBeExactly -0.570897306492
        result.conservativeRiskDelta shouldBeExactly -1.0
        result.vegaPerVolPoint shouldBeExactly 140.899780404
    }

    @Test
    fun `transport configuration pins loopback deadline size workers and no retry`() {
        FinancialEngineeringGrpcProperties(
            enabled = true,
            sharedSecret = "financial-engineering-grpc-test-secret-0001",
        ).validateEnabled()
        assertThrows<IllegalArgumentException> {
            FinancialEngineeringGrpcProperties(
                enabled = true,
                target = "example.com:50054",
                sharedSecret = "financial-engineering-grpc-test-secret-0001",
            ).validateEnabled()
        }
        assertThrows<IllegalArgumentException> {
            FinancialEngineeringGrpcProperties(
                enabled = true,
                sharedSecret = "financial-engineering-grpc-test-secret-0001",
                retryCount = 1,
            ).validateEnabled()
        }
    }

    private class RecordingPort : FinancialEngineeringNumericPort {
        var lastNumeric: OptionNumericCommand? = null

        override fun blackScholes(command: OptionNumericCommand): Double {
            lastNumeric = command
            return 5500.106045553
        }

        override fun greeks(command: OptionNumericCommand): GreeksResult =
            GreeksResult(
                -0.570897306492,
                0.000038828202272,
                14089.9780404,
                140.899780404,
                -6810.08298,
                -18.6577616,
                -11651.17803,
                -116.5117803,
            )

        override fun impliedVolatility(command: ImpliedVolatilityCommand): Double = 0.28
    }
}
