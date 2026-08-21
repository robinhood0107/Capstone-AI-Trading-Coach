package com.capstone.decision.infrastructure.grpc

import com.capstone.decision.application.financialengineering.FinancialEngineeringNumericPort
import com.capstone.decision.application.financialengineering.FinancialEngineeringUnavailableException
import com.capstone.decision.application.financialengineering.FinancialEngineeringValidationException
import com.capstone.decision.application.financialengineering.GreeksResult
import com.capstone.decision.application.financialengineering.ImpliedVolatilityCommand
import com.capstone.decision.application.financialengineering.OptionNumericCommand
import com.capstone.decision.contract.financialengineering.v1.BlackScholesRequest
import com.capstone.decision.contract.financialengineering.v1.FinancialEngineeringServiceGrpc
import com.capstone.decision.contract.financialengineering.v1.GreeksRequest
import com.capstone.decision.contract.financialengineering.v1.ImpliedVolatilityRequest
import io.grpc.ManagedChannel
import io.grpc.Metadata
import io.grpc.StatusRuntimeException
import io.grpc.netty.shaded.io.grpc.netty.NettyChannelBuilder
import io.grpc.stub.MetadataUtils
import jakarta.annotation.PreDestroy
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import java.util.concurrent.Semaphore
import java.util.concurrent.TimeUnit

@Component
@ConditionalOnProperty(name = ["app.financial-engineering.grpc.enabled"], havingValue = "true")
class GrpcFinancialEngineeringAdapter(
    private val properties: FinancialEngineeringGrpcProperties,
) : FinancialEngineeringNumericPort,
    AutoCloseable {
    private val channel: ManagedChannel
    private val concurrency: Semaphore

    init {
        properties.validateEnabled()
        channel =
            NettyChannelBuilder
                .forTarget(properties.target)
                .usePlaintext()
                .disableRetry()
                .maxInboundMessageSize(properties.responseMaxBytes)
                .build()
        concurrency = Semaphore(properties.concurrencyMax, true)
    }

    override fun blackScholes(command: OptionNumericCommand): Double {
        val response = call { stub -> stub.blackScholes(command.toBlackScholesRequest()) }
        return response.discountedValue.requireFinite()
    }

    override fun greeks(command: OptionNumericCommand): GreeksResult {
        val response = call { stub -> stub.greeks(command.toGreeksRequest()) }
        return GreeksResult(
            delta = response.delta.requireFinite(),
            gamma = response.gamma.requireFinite(),
            vegaPerUnitVolatility = response.vegaPerUnitVolatility.requireFinite(),
            vegaPerVolPoint = response.vegaPerVolPoint.requireFinite(),
            calendarThetaPerYear = response.calendarThetaPerYear.requireFinite(),
            calendarThetaPerDay = response.calendarThetaPerDay.requireFinite(),
            rhoPerUnitRate = response.rhoPerUnitRate.requireFinite(),
            rhoPerRatePoint = response.rhoPerRatePoint.requireFinite(),
        )
    }

    override fun impliedVolatility(command: ImpliedVolatilityCommand): Double {
        val request =
            ImpliedVolatilityRequest
                .newBuilder()
                .setOptionRight(command.optionRight)
                .setSpot(command.spot)
                .setStrike(command.strike)
                .setTimeToMaturityYears(command.tau)
                .setRiskFreeRate(command.riskFreeRate)
                .setDividendYield(command.dividendYield)
                .setMarketPrice(command.marketPrice)
                .setMaxIterations(command.maxIterations)
                .build()
        require(request.serializedSize <= properties.requestMaxBytes)
        return call { stub -> stub.impliedVolatility(request) }.impliedVolatility.requireFinite()
    }

    private fun <T> call(block: (FinancialEngineeringServiceGrpc.FinancialEngineeringServiceBlockingStub) -> T): T {
        if (!concurrency.tryAcquire()) throw FinancialEngineeringUnavailableException()
        try {
            val metadata = Metadata().apply { put(AUTH_KEY, properties.sharedSecret) }
            val stub =
                FinancialEngineeringServiceGrpc
                    .newBlockingStub(channel)
                    .withInterceptors(MetadataUtils.newAttachHeadersInterceptor(metadata))
                    .withDeadlineAfter(properties.deadlineMillis, TimeUnit.MILLISECONDS)
            return try {
                block(stub)
            } catch (error: StatusRuntimeException) {
                if (error.status.code == io.grpc.Status.Code.INVALID_ARGUMENT) {
                    val reason = error.status.description.orEmpty()
                    if (reason in setOf("IV_NOT_BRACKETED", "IV_NOT_CONVERGED", "VALIDATION_ERROR")) {
                        throw FinancialEngineeringValidationException(reason)
                    }
                }
                throw FinancialEngineeringUnavailableException()
            }
        } finally {
            concurrency.release()
        }
    }

    private fun OptionNumericCommand.toBlackScholesRequest(): BlackScholesRequest =
        BlackScholesRequest.newBuilder().applyNumeric(this).build()

    private fun OptionNumericCommand.toGreeksRequest(): GreeksRequest = GreeksRequest.newBuilder().applyNumeric(this).build()

    private fun BlackScholesRequest.Builder.applyNumeric(command: OptionNumericCommand) =
        setOptionRight(command.optionRight)
            .setSpot(command.spot)
            .setStrike(command.strike)
            .setTimeToMaturityYears(command.tau)
            .setVolatility(command.volatility)
            .setRiskFreeRate(command.riskFreeRate)
            .setDividendYield(command.dividendYield)

    private fun GreeksRequest.Builder.applyNumeric(command: OptionNumericCommand) =
        setOptionRight(command.optionRight)
            .setSpot(command.spot)
            .setStrike(command.strike)
            .setTimeToMaturityYears(command.tau)
            .setVolatility(command.volatility)
            .setRiskFreeRate(command.riskFreeRate)
            .setDividendYield(command.dividendYield)

    private fun Double.requireFinite(): Double = also { require(it.isFinite()) }

    @PreDestroy
    override fun close() {
        channel.shutdownNow().awaitTermination(2, TimeUnit.SECONDS)
    }

    private companion object {
        val AUTH_KEY: Metadata.Key<String> =
            Metadata.Key.of("x-decision-grpc-auth", Metadata.ASCII_STRING_MARSHALLER)
    }
}
