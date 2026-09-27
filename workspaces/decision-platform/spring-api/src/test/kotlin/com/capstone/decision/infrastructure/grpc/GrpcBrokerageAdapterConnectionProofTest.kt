package com.capstone.decision.infrastructure.grpc

import com.capstone.decision.application.brokerage.BrokerageUnavailableException
import com.capstone.decision.application.brokerage.MockConnectionPosition
import com.capstone.decision.application.brokerage.MockConnectionRejectedException
import com.capstone.decision.contract.v1.BrokerageServiceGrpc
import com.capstone.decision.contract.v1.MockBalancePosition
import com.capstone.decision.contract.v1.VerifyMockConnectionRequest
import com.capstone.decision.contract.v1.VerifyMockConnectionResponse
import com.capstone.decision.infrastructure.brokerage.MockCredentialSettingsService
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry
import io.grpc.Server
import io.grpc.Status
import io.grpc.netty.shaded.io.grpc.netty.NettyServerBuilder
import io.grpc.stub.StreamObserver
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.springframework.beans.factory.support.StaticListableBeanFactory
import java.net.InetSocketAddress

/** 연결 확인은 KIS 잔고조회 한 번이다: 읽은 잔고를 돌려주고, 실패 이유를 고정 목록으로만 올린다. */
class GrpcBrokerageAdapterConnectionProofTest {
    private var server: Server? = null
    private var adapter: GrpcBrokerageAdapter? = null

    @AfterEach
    fun close() {
        adapter?.close()
        server?.shutdownNow()
    }

    @Test
    fun `a successful check returns the balance KIS actually reported`() {
        val proof =
            connect { request, observer ->
                observer.onNext(
                    VerifyMockConnectionResponse
                        .newBuilder()
                        .setAccountId(request.accountId)
                        .setConnected(true)
                        .setCashKrw(94_533_738)
                        .setPortfolioEquityKrw(97_233_738)
                        .addPositions(
                            MockBalancePosition
                                .newBuilder()
                                .setSymbol("055550")
                                .setQuantity(45)
                                .setMarketValueKrw(2_700_000),
                        ).setPositionsComplete(true)
                        .build(),
                )
                observer.onCompleted()
            }.verify("req_test", "usr_test", ACCOUNT)
        requireNotNull(proof)
        assertEquals(94_533_738L, proof.cashKrw)
        assertEquals(listOf(MockConnectionPosition("055550", 45, 2_700_000)), proof.positions)
        assertEquals(true, proof.positionsComplete)
    }

    @Test
    fun `an older agent that returns no balance is connected without a proof`() {
        val proof =
            connect { request, observer ->
                observer.onNext(
                    VerifyMockConnectionResponse
                        .newBuilder()
                        .setAccountId(request.accountId)
                        .setConnected(true)
                        .build(),
                )
                observer.onCompleted()
            }.verify("req_test", "usr_test", ACCOUNT)
        assertNull(proof)
    }

    @Test
    fun `a rejected account number surfaces as a user fixable reason`() {
        val error =
            assertThrows<MockConnectionRejectedException> {
                connect { _, observer ->
                    observer.onError(
                        Status.FAILED_PRECONDITION.withDescription("MOCK_CONNECTION_ACCOUNT_REJECTED").asRuntimeException(),
                    )
                }.verify("req_test", "usr_test", ACCOUNT)
            }
        assertEquals("ACCOUNT_REJECTED", error.reason)
        val keyError =
            assertThrows<MockConnectionRejectedException> {
                connect { _, observer ->
                    observer.onError(
                        Status.FAILED_PRECONDITION.withDescription("MOCK_CONNECTION_APP_KEY_REJECTED").asRuntimeException(),
                    )
                }.verify("req_test", "usr_test", ACCOUNT)
            }
        assertEquals("APP_KEY_REJECTED", keyError.reason)
    }

    @Test
    fun `unknown failure details and plain outages stay a generic brokerage outage`() {
        assertThrows<BrokerageUnavailableException> {
            connect { _, observer ->
                observer.onError(Status.FAILED_PRECONDITION.withDescription("SOMETHING_ELSE").asRuntimeException())
            }.verify("req_test", "usr_test", ACCOUNT)
        }
        assertThrows<BrokerageUnavailableException> {
            connect { _, observer ->
                observer.onError(Status.UNAVAILABLE.withDescription("mock connection check unavailable").asRuntimeException())
            }.verify("req_test", "usr_test", ACCOUNT)
        }
    }

    private fun connect(
        handler: (VerifyMockConnectionRequest, StreamObserver<VerifyMockConnectionResponse>) -> Unit,
    ): GrpcBrokerageAdapter {
        adapter?.close()
        server?.shutdownNow()
        val started =
            NettyServerBuilder
                .forAddress(InetSocketAddress("127.0.0.1", 0))
                .addService(
                    object : BrokerageServiceGrpc.BrokerageServiceImplBase() {
                        override fun verifyMockConnection(
                            request: VerifyMockConnectionRequest,
                            responseObserver: StreamObserver<VerifyMockConnectionResponse>,
                        ) = handler(request, responseObserver)
                    },
                ).build()
                .start()
        server = started
        return GrpcBrokerageAdapter(
            BrokerageGrpcProperties(
                enabled = true,
                target = "127.0.0.1:${started.port}",
                sharedSecret = "s".repeat(40),
                deadlineMillis = 2_000,
            ),
            CircuitBreakerRegistry.ofDefaults(),
            StaticListableBeanFactory().getBeanProvider(MockCredentialSettingsService::class.java),
        ).also { adapter = it }
    }

    private companion object {
        val ACCOUNT = "acct_${"a".repeat(32)}"
    }
}
