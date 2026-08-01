package com.capstone.decision

import com.capstone.decision.application.rag.RagAnswerMode
import com.capstone.decision.application.rag.RagAskCommand
import com.capstone.decision.application.rag.RagEvaluationContext
import com.capstone.decision.application.rag.RagGenerationStatus
import com.capstone.decision.contract.v1.ProviderPhysicalCounts
import com.capstone.decision.contract.v1.RagAskRequest
import com.capstone.decision.contract.v1.RagAskResponse
import com.capstone.decision.contract.v1.RagCitation
import com.capstone.decision.contract.v1.RagResponseStatus
import com.capstone.decision.contract.v1.RagServiceGrpc
import com.capstone.decision.infrastructure.grpc.DecisionGrpcProperties
import com.capstone.decision.infrastructure.grpc.GrpcRagEvaluationAdapter
import com.capstone.decision.infrastructure.grpc.RagGrpcProperties
import com.capstone.decision.infrastructure.grpc.RagGrpcProtocolException
import com.capstone.decision.infrastructure.grpc.RagGrpcUnavailableException
import com.capstone.decision.infrastructure.security.RagGrpcSecretSeparation
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
import org.springframework.boot.test.system.CapturedOutput
import org.springframework.boot.test.system.OutputCaptureExtension
import java.net.InetSocketAddress
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

@ExtendWith(OutputCaptureExtension::class)
class GrpcRagEvaluationAdapterTest {
    @Test
    fun `loopback RPC carries only opaque scope and rechecks zero-provider citations`(output: CapturedOutput) {
        val calls = AtomicInteger()
        val captured = AtomicReference<RagAskRequest>()
        val capturedSecret = AtomicReference<String?>()
        val service =
            object : RagServiceGrpc.RagServiceImplBase() {
                override fun ask(
                    request: RagAskRequest,
                    responseObserver: StreamObserver<RagAskResponse>,
                ) {
                    calls.incrementAndGet()
                    captured.set(request)
                    responseObserver.onNext(validResponse())
                    responseObserver.onCompleted()
                }
            }
        val server =
            server(
                service,
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
            val result = adapter.evaluate(command(), context())

            assertThat(calls.get()).isEqualTo(1)
            assertThat(capturedSecret.get()).isEqualTo(SHARED_SECRET)
            assertThat(captured.get().requestId).isEqualTo(REQUEST_ID)
            assertThat(captured.get().ownerScopeClaim).isEqualTo(SCOPE_CLAIM)
            assertThat(captured.get().question).isEqualTo(QUESTION)
            assertThat(captured.get().consentContext.granted).isFalse()
            assertThat(captured.get().consentContext.policyVersion).isEqualTo("NONE")
            assertThat(captured.get().policyContext.activeGenerationId).isEqualTo(GENERATION_ID)
            assertThat(result.generationStatus).isEqualTo(RagGenerationStatus.ANSWERED)
            assertThat(result.answer).contains("공개 fixture")
            assertThat(result.citations).hasSize(1)
            assertThat(result.providerPhysicalAttempts).isZero()
            assertThat(result.geminiPhysicalCalls).isZero()
            assertThat(result.openAiPhysicalCalls).isZero()
            assertThat(result.voyagePhysicalCalls).isZero()
            assertThat(output.out).doesNotContain(QUESTION)
            assertThat(output.out).doesNotContain("공개 fixture")
            assertThat(output.out).doesNotContain("usr_demo_user")
        } finally {
            adapter.close()
            server.shutdownNow().awaitTermination()
        }
    }

    @Test
    fun `generation drift citation scope and provider counts fail before public mapping`() {
        val invalid =
            listOf(
                validResponse().toBuilder().setGenerationId("rag_gen_${"f".repeat(32)}").build(),
                validResponse().toBuilder().clearAuthorizedTop5ChunkRevisionIds().build(),
                validResponse()
                    .toBuilder()
                    .setProviderPhysicalCounts(
                        ProviderPhysicalCounts
                            .newBuilder()
                            .setTotal(1)
                            .setGemini(1)
                            .build(),
                    ).build(),
                validResponse().toBuilder().setExternalProviderCandidate(true).build(),
                validResponse().toBuilder().setRequestId("different-request-id").build(),
                validResponse()
                    .toBuilder()
                    .addCitations(
                        validResponse()
                            .citationsList
                            .single()
                            .toBuilder()
                            .setCitationId("cit_2"),
                    ).build(),
            )
        invalid.forEach { response ->
            val server = server(constantService(response))
            val adapter = adapter(server)
            try {
                assertThrows<RagGrpcProtocolException> { adapter.evaluate(command(), context()) }
            } finally {
                adapter.close()
                server.shutdownNow().awaitTermination()
            }
        }
    }

    @Test
    fun `deadline and Python unavailability are single-attempt typed failures`() {
        val calls = AtomicInteger()
        val delayed =
            server(
                object : RagServiceGrpc.RagServiceImplBase() {
                    override fun ask(
                        request: RagAskRequest,
                        responseObserver: StreamObserver<RagAskResponse>,
                    ) {
                        calls.incrementAndGet()
                        Thread.sleep(100)
                        responseObserver.onNext(validResponse())
                        responseObserver.onCompleted()
                    }
                },
            )
        val adapter = adapter(delayed, deadlineMillis = 25)
        try {
            assertThrows<RagGrpcUnavailableException> { adapter.evaluate(command(), context()) }
            assertThat(calls.get()).isLessThanOrEqualTo(1)
        } finally {
            adapter.close()
            delayed.shutdownNow().awaitTermination()
        }

        val unavailable = server(failingService(Status.UNAVAILABLE))
        val unavailableAdapter = adapter(unavailable)
        try {
            assertThrows<RagGrpcUnavailableException> {
                unavailableAdapter.evaluate(command(), context())
            }
        } finally {
            unavailableAdapter.close()
            unavailable.shutdownNow().awaitTermination()
        }
    }

    @Test
    fun `properties reject non-loopback target and unsafe bounds`() {
        listOf("0.0.0.0:50053", "localhost:50053", "dns:///127.0.0.1:50053").forEach { target ->
            assertThrows<IllegalArgumentException> {
                RagGrpcProperties(target = target, sharedSecret = SHARED_SECRET).validate()
            }
        }
        assertThrows<IllegalArgumentException> {
            RagGrpcProperties(
                target = "127.0.0.1:50053",
                sharedSecret = SHARED_SECRET,
                retryCount = 1,
            ).validate()
        }
    }

    @Test
    fun `active adapter rejects the Decision grpc secret before opening its channel`() {
        assertThrows<IllegalArgumentException> {
            GrpcRagEvaluationAdapter(
                RagGrpcProperties(
                    target = "127.0.0.1:50053",
                    sharedSecret = SHARED_SECRET,
                ),
                DecisionGrpcProperties(
                    target = "127.0.0.1:50051",
                    sharedSecret = SHARED_SECRET,
                ),
                RagGrpcSecretSeparation,
            )
        }
    }

    private fun adapter(
        server: Server,
        deadlineMillis: Long = 15_000,
    ): GrpcRagEvaluationAdapter =
        GrpcRagEvaluationAdapter(
            RagGrpcProperties(
                target = "127.0.0.1:${server.port}",
                sharedSecret = SHARED_SECRET,
                deadlineMillis = deadlineMillis,
            ),
            DecisionGrpcProperties(
                target = "127.0.0.1:50051",
                sharedSecret = DECISION_SHARED_SECRET,
            ),
            RagGrpcSecretSeparation,
        )

    private fun server(
        service: RagServiceGrpc.RagServiceImplBase,
        interceptor: ServerInterceptor? = null,
    ): Server {
        val builder = NettyServerBuilder.forAddress(InetSocketAddress("127.0.0.1", 0))
        if (interceptor == null) {
            builder.addService(service)
        } else {
            builder.addService(ServerInterceptors.intercept(service, interceptor))
        }
        return builder.build().start()
    }

    private fun constantService(response: RagAskResponse): RagServiceGrpc.RagServiceImplBase =
        object : RagServiceGrpc.RagServiceImplBase() {
            override fun ask(
                request: RagAskRequest,
                responseObserver: StreamObserver<RagAskResponse>,
            ) {
                responseObserver.onNext(response)
                responseObserver.onCompleted()
            }
        }

    private fun failingService(status: Status): RagServiceGrpc.RagServiceImplBase =
        object : RagServiceGrpc.RagServiceImplBase() {
            override fun ask(
                request: RagAskRequest,
                responseObserver: StreamObserver<RagAskResponse>,
            ) {
                responseObserver.onError(status.asRuntimeException())
            }
        }

    private fun command(): RagAskCommand =
        RagAskCommand(
            question = QUESTION,
            answerMode = RagAnswerMode.CONCISE,
            relatedSymbols = emptyList(),
            topics = listOf("RISK", "FINANCIAL_ENGINEERING"),
        )

    private fun context(): RagEvaluationContext =
        RagEvaluationContext(
            requestId = REQUEST_ID,
            ownerScopeClaim = SCOPE_CLAIM,
            consentGranted = false,
            consentPolicyVersion = "NONE",
            policyId = "bge_only_v1",
            policyVersion = 2,
            activeGenerationId = GENERATION_ID,
            embeddingProfileId = "bge_m3_local_1024_v1",
        )

    private fun validResponse(): RagAskResponse =
        RagAskResponse
            .newBuilder()
            .setRequestId(REQUEST_ID)
            .setStatus(RagResponseStatus.RAG_RESPONSE_STATUS_ANSWERED)
            .setAnswer("공개 fixture 근거입니다. [cit_1]")
            .addCitations(
                RagCitation
                    .newBuilder()
                    .setCitationId("cit_1")
                    .setSourceId("src_project_backtest_overfitting_001")
                    .setSourceRevisionId("src_rev_${"b".repeat(32)}")
                    .setChunkRevisionId(CHUNK_ID)
                    .setGenerationId(GENERATION_ID)
                    .setTitle("백테스트 과적합 경계")
                    .setSectionTitle("핵심 claim")
                    .setCanonicalUrl("https://doi.org/10.1093/rfs/hhy070")
                    .build(),
            ).setCitationCoverage(1.0)
            .setRetrievalFailure(false)
            .addGuardrailFlags("FIXTURE_S4_5")
            .setGenerationId(GENERATION_ID)
            .setEmbeddingProfileId("bge_m3_local_1024_v1")
            .setFailureCode("")
            .setProviderPhysicalCounts(ProviderPhysicalCounts.getDefaultInstance())
            .addAuthorizedTop5ChunkRevisionIds(CHUNK_ID)
            .setExternalProviderCandidate(false)
            .setPolicyVersion(2)
            .build()

    private companion object {
        const val SHARED_SECRET = "rag-grpc-shared-secret-for-s4-6-tests-0001"
        const val DECISION_SHARED_SECRET = "decision-grpc-shared-secret-for-s2-3-tests-0001"
        const val REQUEST_ID = "req_s46_fixture_000000000001"
        const val SCOPE_CLAIM = "rag_scope_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        const val GENERATION_ID = "rag_gen_789b3ba9589ad399373194c0e3c0e76f"
        const val CHUNK_ID = "rag_chk_cccccccccccccccccccccccccccccccc"
        const val QUESTION =
            "공개 source identifier src_project_backtest_overfitting_001의 핵심 경계와 허용된 해석을 정확히 알려 주세요."
        val AUTH_HEADER: Metadata.Key<String> =
            Metadata.Key.of("x-decision-grpc-auth", Metadata.ASCII_STRING_MARSHALLER)
    }
}
