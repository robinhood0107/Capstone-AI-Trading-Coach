package com.capstone.decision.infrastructure.grpc

import com.capstone.decision.application.brokerage.BrokerageGatewayCancelRequest
import com.capstone.decision.application.brokerage.BrokerageGatewayCancelResult
import com.capstone.decision.application.brokerage.BrokerageGatewayPort
import com.capstone.decision.application.brokerage.BrokerageGatewaySubmitRequest
import com.capstone.decision.application.brokerage.BrokerageGatewaySubmitResult
import com.capstone.decision.application.brokerage.BrokerageUnavailableException
import com.capstone.decision.contract.v1.BrokerageServiceGrpc
import com.capstone.decision.contract.v1.CancelMockCashOrderRequest
import com.capstone.decision.contract.v1.SubmitMockCashOrderRequest
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry
import io.grpc.ManagedChannel
import io.grpc.Metadata
import io.grpc.Status
import io.grpc.StatusRuntimeException
import io.grpc.netty.shaded.io.grpc.netty.NettyChannelBuilder
import io.grpc.stub.MetadataUtils
import jakarta.annotation.PreDestroy
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import java.time.Instant
import java.util.concurrent.TimeUnit

/**
 * S3.1 KIS Mock provider boundary는 disabled-by-default loopback gRPC adapter다.
 * live-order 경로는 여기서도 열지 않고, Spring REST 기본 submit/cancel은 provider 물리 호출 없이 ledger에 머문다.
 */
@Component
@ConditionalOnProperty(name = ["app.brokerage.grpc.enabled"], havingValue = "true")
class GrpcBrokerageAdapter(
    private val properties: BrokerageGrpcProperties,
    circuitBreakerRegistry: CircuitBreakerRegistry,
) : BrokerageGatewayPort,
    AutoCloseable {
    private val channel: ManagedChannel
    private val circuitBreaker = circuitBreakerRegistry.circuitBreaker(properties.circuitBreakerName)

    init {
        properties.validate()
        channel =
            NettyChannelBuilder
                .forTarget(properties.target)
                .usePlaintext()
                .disableRetry()
                .maxInboundMessageSize(properties.responseMaxBytes)
                .build()
    }

    override fun submitMockOrder(request: BrokerageGatewaySubmitRequest): BrokerageGatewaySubmitResult =
        circuitBreaker.executeSupplier {
            val rpcRequest =
                SubmitMockCashOrderRequest
                    .newBuilder()
                    .setRequestId(request.requestId)
                    .setOrderId(request.orderId)
                    .setAccountId(request.accountId)
                    .setSymbol(request.orderIntent.symbol)
                    .setSide(request.orderIntent.side)
                    .setOrderType(request.orderIntent.orderType)
                    .setQuantity(request.orderIntent.quantity)
                    .setEstimatedPriceKrw(request.orderIntent.estimatedPrice)
                    .build()
            if (rpcRequest.serializedSize > properties.requestMaxBytes) {
                throw BrokerageUnavailableException("Brokerage gRPC request exceeded bounded contract.")
            }
            try {
                val response =
                    stub()
                        .submitMockCashOrder(rpcRequest)
                if (
                    response.orderId != request.orderId ||
                    !response.accepted ||
                    !HASH.matches(response.providerOrderRefHash)
                ) {
                    throw BrokerageUnavailableException("Brokerage gRPC response violated bounded contract.")
                }
                BrokerageGatewaySubmitResult(
                    orderId = response.orderId,
                    providerOrderRefHash = response.providerOrderRefHash,
                    trId = response.trId,
                    receivedAt = Instant.parse(response.receivedAt),
                )
            } catch (exception: StatusRuntimeException) {
                throw mapStatus(exception)
            }
        }

    override fun cancelMockOrder(request: BrokerageGatewayCancelRequest): BrokerageGatewayCancelResult =
        circuitBreaker.executeSupplier {
            val rpcRequest =
                CancelMockCashOrderRequest
                    .newBuilder()
                    .setRequestId(request.requestId)
                    .setOrderId(request.orderId)
                    .setAccountId(request.accountId)
                    .build()
            if (rpcRequest.serializedSize > properties.requestMaxBytes) {
                throw BrokerageUnavailableException("Brokerage gRPC request exceeded bounded contract.")
            }
            try {
                val response =
                    stub()
                        .cancelMockCashOrder(rpcRequest)
                if (response.orderId != request.orderId || response.status !in CANCEL_STATUSES) {
                    throw BrokerageUnavailableException("Brokerage gRPC cancel response violated bounded contract.")
                }
                BrokerageGatewayCancelResult(
                    orderId = response.orderId,
                    status = response.status,
                    receivedAt = Instant.parse(response.receivedAt),
                )
            } catch (exception: StatusRuntimeException) {
                throw mapStatus(exception)
            }
        }

    private fun stub(): BrokerageServiceGrpc.BrokerageServiceBlockingStub =
        BrokerageServiceGrpc
            .newBlockingStub(channel)
            .withInterceptors(MetadataUtils.newAttachHeadersInterceptor(authHeaders()))
            .withDeadlineAfter(properties.deadlineMillis, TimeUnit.MILLISECONDS)

    private fun mapStatus(exception: StatusRuntimeException): BrokerageUnavailableException =
        when (exception.status.code) {
            Status.Code.UNAVAILABLE,
            Status.Code.DEADLINE_EXCEEDED,
            Status.Code.PERMISSION_DENIED,
            Status.Code.FAILED_PRECONDITION,
            -> BrokerageUnavailableException("Brokerage gRPC boundary failed closed.", exception)

            else -> BrokerageUnavailableException("Brokerage gRPC response was not usable.", exception)
        }

    private fun authHeaders(): Metadata {
        val headers = Metadata()
        headers.put(AUTH_HEADER, properties.sharedSecret)
        return headers
    }

    @PreDestroy
    override fun close() {
        channel.shutdownNow()
    }

    private companion object {
        val AUTH_HEADER: Metadata.Key<String> =
            Metadata.Key.of("x-decision-grpc-auth", Metadata.ASCII_STRING_MARSHALLER)
        val HASH = Regex("^[0-9a-f]{64}$")
        val CANCEL_STATUSES = setOf("CANCEL_REQUESTED", "CANCELLED")
    }
}
