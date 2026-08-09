package com.capstone.decision

import com.capstone.decision.application.rag.RagAnswerMode
import com.capstone.decision.application.rag.RagAskCommand
import com.capstone.decision.application.rag.RagGenerationStatus
import com.capstone.decision.application.rag.RagV2EvaluationContext
import com.capstone.decision.contract.v2.DocumentLocator
import com.capstone.decision.contract.v2.LocalDocumentCitation
import com.capstone.decision.contract.v2.ProviderPhysicalCounts
import com.capstone.decision.contract.v2.PublicWebCitation
import com.capstone.decision.contract.v2.RagAskRequest
import com.capstone.decision.contract.v2.RagAskResponse
import com.capstone.decision.contract.v2.RagCitation
import com.capstone.decision.contract.v2.RagConsentContext
import com.capstone.decision.contract.v2.RagResponseStatus
import com.capstone.decision.contract.v2.RagServiceGrpc
import com.capstone.decision.infrastructure.grpc.DecisionGrpcProperties
import com.capstone.decision.infrastructure.grpc.GrpcRagV2EvaluationAdapter
import com.capstone.decision.infrastructure.grpc.RagGrpcProperties
import com.capstone.decision.infrastructure.grpc.RagV2GrpcProperties
import com.capstone.decision.infrastructure.grpc.RagV2GrpcProtocolException
import com.capstone.decision.infrastructure.security.RagV2GrpcSecretSeparation
import io.grpc.Metadata
import io.grpc.Server
import io.grpc.ServerCall
import io.grpc.ServerCallHandler
import io.grpc.ServerInterceptor
import io.grpc.ServerInterceptors
import io.grpc.netty.shaded.io.grpc.netty.NettyServerBuilder
import io.grpc.stub.StreamObserver
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import java.net.InetSocketAddress
import java.util.concurrent.atomic.AtomicReference

class GrpcRagV2EvaluationAdapterTest {
    @Test
    fun `v2 loopback maps only canonical public and local citation metadata`() {
        val captured = AtomicReference<RagAskRequest>()
        val capturedSecret = AtomicReference<String?>()
        val server =
            server(
                object : RagServiceGrpc.RagServiceImplBase() {
                    override fun ask(
                        request: RagAskRequest,
                        responseObserver: StreamObserver<RagAskResponse>,
                    ) {
                        captured.set(request)
                        responseObserver.onNext(validRetrievalOnlyResponse())
                        responseObserver.onCompleted()
                    }
                },
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

            assertThat(capturedSecret.get()).isEqualTo(SHARED_SECRET)
            assertThat(captured.get().requestId).isEqualTo(REQUEST_ID)
            assertThat(captured.get().ownerScopeClaim).isEqualTo(SCOPE_CLAIM)
            assertThat(captured.get().consentContext)
                .isEqualTo(
                    RagConsentContext
                        .newBuilder()
                        .setGranted(false)
                        .setPolicyVersion("NONE")
                        .build(),
                )
            assertThat(result.generationStatus).isEqualTo(RagGenerationStatus.RETRIEVAL_ONLY)
            assertThat(result.citations).hasSize(2)
            assertThat(result.citations[0].citationKind).isEqualTo("PUBLIC_WEB")
            assertThat(result.citations[0].canonicalUrl).isEqualTo("https://example.org/evidence")
            assertThat(result.citations[1].citationKind).isEqualTo("LOCAL_DOCUMENT")
            assertThat(result.citations[1].displayName).isEqualTo("Personal note")
            assertThat(result.citations[1].canonicalUrl).isNull()
            assertThat(result.providerPhysicalAttempts).isZero()
            assertThat(result.externalProviderCandidate).isFalse()
        } finally {
            adapter.close()
            server.shutdownNow().awaitTermination()
        }
    }

    @Test
    fun `v2 adapter rejects provider activity mixed metadata and invalid retrieval only results`() {
        val invalid =
            listOf(
                validRetrievalOnlyResponse()
                    .toBuilder()
                    .setProviderPhysicalCounts(ProviderPhysicalCounts.newBuilder().setTotal(1).setGemini(1))
                    .build(),
                validRetrievalOnlyResponse()
                    .toBuilder()
                    .setOwnerGenerationId(EXACT_GENERATION)
                    .build(),
                validRetrievalOnlyResponse()
                    .toBuilder()
                    .clearCitations()
                    .build(),
                validRetrievalOnlyResponse()
                    .toBuilder()
                    .setRequestId("req_other_000000000001")
                    .build(),
            )
        invalid.forEach { response ->
            val server = server(constantService(response))
            val adapter = adapter(server)
            try {
                assertThrows<RagV2GrpcProtocolException> { adapter.evaluate(command(), context()) }
            } finally {
                adapter.close()
                server.shutdownNow().awaitTermination()
            }
        }
    }

    @Test
    fun `v2 adapter accepts the voyage profile only when the loopback receipt remains provider-free`() {
        val server =
            server(
                constantService(
                    validRetrievalOnlyResponse()
                        .toBuilder()
                        .setEmbeddingProfileId("voyage_context_4_1024_v1")
                        .build(),
                ),
            )
        val adapter = adapter(server)
        try {
            val result = adapter.evaluate(command(), context())

            assertThat(result.embeddingProfileId).isEqualTo("voyage_context_4_1024_v1")
            assertThat(result.providerPhysicalAttempts).isZero()
            assertThat(result.voyagePhysicalCalls).isZero()
        } finally {
            adapter.close()
            server.shutdownNow().awaitTermination()
        }
    }

    @Test
    fun `v2 adapter forwards effective query consent and accepts one voyage query attempt`() {
        val captured = AtomicReference<RagAskRequest>()
        val server =
            server(
                object : RagServiceGrpc.RagServiceImplBase() {
                    override fun ask(
                        request: RagAskRequest,
                        responseObserver: StreamObserver<RagAskResponse>,
                    ) {
                        captured.set(request)
                        responseObserver.onNext(
                            validRetrievalOnlyResponse()
                                .toBuilder()
                                .setEmbeddingProfileId("voyage_context_4_1024_v1")
                                .setProviderPhysicalCounts(
                                    ProviderPhysicalCounts.newBuilder().setTotal(1).setVoyage(1),
                                ).build(),
                        )
                        responseObserver.onCompleted()
                    }
                },
            )
        val adapter = adapter(server)
        try {
            val result = adapter.evaluate(command(), context(externalQueryConsentGranted = true))

            assertThat(captured.get().consentContext)
                .isEqualTo(
                    RagConsentContext
                        .newBuilder()
                        .setGranted(true)
                        .setPolicyVersion("EXTERNAL_AI_RAG_V2")
                        .build(),
                )
            assertThat(result.embeddingProfileId).isEqualTo("voyage_context_4_1024_v1")
            assertThat(result.providerPhysicalAttempts).isEqualTo(1)
            assertThat(result.voyagePhysicalCalls).isEqualTo(1)
        } finally {
            adapter.close()
            server.shutdownNow().awaitTermination()
        }
    }

    private fun adapter(server: Server): GrpcRagV2EvaluationAdapter =
        GrpcRagV2EvaluationAdapter(
            RagV2GrpcProperties(
                target = "127.0.0.1:${server.port}",
                sharedSecret = SHARED_SECRET,
            ),
            DecisionGrpcProperties(
                target = "127.0.0.1:50051",
                sharedSecret = DECISION_SHARED_SECRET,
            ),
            RagGrpcProperties(enabled = false),
            RagV2GrpcSecretSeparation,
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

    private fun command(): RagAskCommand =
        RagAskCommand(
            question = "공개와 개인 문서의 근거를 비교해 보여 주세요.",
            answerMode = RagAnswerMode.CONCISE,
            relatedSymbols = listOf("005930"),
            topics = listOf("FINANCIAL_ENGINEERING", "RISK"),
        )

    private fun context(externalQueryConsentGranted: Boolean = false): RagV2EvaluationContext =
        RagV2EvaluationContext(
            requestId = REQUEST_ID,
            ownerScopeClaim = SCOPE_CLAIM,
            externalQueryConsentGranted = externalQueryConsentGranted,
        )

    private fun validRetrievalOnlyResponse(): RagAskResponse =
        RagAskResponse
            .newBuilder()
            .setRequestId(REQUEST_ID)
            .setStatus(RagResponseStatus.RAG_RESPONSE_STATUS_RETRIEVAL_ONLY)
            .addCitations(
                RagCitation
                    .newBuilder()
                    .setCitationId("cit_1")
                    .setSourceId("src_exact_001")
                    .setSourceRevisionId("srv_exact_001")
                    .setChunkRevisionId(PUBLIC_CHUNK)
                    .setGenerationId(EXACT_GENERATION)
                    .setPublicWeb(
                        PublicWebCitation
                            .newBuilder()
                            .setTitle("Exact source")
                            .setCanonicalUrl("https://example.org/evidence")
                            .setLocator(DocumentLocator.newBuilder().setSection("Evidence")),
                    ).build(),
            ).addCitations(
                RagCitation
                    .newBuilder()
                    .setCitationId("cit_2")
                    .setSourceId("src_owner_note_001")
                    .setSourceRevisionId("srv_owner_note_001")
                    .setChunkRevisionId(OWNER_CHUNK)
                    .setGenerationId(OWNER_GENERATION)
                    .setLocalDocument(
                        LocalDocumentCitation
                            .newBuilder()
                            .setDocumentId("doc_owner_note_0001")
                            .setDisplayName("Personal note")
                            .setLocator(DocumentLocator.newBuilder().setPage(1)),
                    ).build(),
            ).setCitationCoverage(1.0)
            .setRetrievalFailure(false)
            .setExact30GenerationId(EXACT_GENERATION)
            .setOaGenerationId(OA_GENERATION)
            .setOwnerGenerationId(OWNER_GENERATION)
            .setEmbeddingProfileId("bge_m3_local_1024_v1")
            .setFailureCode("")
            .setProviderPhysicalCounts(ProviderPhysicalCounts.getDefaultInstance())
            .addAuthorizedTop5ChunkRevisionIds(PUBLIC_CHUNK)
            .addAuthorizedTop5ChunkRevisionIds(OWNER_CHUNK)
            .setExternalProviderCandidate(false)
            .setPolicyVersion(1)
            .build()

    private companion object {
        const val SHARED_SECRET = "rag-v2-grpc-shared-secret-for-s4-7d-tests-0001"
        const val DECISION_SHARED_SECRET = "decision-grpc-shared-secret-for-s2-3-tests-0001"
        const val REQUEST_ID = "req_v2_runtime_000000000001"
        const val SCOPE_CLAIM = "rvs_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        const val EXACT_GENERATION = "rgr_11111111111111111111111111111111"
        const val OA_GENERATION = "rgr_22222222222222222222222222222222"
        const val OWNER_GENERATION = "rgr_33333333333333333333333333333333"
        const val PUBLIC_CHUNK = "rag_v2_chk_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        const val OWNER_CHUNK = "rag_v2_chk_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        val AUTH_HEADER: Metadata.Key<String> =
            Metadata.Key.of("x-decision-rag-v2-grpc-auth", Metadata.ASCII_STRING_MARSHALLER)
    }
}
