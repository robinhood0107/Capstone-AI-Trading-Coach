package com.capstone.decision.infrastructure.grpc

import com.capstone.decision.application.brokerage.BrokerageGatewayBalanceRequest
import com.capstone.decision.application.brokerage.BrokerageGatewayBalanceResult
import com.capstone.decision.application.brokerage.BrokerageGatewayBuyableRequest
import com.capstone.decision.application.brokerage.BrokerageGatewayBuyableResult
import com.capstone.decision.application.brokerage.BrokerageGatewayCancelRequest
import com.capstone.decision.application.brokerage.BrokerageGatewayCancelResult
import com.capstone.decision.application.brokerage.BrokerageGatewayPort
import com.capstone.decision.application.brokerage.BrokerageGatewaySubmitRequest
import com.capstone.decision.application.brokerage.BrokerageGatewaySubmitResult
import com.capstone.decision.application.brokerage.BrokerageUnavailableException
import com.capstone.decision.application.brokerage.MockBalancePositionProjection
import com.capstone.decision.application.brokerage.MockCredentialCertificationPort
import com.capstone.decision.application.brokerage.MockCredentialCertificationProof
import com.capstone.decision.application.brokerage.MockCredentialCertificationStatus
import com.capstone.decision.application.brokerage.MockCredentialConnectionPort
import com.capstone.decision.contract.v1.BoundMockCredentialEnvelope
import com.capstone.decision.contract.v1.BrokerageServiceGrpc
import com.capstone.decision.contract.v1.CancelMockCashOrderRequest
import com.capstone.decision.contract.v1.CertifyMockCredentialRequest
import com.capstone.decision.contract.v1.GetMockBalanceRequest
import com.capstone.decision.contract.v1.GetMockBuyableRequest
import com.capstone.decision.contract.v1.MockCredentialCertificationState
import com.capstone.decision.contract.v1.SubmitMockCashOrderRequest
import com.capstone.decision.contract.v1.VerifyMockConnectionRequest
import com.capstone.decision.infrastructure.brokerage.MockCredentialSettingsService
import com.google.protobuf.ByteString
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry
import io.grpc.ManagedChannel
import io.grpc.Metadata
import io.grpc.Status
import io.grpc.StatusRuntimeException
import io.grpc.netty.shaded.io.grpc.netty.NettyChannelBuilder
import io.grpc.stub.MetadataUtils
import jakarta.annotation.PreDestroy
import org.springframework.beans.factory.ObjectProvider
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
    private val credentialProvider: ObjectProvider<MockCredentialSettingsService>,
) : BrokerageGatewayPort,
    MockCredentialConnectionPort,
    MockCredentialCertificationPort,
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
            val builder =
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
            boundCredential(request.ownerUserId, request.accountId, setOf("CERTIFIED"))
                ?.let { builder.setCredential(it) }
            val rpcRequest = builder.build()
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
            val builder =
                CancelMockCashOrderRequest
                    .newBuilder()
                    .setRequestId(request.requestId)
                    .setOrderId(request.orderId)
                    .setAccountId(request.accountId)
            boundCredential(request.ownerUserId, request.accountId, setOf("CERTIFIED", "DISCONNECTING"))
                ?.let { builder.setCredential(it) }
            val rpcRequest = builder.build()
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

    override fun getMockBalance(request: BrokerageGatewayBalanceRequest): BrokerageGatewayBalanceResult =
        circuitBreaker.executeSupplier {
            val builder =
                GetMockBalanceRequest
                    .newBuilder()
                    .setRequestId(request.requestId)
                    .setAccountId(request.accountId)
            boundCredential(request.ownerUserId, request.accountId, setOf("STORED", "CONNECTED", "CERTIFIED", "DISCONNECTING"))
                ?.let { builder.setCredential(it) }
            val rpcRequest = builder.build()
            requireBoundedRequest(rpcRequest.serializedSize)
            try {
                val response = stub().getMockBalance(rpcRequest)
                val positions =
                    response.positionsList.map { position ->
                        if (
                            !SYMBOL.matches(position.symbol) ||
                            position.quantity < 0 ||
                            position.marketValueKrw < 0
                        ) {
                            throw BrokerageUnavailableException(
                                "Brokerage gRPC balance response violated bounded contract.",
                            )
                        }
                        MockBalancePositionProjection(
                            symbol = position.symbol,
                            quantity = position.quantity,
                            marketValueKrw = position.marketValueKrw,
                            isGoldEtfEtn = position.isGoldEtfEtn,
                        )
                    }
                if (
                    response.accountId != request.accountId ||
                    response.cashKrw < 0 ||
                    response.portfolioEquityKrw < 0 ||
                    response.marginRequirementKrw < 0 ||
                    positions.size > 1_000 ||
                    positions.map { it.symbol }.toSet().size != positions.size ||
                    response.sourceVersion != "kis-mock-balance-v1"
                ) {
                    throw BrokerageUnavailableException(
                        "Brokerage gRPC balance response violated bounded contract.",
                    )
                }
                BrokerageGatewayBalanceResult(
                    accountId = response.accountId,
                    cashKrw = response.cashKrw,
                    portfolioEquityKrw = response.portfolioEquityKrw,
                    marginRequirementKrw = response.marginRequirementKrw,
                    positions = positions,
                    observedAt = parseInstant(response.observedAt),
                    sourceVersion = response.sourceVersion,
                )
            } catch (exception: StatusRuntimeException) {
                throw mapStatus(exception)
            }
        }

    override fun getMockBuyable(request: BrokerageGatewayBuyableRequest): BrokerageGatewayBuyableResult =
        circuitBreaker.executeSupplier {
            val builder =
                GetMockBuyableRequest
                    .newBuilder()
                    .setRequestId(request.requestId)
                    .setAccountId(request.accountId)
                    .setSymbol(request.symbol)
                    .setEstimatedPriceKrw(request.estimatedPriceKrw)
            boundCredential(request.ownerUserId, request.accountId, setOf("CONNECTED", "CERTIFIED"))
                ?.let { builder.setCredential(it) }
            val rpcRequest = builder.build()
            requireBoundedRequest(rpcRequest.serializedSize)
            try {
                val response = stub().getMockBuyable(rpcRequest)
                if (
                    response.accountId != request.accountId ||
                    response.symbol != request.symbol ||
                    response.estimatedPriceKrw != request.estimatedPriceKrw ||
                    response.buyableQuantity < 0 ||
                    response.buyableAmountKrw < 0 ||
                    response.cashKrw < 0 ||
                    response.sourceVersion != "kis-mock-buyable-v1"
                ) {
                    throw BrokerageUnavailableException(
                        "Brokerage gRPC buyable response violated bounded contract.",
                    )
                }
                BrokerageGatewayBuyableResult(
                    accountId = response.accountId,
                    symbol = response.symbol,
                    estimatedPriceKrw = response.estimatedPriceKrw,
                    buyableQuantity = response.buyableQuantity,
                    buyableAmountKrw = response.buyableAmountKrw,
                    cashKrw = response.cashKrw,
                    observedAt = parseInstant(response.observedAt),
                    sourceVersion = response.sourceVersion,
                )
            } catch (exception: StatusRuntimeException) {
                throw mapStatus(exception)
            }
        }

    override fun verify(
        requestId: String,
        ownerUserId: String,
        accountId: String,
    ) {
        circuitBreaker.executeRunnable {
            val builder =
                VerifyMockConnectionRequest
                    .newBuilder()
                    .setRequestId(requestId)
                    .setAccountId(accountId)
            boundCredential(ownerUserId, accountId, setOf("STORED", "CONNECTED", "CERTIFIED"))
                ?.let { builder.setCredential(it) }
            val request = builder.build()
            requireBoundedRequest(request.serializedSize)
            try {
                val response = stub().verifyMockConnection(request)
                if (!response.connected || response.accountId != accountId) {
                    throw BrokerageUnavailableException("KIS_MOCK connection proof did not match the owner account.")
                }
            } catch (exception: StatusRuntimeException) {
                throw mapStatus(exception)
            }
        }
    }

    override fun certify(
        requestId: String,
        ownerUserId: String,
        accountId: String,
        certificationId: String,
        sessionDate: String,
        recovery: Boolean,
    ): MockCredentialCertificationProof =
        circuitBreaker.executeSupplier {
            val builder =
                CertifyMockCredentialRequest
                    .newBuilder()
                    .setRequestId(requestId)
                    .setOwnerUserId(ownerUserId)
                    .setAccountId(accountId)
                    .setCertificationId(certificationId)
                    .setSessionDate(sessionDate)
                    .setRecovery(recovery)
            boundCredential(ownerUserId, accountId, setOf("CONNECTED"))
                ?.let { builder.setCredential(it) }
            val request = builder.build()
            requireBoundedRequest(request.serializedSize)
            try {
                val response = stub().certifyMockCredential(request)
                val status =
                    when (response.state) {
                        MockCredentialCertificationState.MOCK_CREDENTIAL_CERTIFICATION_PASS ->
                            MockCredentialCertificationStatus.PASS
                        MockCredentialCertificationState.MOCK_CREDENTIAL_CERTIFICATION_FAILED ->
                            MockCredentialCertificationStatus.FAILED
                        MockCredentialCertificationState.MOCK_CREDENTIAL_CERTIFICATION_RECOVERY_REQUIRED ->
                            MockCredentialCertificationStatus.RECOVERY_REQUIRED
                        else -> throw BrokerageUnavailableException(
                            "KIS_MOCK certification response had an unknown state.",
                        )
                    }
                if (
                    response.accountId != accountId ||
                    response.certificationId != certificationId ||
                    !SESSION_DATE.matches(response.sessionDate) ||
                    response.quoteCalls !in 0..1 ||
                    response.brokerageCalls !in 0..7 ||
                    response.tokenCalls !in 0..1 ||
                    response.failureCode !in SAFE_CERTIFICATION_FAILURES ||
                    (
                        status == MockCredentialCertificationStatus.PASS &&
                            (
                                !HASH.matches(response.receiptSha256) ||
                                    response.quoteCalls != 1 ||
                                    response.brokerageCalls != 7
                            )
                    ) ||
                    (status != MockCredentialCertificationStatus.PASS && response.receiptSha256.isNotEmpty())
                ) {
                    throw BrokerageUnavailableException("KIS_MOCK certification response violated its contract.")
                }
                MockCredentialCertificationProof(
                    accountId = response.accountId,
                    certificationId = response.certificationId,
                    status = status,
                    receiptSha256 = response.receiptSha256,
                    sessionDate = response.sessionDate,
                    quoteCalls = response.quoteCalls,
                    brokerageCalls = response.brokerageCalls,
                    tokenCalls = response.tokenCalls,
                    failureCode = response.failureCode,
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

    /** Only a current owner row may supply the sealed bytes; no full-product environment fallback exists. */
    private fun boundCredential(
        ownerUserId: String,
        accountId: String,
        allowedStates: Set<String>,
    ): BoundMockCredentialEnvelope? =
        credentialProvider.ifAvailable?.resolveEnvelope(ownerUserId, accountId)?.use { envelope ->
            if (envelope.state !in allowedStates) {
                throw BrokerageUnavailableException("KIS_MOCK credential is not ready for this operation.")
            }
            val sealed = envelope.sealed
            BoundMockCredentialEnvelope
                .newBuilder()
                .setOwnerUserId(ownerUserId)
                .setAccountId(accountId)
                .setRevision(envelope.revision)
                .setCredentialState(envelope.state)
                .setKekVersion(sealed.kekVersion)
                .setWrapNonce(ByteString.copyFrom(sealed.wrapNonce))
                .setWrappedDek(ByteString.copyFrom(sealed.wrappedDek))
                .setWrapTag(ByteString.copyFrom(sealed.wrapTag))
                .setPayloadNonce(ByteString.copyFrom(sealed.secretNonce))
                .setPayloadCiphertext(ByteString.copyFrom(sealed.secretCiphertext))
                .setPayloadTag(ByteString.copyFrom(sealed.secretTag))
                .build()
        }

    private fun requireBoundedRequest(serializedSize: Int) {
        if (serializedSize > properties.requestMaxBytes) {
            throw BrokerageUnavailableException("Brokerage gRPC request exceeded bounded contract.")
        }
    }

    private fun parseInstant(value: String): Instant =
        try {
            Instant.parse(value)
        } catch (exception: Exception) {
            throw BrokerageUnavailableException(
                "Brokerage gRPC timestamp violated bounded contract.",
                exception,
            )
        }

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
        val SYMBOL = Regex("^[0-9]{6}$")
        val SESSION_DATE = Regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
        val CANCEL_STATUSES = setOf("CANCEL_REQUESTED", "CANCELLED")
        val SAFE_CERTIFICATION_FAILURES =
            setOf(
                "",
                "MARKET_CLOSED",
                "QUOTE_INVALID",
                "BUYABLE_UNAVAILABLE",
                "PROVIDER_FAILED",
                "TEST_ORDER_RECOVERED",
                "TEST_ORDER_UNCERTAIN",
                "EXECUTION_FILLED",
                "BALANCE_CHANGED",
            )
    }
}
