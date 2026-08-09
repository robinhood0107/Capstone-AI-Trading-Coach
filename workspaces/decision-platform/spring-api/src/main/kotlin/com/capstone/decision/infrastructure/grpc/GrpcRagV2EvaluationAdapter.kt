package com.capstone.decision.infrastructure.grpc

import com.capstone.decision.application.rag.RagAskCommand
import com.capstone.decision.application.rag.RagGenerationStatus
import com.capstone.decision.application.rag.RagV2EvaluationContext
import com.capstone.decision.application.rag.RagV2EvaluationPort
import com.capstone.decision.application.rag.RagV2EvaluationResult
import com.capstone.decision.application.rag.RagV2RetrievedCitation
import com.capstone.decision.contract.v2.RagAskRequest
import com.capstone.decision.contract.v2.RagAskResponse
import com.capstone.decision.contract.v2.RagConsentContext
import com.capstone.decision.contract.v2.RagResponseStatus
import com.capstone.decision.contract.v2.RagServiceGrpc
import com.capstone.decision.infrastructure.security.RagV2GrpcSecretSeparation
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

class RagV2GrpcProtocolException : IllegalStateException("RAG v2 gRPC response violated its bounded contract.")

class RagV2GrpcUnavailableException : IllegalStateException("RAG v2 gRPC service is unavailable.")

/**
 * Separate v2 proto namespace/port receives no owner ID or raw document. Spring canonicalizes every
 * citation through a DB SECURITY DEFINER function before history persistence or public response mapping.
 */
@Component
@ConditionalOnProperty(name = ["app.rag-v2.grpc.enabled"], havingValue = "true")
class GrpcRagV2EvaluationAdapter(
    private val properties: RagV2GrpcProperties,
    decisionGrpcProperties: DecisionGrpcProperties,
    ragGrpcProperties: RagGrpcProperties,
    ragV2GrpcSecretSeparation: RagV2GrpcSecretSeparation,
) : RagV2EvaluationPort,
    AutoCloseable {
    private val channel: ManagedChannel
    private val concurrency: Semaphore

    init {
        requireNotNull(ragV2GrpcSecretSeparation)
        properties.validatePurposeSeparation(decisionGrpcProperties, ragGrpcProperties)
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
        context: RagV2EvaluationContext,
    ): RagV2EvaluationResult {
        val request = request(command, context)
        if (request.serializedSize > properties.requestMaxBytes || !concurrency.tryAcquire()) {
            throw RagV2GrpcUnavailableException()
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
        context: RagV2EvaluationContext,
    ): RagAskRequest =
        RagAskRequest
            .newBuilder()
            .setRequestId(context.requestId)
            .setOwnerScopeClaim(context.ownerScopeClaim)
            .setQuestion(command.question)
            .setAnswerMode(command.answerMode.name)
            .addAllRelatedSymbols(command.relatedSymbols)
            .addAllTopics(command.topics)
            // BGE stays local. A Voyage-selected scope gets only this boolean capability; owner and consent
            // event identities remain in Spring/DB and never cross the loopback transport.
            .setConsentContext(
                RagConsentContext
                    .newBuilder()
                    .setGranted(context.externalQueryConsentGranted)
                    .setPolicyVersion(
                        if (context.externalQueryConsentGranted) {
                            "EXTERNAL_AI_RAG_V2"
                        } else {
                            "NONE"
                        },
                    ),
            ).build()

    private fun validateAndMap(
        response: RagAskResponse,
        context: RagV2EvaluationContext,
    ): RagV2EvaluationResult {
        val counts = response.providerPhysicalCounts
        val top5 = response.authorizedTop5ChunkRevisionIdsList.toList()
        val citations = response.citationsList.toList()
        if (
            response.serializedSize > properties.responseMaxBytes ||
            response.requestId != context.requestId ||
            !providerReceiptIsValid(response, context) ||
            response.externalProviderCandidate ||
            top5.size > MAX_CITATIONS ||
            top5.distinct().size != top5.size ||
            top5.any { !CHUNK_ID.matches(it) } ||
            citations.size > MAX_CITATIONS ||
            citations.map { it.chunkRevisionId }.distinct().size != citations.size ||
            !response.citationCoverage.isFinite() ||
            response.citationCoverage !in 0.0..1.0 ||
            response.guardrailFlagsCount > MAX_FLAGS ||
            response.guardrailFlagsList.distinct().size != response.guardrailFlagsCount ||
            response.guardrailFlagsList.any { !FLAG.matches(it) } ||
            (response.failureCode.isNotEmpty() && !FAILURE_CODE.matches(response.failureCode))
        ) {
            throw RagV2GrpcProtocolException()
        }
        val status = mapStatus(response.status)
        val metadata = bundleMetadata(response, status)
        val mapped = citations.mapIndexed { index, citation -> mapCitation(citation, index, top5, metadata) }
        validateStatus(response, status, mapped, top5)
        return RagV2EvaluationResult(
            generationStatus = status,
            answer = response.answer.takeIf { response.hasAnswer() },
            citations = mapped,
            citationCoverage = response.citationCoverage,
            retrievalFailure = response.retrievalFailure,
            guardrailFlags = response.guardrailFlagsList.toList(),
            failureCode = response.failureCode,
            exact30GenerationId = metadata.exact30GenerationId,
            oa112GenerationId = metadata.oa112GenerationId,
            ownerGenerationId = metadata.ownerGenerationId,
            embeddingProfileId = metadata.embeddingProfileId,
            policyVersion = metadata.policyVersion,
            providerPhysicalAttempts = counts.total,
            externalProviderCandidate = response.externalProviderCandidate,
            geminiPhysicalCalls = counts.gemini,
            openAiPhysicalCalls = counts.openai,
            voyagePhysicalCalls = counts.voyage,
        )
    }

    private fun bundleMetadata(
        response: RagAskResponse,
        status: RagGenerationStatus,
    ): BundleMetadata {
        val publicGenerationIds = setOf(response.exact30GenerationId, response.oaGenerationId)
        val absent =
            response.exact30GenerationId.isEmpty() &&
                response.oaGenerationId.isEmpty() &&
                !response.hasOwnerGenerationId() &&
                response.embeddingProfileId.isEmpty() &&
                response.policyVersion == 0L
        if (absent && status != RagGenerationStatus.RETRIEVAL_ONLY) {
            return BundleMetadata("", "", null, "", 0)
        }
        if (
            !GENERATION_ID.matches(response.exact30GenerationId) ||
            !GENERATION_ID.matches(response.oaGenerationId) ||
            response.exact30GenerationId == response.oaGenerationId ||
            response.embeddingProfileId !in RETRIEVAL_PROFILES ||
            response.policyVersion < 1 ||
            (
                response.hasOwnerGenerationId() &&
                    (
                        !GENERATION_ID.matches(response.ownerGenerationId) ||
                            response.ownerGenerationId in publicGenerationIds
                    )
            )
        ) {
            throw RagV2GrpcProtocolException()
        }
        return BundleMetadata(
            response.exact30GenerationId,
            response.oaGenerationId,
            response.ownerGenerationId.takeIf { response.hasOwnerGenerationId() },
            response.embeddingProfileId,
            response.policyVersion,
        )
    }

    /**
     * The Python engine may report one Voyage attempt only for a Voyage profile and a Spring-issued
     * effective-consent capability. No generator/OpenAI count can share this retrieval receipt.
     */
    private fun providerReceiptIsValid(
        response: RagAskResponse,
        context: RagV2EvaluationContext,
    ): Boolean {
        val counts = response.providerPhysicalCounts
        if (counts.gemini != 0 || counts.openai != 0) {
            return false
        }
        return when (response.embeddingProfileId) {
            "", BGE_PROFILE -> counts.total == 0 && counts.voyage == 0
            VOYAGE_PROFILE ->
                counts.total == counts.voyage &&
                    counts.voyage in 0..1 &&
                    (counts.voyage == 0 || context.externalQueryConsentGranted)
            else -> false
        }
    }

    private fun mapCitation(
        citation: com.capstone.decision.contract.v2.RagCitation,
        index: Int,
        top5: List<String>,
        metadata: BundleMetadata,
    ): RagV2RetrievedCitation {
        val allowedGenerationIds =
            setOf(
                metadata.exact30GenerationId,
                metadata.oa112GenerationId,
                metadata.ownerGenerationId,
            )
        if (
            citation.citationId != "cit_${index + 1}" ||
            !SOURCE_ID.matches(citation.sourceId) ||
            !SOURCE_REVISION_ID.matches(citation.sourceRevisionId) ||
            !CHUNK_ID.matches(citation.chunkRevisionId) ||
            citation.chunkRevisionId !in top5 ||
            !GENERATION_ID.matches(citation.generationId) ||
            citation.generationId !in allowedGenerationIds
        ) {
            throw RagV2GrpcProtocolException()
        }
        return when (citation.citationCase) {
            com.capstone.decision.contract.v2.RagCitation.CitationCase.PUBLIC_WEB -> {
                val public = citation.publicWeb
                if (
                    citation.generationId !in setOf(metadata.exact30GenerationId, metadata.oa112GenerationId) ||
                    !boundedText(public.title, MAX_TITLE_BYTES) ||
                    !isBoundedHttps(public.canonicalUrl)
                ) {
                    throw RagV2GrpcProtocolException()
                }
                RagV2RetrievedCitation(
                    citation.citationId,
                    citation.sourceId,
                    citation.sourceRevisionId,
                    citation.chunkRevisionId,
                    citation.generationId,
                    "PUBLIC_WEB",
                    public.title,
                    public.canonicalUrl,
                    null,
                    null,
                    locator(public.locator),
                )
            }
            com.capstone.decision.contract.v2.RagCitation.CitationCase.LOCAL_DOCUMENT -> {
                val local = citation.localDocument
                if (
                    citation.generationId != metadata.ownerGenerationId ||
                    !DOCUMENT_ID.matches(local.documentId) ||
                    !boundedDisplayName(local.displayName)
                ) {
                    throw RagV2GrpcProtocolException()
                }
                RagV2RetrievedCitation(
                    citation.citationId,
                    citation.sourceId,
                    citation.sourceRevisionId,
                    citation.chunkRevisionId,
                    citation.generationId,
                    "LOCAL_DOCUMENT",
                    null,
                    null,
                    local.documentId,
                    local.displayName,
                    locator(local.locator),
                )
            }
            else -> throw RagV2GrpcProtocolException()
        }
    }

    private fun validateStatus(
        response: RagAskResponse,
        status: RagGenerationStatus,
        citations: List<RagV2RetrievedCitation>,
        top5: List<String>,
    ) {
        when (status) {
            RagGenerationStatus.RETRIEVAL_ONLY ->
                requireProtocol(
                    !response.hasAnswer() &&
                        citations.isNotEmpty() &&
                        top5.isNotEmpty() &&
                        response.citationCoverage == 1.0 &&
                        !response.retrievalFailure &&
                        response.guardrailFlagsCount == 0 &&
                        response.failureCode.isEmpty(),
                )
            RagGenerationStatus.RETRIEVAL_FAILURE ->
                requireProtocol(
                    !response.hasAnswer() &&
                        citations.isEmpty() &&
                        top5.isEmpty() &&
                        response.citationCoverage == 0.0 &&
                        response.retrievalFailure &&
                        response.failureCode.isNotEmpty(),
                )
            else ->
                requireProtocol(
                    !response.hasAnswer() &&
                        citations.isEmpty() &&
                        top5.isEmpty() &&
                        response.citationCoverage == 0.0 &&
                        !response.retrievalFailure &&
                        response.failureCode.isNotEmpty(),
                )
        }
    }

    private fun locator(value: com.capstone.decision.contract.v2.DocumentLocator): Map<String, Any> {
        // optional locator fields are not a protobuf oneof: require exactly one to prevent ambiguous receipt mapping.
        return listOfNotNull(
            value.page.takeIf { value.hasPage() && it > 0 }?.let { mapOf("page" to it) },
            value.slide.takeIf { value.hasSlide() && it > 0 }?.let { mapOf("slide" to it) },
            value.sheet.takeIf { value.hasSheet() && locatorText(it, 128) }?.let { mapOf("sheet" to it) },
            value.section.takeIf { value.hasSection() && locatorText(it, 300) }?.let { mapOf("section" to it) },
        ).singleOrNull() ?: throw RagV2GrpcProtocolException()
    }

    private fun mapStatus(status: RagResponseStatus): RagGenerationStatus =
        when (status) {
            RagResponseStatus.RAG_RESPONSE_STATUS_RETRIEVAL_ONLY -> RagGenerationStatus.RETRIEVAL_ONLY
            RagResponseStatus.RAG_RESPONSE_STATUS_RETRIEVAL_FAILURE -> RagGenerationStatus.RETRIEVAL_FAILURE
            RagResponseStatus.RAG_RESPONSE_STATUS_BLOCKED_SENSITIVE -> RagGenerationStatus.BLOCKED_SENSITIVE
            RagResponseStatus.RAG_RESPONSE_STATUS_BLOCKED_ADVICE -> RagGenerationStatus.BLOCKED_ADVICE
            RagResponseStatus.RAG_RESPONSE_STATUS_GENERATION_UNAVAILABLE -> RagGenerationStatus.GENERATION_UNAVAILABLE
            else -> throw RagV2GrpcProtocolException()
        }

    private fun mapTransportFailure(exception: StatusRuntimeException): RuntimeException =
        when (exception.status.code) {
            Status.Code.UNAVAILABLE,
            Status.Code.DEADLINE_EXCEEDED,
            Status.Code.CANCELLED,
            -> RagV2GrpcUnavailableException()
            else -> RagV2GrpcProtocolException()
        }

    private fun authHeaders(): Metadata = Metadata().also { headers -> headers.put(AUTH_HEADER, properties.sharedSecret) }

    private fun boundedText(
        value: String,
        maximumBytes: Int,
    ): Boolean =
        value.isNotBlank() &&
            value.toByteArray(Charsets.UTF_8).size <= maximumBytes &&
            value.none { it.isISOControl() }

    private fun boundedDisplayName(value: String): Boolean = boundedText(value, 160) && value.none { it in setOf('/', '\\', ':') }

    private fun locatorText(
        value: String,
        maximum: Int,
    ): Boolean =
        value.isNotBlank() &&
            value.length <= maximum &&
            value.none { it in setOf('/', '\\', '\u0000', '\r', '\n') } &&
            !value.startsWith('.') &&
            !value.startsWith('~')

    private fun isBoundedHttps(value: String): Boolean =
        value.length <= MAX_URL_CHARS &&
            runCatching {
                val uri = URI(value)
                uri.scheme == "https" && uri.host?.isNotBlank() == true && uri.userInfo == null && uri.fragment == null
            }.getOrDefault(false)

    @PreDestroy
    override fun close() {
        channel.shutdownNow()
    }

    private fun requireProtocol(condition: Boolean) {
        if (!condition) throw RagV2GrpcProtocolException()
    }

    private data class BundleMetadata(
        val exact30GenerationId: String,
        val oa112GenerationId: String,
        val ownerGenerationId: String?,
        val embeddingProfileId: String,
        val policyVersion: Long,
    )

    private companion object {
        val AUTH_HEADER: Metadata.Key<String> =
            Metadata.Key.of("x-decision-rag-v2-grpc-auth", Metadata.ASCII_STRING_MARSHALLER)
        val SOURCE_ID = Regex("""src_[a-z0-9][a-z0-9_-]{2,95}""")
        val SOURCE_REVISION_ID = Regex("""srv_[a-z0-9][a-z0-9_-]{2,95}""")
        val CHUNK_ID = Regex("""rag_v2_chk_[0-9a-f]{32}""")
        val GENERATION_ID = Regex("""rgr_[0-9a-f]{32}""")
        val DOCUMENT_ID = Regex("""doc_[a-z0-9][a-z0-9_-]{10,95}""")
        val FLAG = Regex("""[A-Z0-9_]{1,64}""")
        val FAILURE_CODE = Regex("""[A-Z0-9_]{1,96}""")
        const val BGE_PROFILE = "bge_m3_local_1024_v1"
        const val VOYAGE_PROFILE = "voyage_context_4_1024_v1"
        val RETRIEVAL_PROFILES = setOf(BGE_PROFILE, VOYAGE_PROFILE)
        const val MAX_CITATIONS = 5
        const val MAX_FLAGS = 8
        const val MAX_TITLE_BYTES = 1_024
        const val MAX_URL_CHARS = 2_048
    }
}
