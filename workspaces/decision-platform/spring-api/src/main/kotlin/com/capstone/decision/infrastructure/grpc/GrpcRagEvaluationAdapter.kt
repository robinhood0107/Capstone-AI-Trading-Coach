package com.capstone.decision.infrastructure.grpc

import com.capstone.decision.application.rag.RagAskCommand
import com.capstone.decision.application.rag.RagCitation
import com.capstone.decision.application.rag.RagEvaluationContext
import com.capstone.decision.application.rag.RagEvaluationPort
import com.capstone.decision.application.rag.RagEvaluationResult
import com.capstone.decision.application.rag.RagGenerationStatus
import com.capstone.decision.contract.v1.RagAskRequest
import com.capstone.decision.contract.v1.RagAskResponse
import com.capstone.decision.contract.v1.RagConsentContext
import com.capstone.decision.contract.v1.RagPolicyContext
import com.capstone.decision.contract.v1.RagResponseStatus
import com.capstone.decision.contract.v1.RagServiceGrpc
import io.grpc.ManagedChannel
import io.grpc.Metadata
import io.grpc.Status
import io.grpc.StatusRuntimeException
import io.grpc.netty.shaded.io.grpc.netty.NettyChannelBuilder
import io.grpc.stub.MetadataUtils
import jakarta.annotation.PreDestroy
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import java.net.URI
import java.util.concurrent.Semaphore
import java.util.concurrent.TimeUnit

class RagGrpcProtocolException : IllegalStateException("RAG gRPC response violated its bounded contract.")

class RagGrpcUnavailableException : IllegalStateException("RAG gRPC service is unavailable.")

/**
 * 인증된 Spring request를 loopback Python RagService에 단일 시도로 전달하고 public mapping 전에 근거 범위를 재검증한다.
 */
@Component
@ConditionalOnProperty(name = ["app.rag.grpc.enabled"], havingValue = "true")
class GrpcRagEvaluationAdapter(
    private val properties: RagGrpcProperties,
    private val decisionGrpcProperties: DecisionGrpcProperties,
) : RagEvaluationPort,
    AutoCloseable {
    private val channel: ManagedChannel
    private val concurrency: Semaphore

    init {
        properties.validatePurposeSeparation(decisionGrpcProperties)
        concurrency = Semaphore(properties.concurrencyMax, true)
        channel =
            NettyChannelBuilder
                .forTarget(properties.target)
                .usePlaintext()
                .disableRetry()
                .maxInboundMessageSize(properties.responseMaxBytes)
                .build()
    }

    override fun evaluate(
        command: RagAskCommand,
        context: RagEvaluationContext,
    ): RagEvaluationResult {
        val request = request(command, context)
        if (request.serializedSize > properties.requestMaxBytes || !concurrency.tryAcquire()) {
            throw RagGrpcUnavailableException()
        }
        val response =
            try {
                RagServiceGrpc
                    .newBlockingStub(channel)
                    .withInterceptors(MetadataUtils.newAttachHeadersInterceptor(authHeaders()))
                    .withDeadlineAfter(properties.deadlineMillis, TimeUnit.MILLISECONDS)
                    .ask(request)
            } catch (exception: StatusRuntimeException) {
                throw mapTransportFailure(exception)
            } finally {
                concurrency.release()
            }
        return validateAndMap(response, context)
    }

    private fun request(
        command: RagAskCommand,
        context: RagEvaluationContext,
    ): RagAskRequest =
        RagAskRequest
            .newBuilder()
            .setRequestId(context.requestId)
            .setOwnerScopeClaim(context.ownerScopeClaim)
            .setQuestion(command.question)
            .setAnswerMode(command.answerMode.name)
            .addAllRelatedSymbols(command.relatedSymbols)
            .addAllTopics(command.topics)
            .setConsentContext(
                RagConsentContext
                    .newBuilder()
                    .setGranted(context.consentGranted)
                    .setPolicyVersion(context.consentPolicyVersion),
            ).setPolicyContext(
                RagPolicyContext
                    .newBuilder()
                    .setPolicyId(context.policyId)
                    .setPolicyVersion(context.policyVersion)
                    .setActiveGenerationId(context.activeGenerationId)
                    .setEmbeddingProfileId(context.embeddingProfileId),
            ).build()

    private fun validateAndMap(
        response: RagAskResponse,
        context: RagEvaluationContext,
    ): RagEvaluationResult {
        val counts = response.providerPhysicalCounts
        val top5 = response.authorizedTop5ChunkRevisionIdsList.toList()
        val citations = response.citationsList.toList()
        if (
            response.serializedSize > properties.responseMaxBytes ||
            response.requestId != context.requestId ||
            response.generationId != context.activeGenerationId ||
            response.embeddingProfileId != context.embeddingProfileId ||
            response.policyVersion != context.policyVersion ||
            counts.total != 0 ||
            counts.gemini != 0 ||
            counts.openai != 0 ||
            counts.voyage != 0 ||
            response.externalProviderCandidate ||
            top5.size > MAX_CITATIONS ||
            top5.distinct().size != top5.size ||
            top5.any { !CHUNK_REVISION_ID.matches(it) } ||
            citations.size > MAX_CITATIONS ||
            citations.map { it.chunkRevisionId }.distinct().size != citations.size ||
            !response.citationCoverage.isFinite() ||
            response.citationCoverage !in 0.0..1.0 ||
            response.guardrailFlagsCount > MAX_FLAGS ||
            response.guardrailFlagsList.distinct().size != response.guardrailFlagsCount ||
            response.guardrailFlagsList.any { !FLAG.matches(it) } ||
            (response.failureCode.isNotEmpty() && !FAILURE_CODE.matches(response.failureCode))
        ) {
            throw RagGrpcProtocolException()
        }
        val mappedCitations =
            citations.mapIndexed { index, citation ->
                if (
                    citation.citationId != "cit_${index + 1}" ||
                    !SOURCE_ID.matches(citation.sourceId) ||
                    !SOURCE_REVISION_ID.matches(citation.sourceRevisionId) ||
                    !CHUNK_REVISION_ID.matches(citation.chunkRevisionId) ||
                    citation.chunkRevisionId !in top5 ||
                    citation.generationId != context.activeGenerationId ||
                    citation.title.isBlank() ||
                    citation.title.length > MAX_TITLE_CHARS ||
                    citation.sectionTitle.isBlank() ||
                    citation.sectionTitle.length > MAX_SECTION_TITLE_CHARS ||
                    !isBoundedHttps(citation.canonicalUrl)
                ) {
                    throw RagGrpcProtocolException()
                }
                RagCitation(
                    citationId = citation.citationId,
                    sourceId = citation.sourceId,
                    sourceRevisionId = citation.sourceRevisionId,
                    chunkRevisionId = citation.chunkRevisionId,
                    generationId = citation.generationId,
                    title = citation.title,
                    sectionTitle = citation.sectionTitle,
                    canonicalUrl = citation.canonicalUrl,
                )
            }
        val status = mapStatus(response.status)
        val answer = response.answer.takeIf { response.hasAnswer() }
        if (answer?.toByteArray(Charsets.UTF_8)?.size?.let { it > MAX_ANSWER_BYTES } == true) {
            throw RagGrpcProtocolException()
        }
        validateStatus(response, status, answer, mappedCitations, top5)
        return RagEvaluationResult(
            generationStatus = status,
            answer = answer,
            citations = mappedCitations,
            citationCoverage = response.citationCoverage,
            retrievalFailure = response.retrievalFailure,
            guardrailFlags = response.guardrailFlagsList.toList(),
            providerPhysicalAttempts = counts.total,
            externalProviderCandidate = response.externalProviderCandidate,
            geminiPhysicalCalls = counts.gemini,
            openAiPhysicalCalls = counts.openai,
            voyagePhysicalCalls = counts.voyage,
        )
    }

    private fun validateStatus(
        response: RagAskResponse,
        status: RagGenerationStatus,
        answer: String?,
        citations: List<RagCitation>,
        top5: List<String>,
    ) {
        when (status) {
            RagGenerationStatus.ANSWERED ->
                requireProtocol(
                    !answer.isNullOrBlank() &&
                        citations.isNotEmpty() &&
                        top5.isNotEmpty() &&
                        response.citationCoverage == 1.0 &&
                        !response.retrievalFailure &&
                        response.failureCode.isEmpty(),
                )
            RagGenerationStatus.RETRIEVAL_FAILURE ->
                requireProtocol(
                    answer == null &&
                        citations.isEmpty() &&
                        response.citationCoverage == 0.0 &&
                        response.retrievalFailure &&
                        response.failureCode.isNotEmpty(),
                )
            else ->
                requireProtocol(
                    answer == null &&
                        citations.isEmpty() &&
                        response.citationCoverage == 0.0 &&
                        !response.retrievalFailure &&
                        response.failureCode.isNotEmpty(),
                )
        }
    }

    private fun mapStatus(status: RagResponseStatus): RagGenerationStatus =
        when (status) {
            RagResponseStatus.RAG_RESPONSE_STATUS_ANSWERED -> RagGenerationStatus.ANSWERED
            RagResponseStatus.RAG_RESPONSE_STATUS_RETRIEVAL_ONLY -> RagGenerationStatus.RETRIEVAL_ONLY
            RagResponseStatus.RAG_RESPONSE_STATUS_RETRIEVAL_FAILURE -> RagGenerationStatus.RETRIEVAL_FAILURE
            RagResponseStatus.RAG_RESPONSE_STATUS_BLOCKED_SENSITIVE -> RagGenerationStatus.BLOCKED_SENSITIVE
            RagResponseStatus.RAG_RESPONSE_STATUS_BLOCKED_ADVICE -> RagGenerationStatus.BLOCKED_ADVICE
            RagResponseStatus.RAG_RESPONSE_STATUS_GENERATION_UNAVAILABLE -> RagGenerationStatus.GENERATION_UNAVAILABLE
            else -> throw RagGrpcProtocolException()
        }

    private fun mapTransportFailure(exception: StatusRuntimeException): RuntimeException =
        when (exception.status.code) {
            Status.Code.UNAVAILABLE,
            Status.Code.DEADLINE_EXCEEDED,
            Status.Code.CANCELLED,
            -> RagGrpcUnavailableException()
            else -> RagGrpcProtocolException()
        }

    private fun authHeaders(): Metadata = Metadata().also { headers -> headers.put(AUTH_HEADER, properties.sharedSecret) }

    private fun isBoundedHttps(value: String): Boolean =
        value.length <= MAX_URL_CHARS &&
            runCatching {
                val uri = URI(value)
                uri.scheme == "https" && !uri.host.isNullOrBlank() && uri.userInfo == null
            }.getOrDefault(false)

    @PreDestroy
    override fun close() {
        channel.shutdownNow()
    }

    private fun requireProtocol(condition: Boolean) {
        if (!condition) throw RagGrpcProtocolException()
    }

    private companion object {
        val AUTH_HEADER: Metadata.Key<String> =
            Metadata.Key.of("x-decision-grpc-auth", Metadata.ASCII_STRING_MARSHALLER)
        val SOURCE_ID = Regex("""src_project_[a-z0-9][a-z0-9_]*_[0-9]{3}""")
        val SOURCE_REVISION_ID = Regex("""src_rev_[0-9a-f]{32}""")
        val CHUNK_REVISION_ID = Regex("""rag_chk_[0-9a-f]{32}""")
        val FLAG = Regex("""[A-Z0-9_]{1,64}""")
        val FAILURE_CODE = Regex("""[A-Z0-9_]{1,64}""")
        const val MAX_CITATIONS = 5
        const val MAX_FLAGS = 8
        const val MAX_ANSWER_BYTES = 8_192
        const val MAX_TITLE_CHARS = 1_024
        const val MAX_SECTION_TITLE_CHARS = 512
        const val MAX_URL_CHARS = 2_048
    }
}
