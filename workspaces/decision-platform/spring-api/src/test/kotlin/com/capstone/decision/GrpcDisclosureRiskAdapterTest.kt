package com.capstone.decision

import com.capstone.decision.application.risk.port.EvaluationSourceRequest
import com.capstone.decision.application.risk.port.PortfolioContextRef
import com.capstone.decision.contract.v1.DisclosureObservationServiceGrpc
import com.capstone.decision.contract.v1.DisclosureRiskEvent
import com.capstone.decision.contract.v1.GetDisclosureEventsRequest
import com.capstone.decision.contract.v1.GetDisclosureEventsResponse
import com.capstone.decision.domain.risk.MetricCell
import com.capstone.decision.domain.risk.OrderIntentSnapshot
import com.capstone.decision.domain.risk.PortfolioSource
import com.capstone.decision.infrastructure.grpc.DecisionGrpcProperties
import com.capstone.decision.infrastructure.grpc.DisclosureGrpcProtocolException
import com.capstone.decision.infrastructure.grpc.GrpcDisclosureRiskAdapter
import io.grpc.Server
import io.grpc.Status
import io.grpc.netty.shaded.io.grpc.netty.NettyServerBuilder
import io.grpc.stub.StreamObserver
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.junit.jupiter.api.extension.ExtendWith
import org.slf4j.MDC
import org.springframework.boot.test.system.CapturedOutput
import org.springframework.boot.test.system.OutputCaptureExtension
import java.net.InetSocketAddress
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

@ExtendWith(OutputCaptureExtension::class)
class GrpcDisclosureRiskAdapterTest {
    @Test
    fun `non numeric loopback target is rejected before channel startup`() {
        listOf(
            "0.0.0.0:50051",
            "192.0.2.10:50051",
            "localhost:50051",
            "dns:///127.0.0.1:50051",
        ).forEach { target ->
            assertThrows<IllegalArgumentException> {
                DecisionGrpcProperties(target = target).validate()
            }
        }
    }

    @Test
    fun `real loopback business RPC preserves provenance with one physical attempt`(output: CapturedOutput) {
        val calls = AtomicInteger()
        val capturedRequest = AtomicReference<GetDisclosureEventsRequest>()
        val server =
            server(
                object : DisclosureObservationServiceGrpc.DisclosureObservationServiceImplBase() {
                    override fun getDisclosureEvents(
                        request: GetDisclosureEventsRequest,
                        responseObserver: StreamObserver<GetDisclosureEventsResponse>,
                    ) {
                        calls.incrementAndGet()
                        capturedRequest.set(request)
                        responseObserver.onNext(validResponse())
                        responseObserver.onCompleted()
                    }
                },
            )
        val adapter = adapter(server)
        try {
            MDC.put("trace_id", "1".repeat(32))
            MDC.put("span_id", "2".repeat(16))
            val result = adapter.load(request())
            val available = result as MetricCell.Available

            assertThat(calls.get()).isEqualTo(1)
            assertThat(capturedRequest.get().corpCode).isEmpty()
            assertThat(capturedRequest.get().windowFrom).isEqualTo("2029-01-02")
            assertThat(capturedRequest.get().windowTo).isEqualTo("2030-01-02")
            assertThat(available.value.score.toPlainString()).isEqualTo("0.6")
            assertThat(available.value.mappingVersion).isEqualTo("s1.2-v1")
            assertThat(available.value.events.map { it.eventCode })
                .containsExactly("OPENDART:piicDecsn")
            assertThat(available.value.sourceRefs).containsExactly("a".repeat(64))
            assertThat(available.freshUntil)
                .isEqualTo(Instant.parse("2030-01-03T03:04:05Z"))
            assertThat(output.out).contains("dec_grpc_fixture")
            assertThat(output.out).contains("evl_grpc_fixture")
            assertThat(output.out).contains("1".repeat(32))
            assertThat(output.out).doesNotContain("usr_fixture")
            assertThat(output.out).doesNotContain("paper-context")
            assertThat(output.out).doesNotContain("a".repeat(64))
        } finally {
            MDC.remove("trace_id")
            MDC.remove("span_id")
            adapter.close()
            server.shutdownNow().awaitTermination()
        }
        assertThat(adapter.isShutdown()).isTrue()
    }

    @Test
    fun `UNAVAILABLE maps to typed absence while INTERNAL remains technical failure`() {
        val unavailable =
            server(
                failingService(Status.UNAVAILABLE),
            )
        val unavailableAdapter = adapter(unavailable)
        try {
            assertThat(unavailableAdapter.load(request())).isInstanceOf(MetricCell.Error::class.java)
        } finally {
            unavailableAdapter.close()
            unavailable.shutdownNow().awaitTermination()
        }

        val internal = server(failingService(Status.INTERNAL))
        val internalAdapter = adapter(internal)
        try {
            assertThrows<DisclosureGrpcProtocolException> {
                internalAdapter.load(request())
            }
        } finally {
            internalAdapter.close()
            internal.shutdownNow().awaitTermination()
        }
    }

    @Test
    fun `effective deadline uses remaining 900ms budget and never retries`() {
        val calls = AtomicInteger()
        val server =
            server(
                object : DisclosureObservationServiceGrpc.DisclosureObservationServiceImplBase() {
                    override fun getDisclosureEvents(
                        request: GetDisclosureEventsRequest,
                        responseObserver: StreamObserver<GetDisclosureEventsResponse>,
                    ) {
                        calls.incrementAndGet()
                        Thread.sleep(600)
                        responseObserver.onNext(validResponse())
                        responseObserver.onCompleted()
                    }
                },
            )
        val clock = Clock.fixed(EVALUATION_AS_OF.plusMillis(500), ZoneOffset.UTC)
        val adapter =
            GrpcDisclosureRiskAdapter(
                DecisionGrpcProperties(target = "127.0.0.1:${server.port}"),
                clock,
            )
        try {
            assertThat(adapter.load(request())).isInstanceOf(MetricCell.Error::class.java)
            assertThat(calls.get()).isEqualTo(1)
        } finally {
            adapter.close()
            server.shutdownNow().awaitTermination()
        }
    }

    @Test
    fun `duplicate provenance and over-limit list are malformed technical failures`() {
        val duplicate =
            validResponse()
                .toBuilder()
                .addSourceRefs("a".repeat(64))
                .build()
        val overLimit =
            validResponse()
                .toBuilder()
                .clearEvents()
                .addAllEvents(
                    (0..100).map { index ->
                        DisclosureRiskEvent
                            .newBuilder()
                            .setEventCode("OPENDART:dfOcr")
                            .setReceiptNo(index.toString().padStart(14, '0'))
                            .setOccurredOn("2030-01-02")
                            .build()
                    },
                ).build()

        listOf(duplicate, overLimit).forEach { response ->
            val server = server(constantService(response))
            val adapter = adapter(server)
            try {
                assertThrows<DisclosureGrpcProtocolException> {
                    adapter.load(request())
                }
            } finally {
                adapter.close()
                server.shutdownNow().awaitTermination()
            }
        }
    }

    @Test
    fun `incomplete malformed response is rejected before typed unavailability`() {
        val malformed =
            validResponse()
                .toBuilder()
                .setComplete(false)
                .setMappingVersion("")
                .clearSourceRefs()
                .addSourceRefs("not-a-sha256")
                .build()
        val server = server(constantService(malformed))
        val adapter = adapter(server)
        try {
            assertThrows<DisclosureGrpcProtocolException> {
                adapter.load(request())
            }
        } finally {
            adapter.close()
            server.shutdownNow().awaitTermination()
        }
    }

    private fun adapter(server: Server): GrpcDisclosureRiskAdapter =
        GrpcDisclosureRiskAdapter(
            DecisionGrpcProperties(target = "127.0.0.1:${server.port}"),
            Clock.fixed(EVALUATION_AS_OF, ZoneOffset.UTC),
        )

    private fun server(service: DisclosureObservationServiceGrpc.DisclosureObservationServiceImplBase): Server =
        NettyServerBuilder
            .forAddress(InetSocketAddress("127.0.0.1", 0))
            .addService(service)
            .build()
            .start()

    private fun constantService(
        response: GetDisclosureEventsResponse,
    ): DisclosureObservationServiceGrpc.DisclosureObservationServiceImplBase =
        object : DisclosureObservationServiceGrpc.DisclosureObservationServiceImplBase() {
            override fun getDisclosureEvents(
                request: GetDisclosureEventsRequest,
                responseObserver: StreamObserver<GetDisclosureEventsResponse>,
            ) {
                responseObserver.onNext(response)
                responseObserver.onCompleted()
            }
        }

    private fun failingService(status: Status): DisclosureObservationServiceGrpc.DisclosureObservationServiceImplBase =
        object : DisclosureObservationServiceGrpc.DisclosureObservationServiceImplBase() {
            override fun getDisclosureEvents(
                request: GetDisclosureEventsRequest,
                responseObserver: StreamObserver<GetDisclosureEventsResponse>,
            ) {
                responseObserver.onError(status.asRuntimeException())
            }
        }

    private fun validResponse(): GetDisclosureEventsResponse =
        GetDisclosureEventsResponse
            .newBuilder()
            .setSymbol("005930")
            .setCorpCode("00126380")
            .setAsOf("2030-01-02")
            .setWindowFrom("2029-01-02")
            .setWindowTo("2030-01-02")
            .setScore(0.6)
            .setMappingVersion("s1.2-v1")
            .setObservedAt("2030-01-02T03:04:05Z")
            .setComplete(true)
            .addSourceRefs("a".repeat(64))
            .addEvents(
                DisclosureRiskEvent
                    .newBuilder()
                    .setEventCode("OPENDART:piicDecsn")
                    .setReceiptNo("20300102000001")
                    .setOccurredOn("2030-01-02")
                    .build(),
            ).build()

    private fun request(): EvaluationSourceRequest =
        EvaluationSourceRequest(
            actorUserId = "usr_fixture",
            portfolioContext =
                PortfolioContextRef(
                    opaqueRef = "paper-context",
                    source = PortfolioSource.INTERNAL_PAPER,
                    ownerScopeHash = "b".repeat(64),
                ),
            orderIntent =
                OrderIntentSnapshot(
                    symbol = "005930",
                    side = "BUY",
                    orderType = "MARKET",
                    quantity = 1,
                    estimatedPrice = 70_000,
                    estimatedAmount = 70_000,
                    timeframe = "1d",
                    strategyId = "cash-equity-v1",
                ),
            evaluationAsOf = EVALUATION_AS_OF,
            evaluationId = "evl_grpc_fixture",
            decisionId = "dec_grpc_fixture",
        )

    private companion object {
        val EVALUATION_AS_OF: Instant = Instant.parse("2030-01-02T03:04:05Z")
    }
}
