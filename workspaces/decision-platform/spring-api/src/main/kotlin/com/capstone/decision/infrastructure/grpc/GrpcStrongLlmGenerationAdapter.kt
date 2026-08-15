package com.capstone.decision.infrastructure.grpc

import com.capstone.decision.application.rag.RagGenerationStatus
import com.capstone.decision.application.rag.RagV2VertexEvidence
import com.capstone.decision.application.rag.RagV2VertexGenerationCommand
import com.capstone.decision.application.rag.RagV2VertexGenerationPort
import com.capstone.decision.application.rag.RagV2VertexGenerationResult
import com.capstone.decision.application.rag.RagV2VertexResponseValidator
import com.capstone.decision.application.rag.StrongLlmAnswerBasis
import com.capstone.decision.application.rag.StrongLlmEvidenceSupportType
import com.capstone.decision.application.rag.StrongLlmWebCitation
import com.capstone.decision.contract.internal.s49.AgentEvent
import com.capstone.decision.contract.internal.s49.Completed
import com.capstone.decision.contract.internal.s49.EvidenceItem
import com.capstone.decision.contract.internal.s49.HostEvent
import com.capstone.decision.contract.internal.s49.ProviderCallPermit
import com.capstone.decision.contract.internal.s49.StartRun
import com.capstone.decision.contract.internal.s49.StrongLlmAgentServiceGrpc
import com.capstone.decision.contract.internal.s49.ToolResult
import com.capstone.decision.infrastructure.mcp.ResearchToolFacade
import com.capstone.decision.infrastructure.mcp.S49SearchUnavailableException
import com.capstone.decision.infrastructure.vertex.S49GoogleGroundingBudgetPort
import com.capstone.decision.infrastructure.vertex.S49StrongLlmCompletionPort
import com.capstone.decision.infrastructure.vertex.S49StrongLlmProperties
import com.capstone.decision.infrastructure.vertex.S49StrongLlmUsageV2
import com.capstone.decision.infrastructure.vertex.S49StrongLlmUsageV2Port
import io.grpc.ManagedChannel
import io.grpc.Metadata
import io.grpc.netty.shaded.io.grpc.netty.NettyChannelBuilder
import io.grpc.stub.MetadataUtils
import io.grpc.stub.StreamObserver
import jakarta.annotation.PreDestroy
import org.slf4j.LoggerFactory
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import tools.jackson.databind.json.JsonMapper
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Clock
import java.time.Duration
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/** Kotlin이 permit·budget·tool·검증·ledger를 소유하고 Python은 provider 대화 상태만 수행한다. */
@Component
@ConditionalOnProperty(name = ["app.s4-9.strong-llm.enabled"], havingValue = "true")
internal class GrpcStrongLlmGenerationAdapter(
    private val strongLlmProperties: S49StrongLlmProperties,
    private val grpcProperties: StrongLlmAgentGrpcProperties,
    private val googleBudget: S49GoogleGroundingBudgetPort,
    private val usageLedger: S49StrongLlmUsageV2Port,
    private val completion: S49StrongLlmCompletionPort,
    private val groundingProvenance: S49GroundingProvenancePort,
    private val researchTools: ResearchToolFacade,
    private val clock: Clock = Clock.systemUTC(),
) : RagV2VertexGenerationPort {
    private val validator = RagV2VertexResponseValidator()
    private val mapper = JsonMapper.builder().build()
    private val channel: ManagedChannel
    private val stub: StrongLlmAgentServiceGrpc.StrongLlmAgentServiceStub

    init {
        strongLlmProperties.validateEnabled()
        grpcProperties.validate(strongLlmProperties.enabled)
        channel =
            NettyChannelBuilder
                .forTarget(grpcProperties.target)
                .usePlaintext()
                .maxInboundMessageSize(grpcProperties.responseMaxBytes)
                .build()
        val metadata = Metadata()
        metadata.put(AUTH_KEY, grpcProperties.sharedSecret)
        stub =
            StrongLlmAgentServiceGrpc
                .newStub(channel)
                .withInterceptors(MetadataUtils.newAttachHeadersInterceptor(metadata))
    }

    override fun isActivationEnabled(): Boolean = true

    override fun generate(command: RagV2VertexGenerationCommand): RagV2VertexGenerationResult {
        val runId = "s49_run_${sha256(command.requestId).take(32)}"
        researchTools.openSession(runId)
        researchTools.registerUserRoots(runId, command.question)
        val googlePermit = googleBudget.reserve(command.ownerUserId, command.requestId)
        val inbound = LinkedBlockingQueue<AgentEvent>()
        val terminalError = AtomicReference<Throwable?>()
        var requestObserver: StreamObserver<HostEvent>? = null
        var outboundSequence = 1L
        var inboundSequence = 0L
        var providerAttempted = false
        val hostBudget = StrongLlmHostBudget()
        var completed: Completed? = null
        val readEvidence = mutableListOf<RagV2VertexEvidence>()
        val readWebCitations = mutableListOf<StrongLlmWebCitation>()
        try {
            requestObserver =
                stub
                    .withDeadlineAfter(grpcProperties.deadlineMillis, TimeUnit.MILLISECONDS)
                    .generate(
                        object : StreamObserver<AgentEvent> {
                            override fun onNext(value: AgentEvent) {
                                inbound.put(value)
                            }

                            override fun onError(error: Throwable) {
                                terminalError.set(error)
                            }

                            override fun onCompleted() = Unit
                        },
                    )
            requestObserver.onNext(startEvent(runId, command, googlePermit.googleEnabled))
            val deadline = System.nanoTime() + Duration.ofMillis(grpcProperties.deadlineMillis).toNanos()
            while (completed == null) {
                terminalError.get()?.let { throw it }
                val remaining = deadline - System.nanoTime()
                if (remaining <= 0) throw IllegalStateException("STRONG_LLM_GRPC_TIMEOUT")
                val event = inbound.poll(remaining, TimeUnit.NANOSECONDS) ?: continue
                require(event.runId == runId && event.sequence == ++inboundSequence)
                when (event.payloadCase) {
                    AgentEvent.PayloadCase.PROVIDER_CALL_PLANNED -> {
                        hostBudget.permitProvider(event.providerCallPlanned.phase)
                        providerAttempted = true
                        requestObserver.onNext(
                            hostEvent(
                                runId,
                                ++outboundSequence,
                                event.callId,
                                providerCallPermit =
                                    ProviderCallPermit
                                        .newBuilder()
                                        .setPlannedCallId(event.providerCallPlanned.plannedCallId)
                                        .build(),
                            ),
                        )
                    }
                    AgentEvent.PayloadCase.WEB_SEARCH -> {
                        val searchCalls = hostBudget.permitSearch()
                        val results =
                            try {
                                researchTools.search(runId, event.webSearch.query)
                            } catch (error: S49SearchUnavailableException) {
                                groundingProvenance.recordSearch(
                                    command.ownerUserId,
                                    command.requestId,
                                    searchCalls,
                                    "SEARXNG",
                                    0,
                                    "SEARCH_UNAVAILABLE",
                                )
                                throw error
                            }
                        groundingProvenance.recordSearch(
                            command.ownerUserId,
                            command.requestId,
                            searchCalls,
                            "SEARXNG",
                            results.size,
                            "COMMITTED",
                        )
                        requestObserver.onNext(
                            toolResultEvent(
                                runId,
                                ++outboundSequence,
                                event.callId,
                                "capstone_web_search",
                                mapper.writeValueAsString(
                                    mapOf(
                                        "results" to results,
                                    ),
                                ),
                            ),
                        )
                    }
                    AgentEvent.PayloadCase.WEB_READ -> {
                        val readCalls = hostBudget.permitRead()
                        val result = researchTools.read(runId, event.webRead.resultId, null)
                        // 공개 계약의 citation 최대 5개를 유지하려고 웹 근거가 낮은 rank의 local slot부터 대체한다.
                        // 아래 validationEvidence 결합이 같은 ID의 local 원문을 제거하므로 exact quote는 웹 원문 하나에만 결속된다.
                        val citationId = "cit_${6 - readCalls}".takeIf { readCalls <= 5 }
                        citationId?.let { id ->
                            val textHash = sha256(result.document.text)
                            val source =
                                result.source.copy(
                                    title = result.document.title,
                                    url = result.document.canonicalUrl,
                                )
                            groundingProvenance.recordRead(
                                command.ownerUserId,
                                command.requestId,
                                source,
                                id,
                                textHash,
                            )
                            readEvidence +=
                                RagV2VertexEvidence(
                                    ordinal = readEvidence.size + 1,
                                    citationId = id,
                                    chunkRevisionId = "rag_v2_chk_${textHash.take(32)}",
                                    canonicalText = result.document.text,
                                    canonicalTextSha256 = textHash,
                                    title = result.document.title,
                                    canonicalUrl = result.document.canonicalUrl,
                                    sectionTitle =
                                        java.net.URI
                                            .create(result.document.canonicalUrl)
                                            .host,
                                )
                            readWebCitations +=
                                StrongLlmWebCitation(
                                    citationId = id,
                                    sourceId = "src_web_${sha256(result.document.canonicalUrl).take(24)}",
                                    title = result.document.title,
                                    sectionTitle =
                                        java.net.URI
                                            .create(result.document.canonicalUrl)
                                            .host,
                                    canonicalUrl = result.document.canonicalUrl,
                                    provenanceResultId = result.source.resultId,
                                )
                        }
                        requestObserver.onNext(
                            toolResultEvent(
                                runId,
                                ++outboundSequence,
                                event.callId,
                                "capstone_web_read",
                                mapper.writeValueAsString(
                                    mapOf(
                                        "resultId" to result.source.resultId,
                                        "title" to result.document.title,
                                        "canonicalUrl" to result.document.canonicalUrl,
                                        "text" to result.document.text,
                                        "citationId" to citationId,
                                        "discoveredLinks" to result.discoveredLinks,
                                    ),
                                ),
                            ),
                        )
                    }
                    AgentEvent.PayloadCase.REGISTER_GROUNDING_ROOTS -> registerGrounding(runId, event.registerGroundingRoots.rootsList)
                    AgentEvent.PayloadCase.COMPLETED -> completed = event.completed
                    AgentEvent.PayloadCase.FAILED -> throw IllegalStateException(event.failed.failureLeaf)
                    else -> throw IllegalStateException("STRONG_LLM_GRPC_EVENT_INVALID")
                }
            }
            requestObserver.onCompleted()
            val result = requireNotNull(completed)
            hostBudget.verifyCompleted(result.vertexGenerateCallCount, result.searchBackend)
            registerGrounding(runId, result.groundingRootsList)
            if (result.groundingRootsCount > 0) {
                groundingProvenance.record(
                    command.ownerUserId,
                    command.requestId,
                    result.groundingRootsList,
                    result.groundingSupportsList,
                )
            }
            if (result.searchBackend == "VERTEX_GOOGLE") {
                groundingProvenance.recordSearch(
                    command.ownerUserId,
                    command.requestId,
                    1,
                    "VERTEX_GOOGLE",
                    result.googleGroundingQueryCount,
                    "COMMITTED",
                )
            }
            val groundingEvidence = groundingEvidence(result)
            val generatedEvidence = groundingEvidence + readEvidence
            val validationEvidence =
                (
                    command.evidence.filter { local -> generatedEvidence.none { it.citationId == local.citationId } } +
                        generatedEvidence
                ).sortedBy { it.citationId.removePrefix("cit_").toInt() }
                    .take(5)
                    .mapIndexed { index, evidence -> evidence.copy(ordinal = index + 1) }
            val validated = validator.validate(result.answerJson, validationEvidence)
            val usage =
                S49StrongLlmUsageV2(
                    result.promptTokenCount,
                    result.outputTokenCount,
                    hostBudget.toolRounds,
                    hostBudget.searchCalls,
                    hostBudget.readCalls,
                    result.vertexGenerateCallCount,
                    result.googleGroundingQueryCount,
                    result.searchBackend,
                    result.evidenceValidationMode,
                )
            completion.commit(
                command.ownerUserId,
                googlePermit.reservationId,
                result.googleGroundingQueryCount,
                command.requestId,
                strongLlmProperties.modelId,
                validated.basis,
                validationEvidence,
                usage,
            )
            val webCitations = webCitations(result) + readWebCitations
            return RagV2VertexGenerationResult(
                generationStatus =
                    if (validated.basis == StrongLlmAnswerBasis.INSUFFICIENT_EVIDENCE) {
                        RagGenerationStatus.RETRIEVAL_ONLY
                    } else {
                        RagGenerationStatus.ANSWERED
                    },
                answer = validated.answer,
                citationIds = validated.citationIds,
                failureCode = "",
                answerBasis = validated.basis,
                validationStatus = validated.validationStatus,
                warnings = validated.warnings,
                citationCoverage = validated.citationCoverage,
                webCitations = webCitations,
            )
        } catch (error: Exception) {
            requestObserver?.onError(
                io.grpc.Status.CANCELLED
                    .withDescription(failureLeaf(error))
                    .asRuntimeException(),
            )
            googlePermit.reservationId?.let { reservation ->
                runCatching {
                    if (providerAttempted) {
                        googleBudget.unknown(
                            command.ownerUserId,
                            reservation,
                        )
                    } else {
                        googleBudget.release(command.ownerUserId, reservation)
                    }
                }
            }
            val usage =
                S49StrongLlmUsageV2(
                    0,
                    0,
                    hostBudget.toolRounds,
                    hostBudget.searchCalls,
                    hostBudget.readCalls,
                    hostBudget.providerCalls,
                    0,
                    "NONE",
                    "NONE",
                )
            runCatching {
                usageLedger.failed(
                    command.ownerUserId,
                    command.requestId,
                    strongLlmProperties.modelId,
                    command.evidence,
                    usage,
                    failureLeaf(error),
                    providerAttempted,
                )
            }
            LOGGER.warn("s4_9_strong_llm_grpc_failed leaf={}", failureLeaf(error))
            return RagV2VertexGenerationResult(
                generationStatus = RagGenerationStatus.GENERATION_UNAVAILABLE,
                answer = null,
                citationIds = emptyList(),
                failureCode = "GENERATION_UNAVAILABLE",
            )
        } finally {
            researchTools.closeSession(runId)
        }
    }

    private fun startEvent(
        runId: String,
        command: RagV2VertexGenerationCommand,
        googleEnabled: Boolean,
    ): HostEvent {
        val publicEvidence = command.evidence.filterNot { it.ownerPrivate }.map(::evidenceItem)
        val ownerEvidence = command.evidence.filter { it.ownerPrivate }.map(::evidenceItem)
        val start =
            StartRun
                .newBuilder()
                .setModelId(strongLlmProperties.modelId)
                .setQuestion(command.question)
                .setAnswerMode(command.answerMode.name)
                .addAllRelatedSymbols(command.relatedSymbols)
                .addAllTopics(command.topics)
                .addAllPublicEvidence(publicEvidence)
                .addAllOwnerEvidence(ownerEvidence)
                .setGoogleSearchEnabled(googleEnabled)
                .setMaxToolRounds(3)
                .setCurrentTime(DateTimeFormatter.ISO_INSTANT.format(clock.instant()))
                .setTimezone(ZoneId.systemDefault().id)
                .build()
        return HostEvent
            .newBuilder()
            .setRunId(runId)
            .setSequence(1)
            .setCallId("start")
            .setStartRun(start)
            .build()
    }

    private fun evidenceItem(value: RagV2VertexEvidence): EvidenceItem =
        EvidenceItem
            .newBuilder()
            .setOrdinal(value.ordinal)
            .setCitationId(value.citationId)
            .setChunkRevisionId(value.chunkRevisionId)
            .setCanonicalText(value.canonicalText)
            .setCanonicalTextSha256(value.canonicalTextSha256)
            .setOwnerPrivate(value.ownerPrivate)
            .build()

    private fun groundingEvidence(result: Completed): List<RagV2VertexEvidence> =
        result.groundingRootsList.take(5).mapIndexedNotNull { index, root ->
            val supports =
                result.groundingSupportsList
                    .filter { root.chunkIndex in it.chunkIndicesList }
                    .map { it.text }
                    .distinct()
            val text = supports.joinToString("\n").takeIf { it.isNotBlank() } ?: return@mapIndexedNotNull null
            val hash = sha256(text)
            RagV2VertexEvidence(
                ordinal = index + 1,
                citationId = root.citationId,
                chunkRevisionId = "rag_v2_chk_${hash.take(32)}",
                canonicalText = text,
                canonicalTextSha256 = hash,
                supportType = StrongLlmEvidenceSupportType.GOOGLE_GROUNDING,
                title = root.title,
                canonicalUrl = root.uri,
                sectionTitle = root.domain,
            )
        }

    private fun webCitations(result: Completed): List<StrongLlmWebCitation> =
        result.groundingRootsList.take(5).map { root ->
            StrongLlmWebCitation(
                citationId = root.citationId,
                sourceId = "src_web_${sha256(root.uri).take(24)}",
                title = root.title,
                sectionTitle = root.domain,
                canonicalUrl = root.uri,
                provenanceResultId = root.resultId,
            )
        }

    private fun registerGrounding(
        runId: String,
        roots: List<com.capstone.decision.contract.internal.s49.GroundingRoot>,
    ) {
        roots.take(5).forEach { root ->
            researchTools.registerGoogleGrounding(runId, root.resultId, root.title, root.uri, root.domain)
        }
    }

    private fun hostEvent(
        runId: String,
        sequence: Long,
        callId: String,
        providerCallPermit: ProviderCallPermit,
    ): HostEvent =
        HostEvent
            .newBuilder()
            .setRunId(runId)
            .setSequence(sequence)
            .setCallId(callId)
            .setProviderCallPermit(providerCallPermit)
            .build()

    private fun toolResultEvent(
        runId: String,
        sequence: Long,
        callId: String,
        name: String,
        resultJson: String,
    ): HostEvent =
        HostEvent
            .newBuilder()
            .setRunId(runId)
            .setSequence(sequence)
            .setCallId(callId)
            .setToolResult(
                ToolResult
                    .newBuilder()
                    .setToolCallId(callId)
                    .setToolName(name)
                    .setResultJson(resultJson)
                    .build(),
            ).build()

    private fun failureLeaf(error: Exception): String =
        error.message?.takeIf { it.matches(FAILURE_LEAF) } ?: error::class
            .simpleName
            .orEmpty()
            .uppercase()
            .take(96)

    private fun sha256(value: String): String {
        val bytes = value.toByteArray(StandardCharsets.UTF_8)
        return try {
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        } finally {
            bytes.fill(0)
        }
    }

    @PreDestroy
    fun close() {
        channel.shutdownNow()
    }

    private companion object {
        val AUTH_KEY: Metadata.Key<String> = Metadata.Key.of("x-decision-strong-llm-grpc-auth", Metadata.ASCII_STRING_MARSHALLER)
        val FAILURE_LEAF = Regex("^[A-Z0-9_]{3,96}$")
        val LOGGER = LoggerFactory.getLogger(GrpcStrongLlmGenerationAdapter::class.java)
    }
}

/** Python process가 오동작해도 Kotlin host가 provider와 web 물리 호출 상한을 permit 전에 강제한다. */
internal class StrongLlmHostBudget {
    var providerCalls: Int = 0
        private set
    var toolRounds: Int = 0
        private set
    var searchCalls: Int = 0
        private set
    var readCalls: Int = 0
        private set

    private var route: Route? = null
    private var finalPlanned = false

    fun permitProvider(phase: String) {
        ensure(!finalPlanned, "STRONG_LLM_HOST_PROVIDER_AFTER_FINAL")
        ensure(providerCalls < MAX_PROVIDER_CALLS, "STRONG_LLM_HOST_PROVIDER_BUDGET_EXHAUSTED")
        when (phase) {
            "GOOGLE_DISCOVERY" -> {
                ensure(route == null && providerCalls == 0, "STRONG_LLM_HOST_PROVIDER_PHASE_INVALID")
                route = Route.GOOGLE
            }
            "OWNER_FINAL" -> {
                ensure(route == Route.GOOGLE && providerCalls == 1, "STRONG_LLM_HOST_PROVIDER_PHASE_INVALID")
                finalPlanned = true
            }
            "SEARXNG_TOOL" -> {
                ensure(route in setOf(null, Route.SEARXNG), "STRONG_LLM_HOST_PROVIDER_PHASE_INVALID")
                ensure(toolRounds < MAX_TOOL_ROUNDS, "STRONG_LLM_HOST_TOOL_ROUND_BUDGET_EXHAUSTED")
                route = Route.SEARXNG
                toolRounds += 1
            }
            "FINAL" -> {
                ensure(route in setOf(null, Route.SEARXNG), "STRONG_LLM_HOST_PROVIDER_PHASE_INVALID")
                route = Route.SEARXNG
                finalPlanned = true
            }
            else -> fail("STRONG_LLM_HOST_PROVIDER_PHASE_INVALID")
        }
        providerCalls += 1
    }

    fun permitSearch(): Int {
        ensure(route == Route.SEARXNG, "STRONG_LLM_HOST_TOOL_ROUTE_INVALID")
        ensure(searchCalls < MAX_SEARCH_CALLS, "STRONG_LLM_HOST_SEARCH_BUDGET_EXHAUSTED")
        searchCalls += 1
        return searchCalls
    }

    fun permitRead(): Int {
        ensure(route == Route.SEARXNG, "STRONG_LLM_HOST_TOOL_ROUTE_INVALID")
        ensure(readCalls < MAX_READ_CALLS, "STRONG_LLM_HOST_READ_BUDGET_EXHAUSTED")
        readCalls += 1
        return readCalls
    }

    fun verifyCompleted(
        reportedProviderCalls: Int,
        searchBackend: String,
    ) {
        ensure(reportedProviderCalls == providerCalls, "STRONG_LLM_HOST_PROVIDER_COUNT_MISMATCH")
        when (route) {
            Route.GOOGLE -> ensure(searchBackend in setOf("NONE", "VERTEX_GOOGLE"), "STRONG_LLM_HOST_SEARCH_BACKEND_MISMATCH")
            Route.SEARXNG -> ensure(searchBackend == "SEARXNG", "STRONG_LLM_HOST_SEARCH_BACKEND_MISMATCH")
            null -> fail("STRONG_LLM_HOST_PROVIDER_PHASE_INVALID")
        }
    }

    private fun ensure(
        condition: Boolean,
        leaf: String,
    ) {
        if (!condition) fail(leaf)
    }

    private fun fail(leaf: String): Nothing = throw IllegalStateException(leaf)

    private enum class Route {
        GOOGLE,
        SEARXNG,
    }

    private companion object {
        const val MAX_PROVIDER_CALLS = 4
        const val MAX_TOOL_ROUNDS = 3
        const val MAX_SEARCH_CALLS = 3
        const val MAX_READ_CALLS = 8
    }
}
