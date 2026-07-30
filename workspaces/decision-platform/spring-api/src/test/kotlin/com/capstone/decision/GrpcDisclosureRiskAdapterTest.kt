package com.capstone.decision

import com.capstone.decision.application.risk.port.EvaluationSourceRequest
import com.capstone.decision.application.risk.port.PortfolioContextRef
import com.capstone.decision.contract.v1.DisclosureObservationServiceGrpc
import com.capstone.decision.contract.v1.DisclosureRiskEvent
import com.capstone.decision.contract.v1.DisclosureRiskWarning
import com.capstone.decision.contract.v1.GetDisclosureEventsRequest
import com.capstone.decision.contract.v1.GetDisclosureEventsResponse
import com.capstone.decision.domain.risk.EvaluationBounds
import com.capstone.decision.domain.risk.MetricCell
import com.capstone.decision.domain.risk.MetricIssueCode
import com.capstone.decision.domain.risk.OrderIntentSnapshot
import com.capstone.decision.domain.risk.PortfolioSource
import com.capstone.decision.infrastructure.grpc.DecisionGrpcProperties
import com.capstone.decision.infrastructure.grpc.DisclosureGrpcProtocolException
import com.capstone.decision.infrastructure.grpc.GrpcDisclosureRiskAdapter
import io.grpc.Metadata
import io.grpc.Server
import io.grpc.ServerCall
import io.grpc.ServerCallHandler
import io.grpc.ServerInterceptor
import io.grpc.ServerInterceptors
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
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
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
                DecisionGrpcProperties(target = target, sharedSecret = SHARED_SECRET).validate()
            }
        }
    }

    @Test
    fun `shared secret is required and attached to every business RPC`() {
        listOf("", "short", "has whitespace ${"s".repeat(32)}").forEach { secret ->
            assertThrows<IllegalArgumentException> {
                DecisionGrpcProperties(target = "127.0.0.1:50051", sharedSecret = secret).validate()
            }
        }

        val capturedSecret = AtomicReference<String?>()
        val server =
            server(
                constantService(validResponse()),
                object : ServerInterceptor {
                    override fun <ReqT : Any, RespT : Any> interceptCall(
                        call: ServerCall<ReqT, RespT>,
                        headers: Metadata,
                        next: ServerCallHandler<ReqT, RespT>,
                    ): ServerCall.Listener<ReqT> {
                        capturedSecret.set(headers.get(AUTH_HEADER))
                        return next.startCall(call, headers)
                    }
                },
            )
        val adapter = adapter(server)
        try {
            assertThat(adapter.load(request())).isInstanceOf(MetricCell.Available::class.java)
            assertThat(capturedSecret.get()).isEqualTo(SHARED_SECRET)
        } finally {
            adapter.close()
            server.shutdownNow().awaitTermination()
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
    fun `transport and bounded evidence failures map to distinct typed absence`() {
        val unavailable =
            server(
                failingService(Status.UNAVAILABLE),
            )
        val unavailableAdapter = adapter(unavailable)
        try {
            assertThat(unavailableAdapter.load(request()))
                .isEqualTo(MetricCell.Error(MetricIssueCode.DISCLOSURE_UNAVAILABLE))
        } finally {
            unavailableAdapter.close()
            unavailable.shutdownNow().awaitTermination()
        }

        listOf(Status.DEADLINE_EXCEEDED, Status.FAILED_PRECONDITION).forEach { status ->
            val incomplete = server(failingService(status))
            val incompleteAdapter = adapter(incomplete)
            try {
                assertThat(incompleteAdapter.load(request()))
                    .isEqualTo(MetricCell.Incomplete(MetricIssueCode.SOURCE_INCOMPLETE))
            } finally {
                incompleteAdapter.close()
                incomplete.shutdownNow().awaitTermination()
            }
        }

        val oversized = server(failingService(Status.OUT_OF_RANGE))
        val oversizedAdapter = adapter(oversized)
        try {
            assertThat(oversizedAdapter.load(request()))
                .isEqualTo(MetricCell.Incomplete(MetricIssueCode.SOURCE_OVERSIZED))
        } finally {
            oversizedAdapter.close()
            oversized.shutdownNow().awaitTermination()
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

        val ambiguousResourceFailure = server(failingService(Status.RESOURCE_EXHAUSTED))
        val ambiguousResourceAdapter = adapter(ambiguousResourceFailure)
        try {
            assertThrows<DisclosureGrpcProtocolException> {
                ambiguousResourceAdapter.load(request())
            }
        } finally {
            ambiguousResourceAdapter.close()
            ambiguousResourceFailure.shutdownNow().awaitTermination()
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
                DecisionGrpcProperties(target = "127.0.0.1:${server.port}", sharedSecret = SHARED_SECRET),
                clock,
            )
        try {
            assertThat(adapter.load(request()))
                .isEqualTo(MetricCell.Incomplete(MetricIssueCode.SOURCE_INCOMPLETE))
            // cold channel 자체가 deadline을 소비하면 server handler 도달 전 0일 수 있지만 재시도는 없어야 한다.
            assertThat(calls.get()).isLessThanOrEqualTo(1)
        } finally {
            adapter.close()
            server.shutdownNow().awaitTermination()
        }
    }

    @Test
    fun `client boundary rejects ninth concurrent source call before physical RPC`() {
        val calls = AtomicInteger()
        val started = CountDownLatch(8)
        val release = CountDownLatch(1)
        val server =
            server(
                object : DisclosureObservationServiceGrpc.DisclosureObservationServiceImplBase() {
                    override fun getDisclosureEvents(
                        request: GetDisclosureEventsRequest,
                        responseObserver: StreamObserver<GetDisclosureEventsResponse>,
                    ) {
                        calls.incrementAndGet()
                        started.countDown()
                        release.await(2, TimeUnit.SECONDS)
                        responseObserver.onNext(validResponse())
                        responseObserver.onCompleted()
                    }
                },
            )
        val adapter = adapter(server)
        val executor = Executors.newFixedThreadPool(9)
        try {
            val results = (0 until 9).map { executor.submit<MetricCell<*>> { adapter.load(request()) } }
            assertThat(started.await(1, TimeUnit.SECONDS)).isTrue()
            val bounded =
                results
                    .firstOrNull { it.isDone }
                    ?.get(1, TimeUnit.SECONDS)
            assertThat(bounded).isInstanceOf(MetricCell.Error::class.java)
            assertThat(calls.get()).isEqualTo(8)

            release.countDown()
            val completed = results.map { it.get(2, TimeUnit.SECONDS) }
            assertThat(completed.count { it is MetricCell.Available }).isEqualTo(8)
            assertThat(completed.count { it is MetricCell.Error }).isEqualTo(1)
        } finally {
            release.countDown()
            executor.shutdownNow()
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

    @Test
    fun `malformed scalar identity list provenance and warning responses are technical failures`() {
        val invalidEventCode =
            validResponse()
                .eventsList
                .single()
                .toBuilder()
                .setEventCode("")
                .build()
        val invalidReceiptNo =
            validResponse()
                .eventsList
                .single()
                .toBuilder()
                .setReceiptNo("invalid")
                .build()
        val invalidOccurredOn =
            validResponse()
                .eventsList
                .single()
                .toBuilder()
                .setOccurredOn("2028-12-31")
                .build()
        val tooManySourceRefs = (1..101).map { index -> index.toString(16).padStart(64, '0') }
        val tooManyWarnings = (1..51).map { validWarning("WARN_$it", "safe") }
        val invalidResponses =
            listOf(
                validResponse().toBuilder().setScore(Double.NaN).build(),
                validResponse().toBuilder().setScore(Double.POSITIVE_INFINITY).build(),
                validResponse().toBuilder().setScore(-0.01).build(),
                validResponse().toBuilder().setScore(1.01).build(),
                validResponse().toBuilder().setObservedAt("not-an-instant").build(),
                validResponse().toBuilder().setSymbol("000660").build(),
                validResponse().toBuilder().setCorpCode("invalid").build(),
                validResponse().toBuilder().setAsOf("2030-01-01").build(),
                validResponse().toBuilder().setWindowFrom("2029-01-01").build(),
                validResponse().toBuilder().setWindowTo("2030-01-01").build(),
                validResponse().toBuilder().setMappingVersion("").build(),
                validResponse()
                    .toBuilder()
                    .setMappingVersion("m".repeat(EvaluationBounds.MAX_ID_OR_CODE_CHARS + 1))
                    .build(),
                validResponse()
                    .toBuilder()
                    .clearEvents()
                    .addEvents(invalidEventCode)
                    .build(),
                validResponse()
                    .toBuilder()
                    .clearEvents()
                    .addEvents(invalidReceiptNo)
                    .build(),
                validResponse()
                    .toBuilder()
                    .clearEvents()
                    .addEvents(invalidOccurredOn)
                    .build(),
                validResponse()
                    .toBuilder()
                    .clearSourceRefs()
                    .addSourceRefs("A".repeat(64))
                    .build(),
                validResponse()
                    .toBuilder()
                    .clearSourceRefs()
                    .addAllSourceRefs(tooManySourceRefs)
                    .build(),
                validResponse().toBuilder().addAllWarnings(tooManyWarnings).build(),
                validResponse()
                    .toBuilder()
                    .addWarnings(validWarning("", "safe"))
                    .build(),
                validResponse()
                    .toBuilder()
                    .addWarnings(
                        validWarning(
                            "W".repeat(EvaluationBounds.MAX_ID_OR_CODE_CHARS + 1),
                            "safe",
                        ),
                    ).build(),
                validResponse()
                    .toBuilder()
                    .addWarnings(validWarning("WARN", ""))
                    .build(),
                validResponse()
                    .toBuilder()
                    .addWarnings(
                        validWarning(
                            "WARN",
                            "m".repeat(EvaluationBounds.MAX_SAFE_MESSAGE_CHARS + 1),
                        ),
                    ).build(),
            )

        invalidResponses.forEach(::assertProtocolFailure)
    }

    @Test
    fun `actual response larger than one MiB is a technical failure`() {
        val oversizedResponse =
            validResponse()
                .toBuilder()
                .addWarnings(
                    validWarning(
                        "OVERSIZED",
                        "m".repeat(EvaluationBounds.MAX_RESPONSE_BYTES),
                    ),
                ).build()
        assertThat(oversizedResponse.serializedSize).isGreaterThan(EvaluationBounds.MAX_RESPONSE_BYTES)

        assertProtocolFailure(oversizedResponse)
    }

    private fun adapter(server: Server): GrpcDisclosureRiskAdapter =
        GrpcDisclosureRiskAdapter(
            DecisionGrpcProperties(target = "127.0.0.1:${server.port}", sharedSecret = SHARED_SECRET),
            Clock.fixed(EVALUATION_AS_OF, ZoneOffset.UTC),
        )

    private fun server(
        service: DisclosureObservationServiceGrpc.DisclosureObservationServiceImplBase,
        vararg interceptors: ServerInterceptor,
    ): Server =
        NettyServerBuilder
            .forAddress(InetSocketAddress("127.0.0.1", 0))
            .addService(ServerInterceptors.intercept(service, *interceptors))
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

    private fun assertProtocolFailure(response: GetDisclosureEventsResponse) {
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

    private fun validWarning(
        code: String,
        message: String,
    ): DisclosureRiskWarning =
        DisclosureRiskWarning
            .newBuilder()
            .setCode(code)
            .setEventCode("OPENDART:piicDecsn")
            .setReceiptNo("20300102000001")
            .setMessage(message)
            .build()

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
        const val SHARED_SECRET = "grpc-shared-secret-for-s2-3-tests-0001"
        val AUTH_HEADER: Metadata.Key<String> =
            Metadata.Key.of("x-decision-grpc-auth", Metadata.ASCII_STRING_MARSHALLER)
    }
}
