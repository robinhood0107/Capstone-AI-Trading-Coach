package com.capstone.decision.infrastructure.grpc

import com.capstone.decision.application.automation.AutomationEvidenceCandidate
import com.capstone.decision.application.automation.AutomationEvidenceProvider
import com.capstone.decision.application.automation.AutomationEvidenceSettings
import com.capstone.decision.application.automation.RawAutomationEvidence
import com.capstone.decision.application.automation.RawAutomationJudgeVerdict
import com.capstone.decision.application.automation.RawAutomationJudgement
import com.capstone.decision.application.automation.RawAutomationScreening
import com.capstone.decision.application.automation.RawAutomationScreeningBatch
import com.capstone.decision.application.automation.automationEvidenceSourceRegistry
import com.capstone.decision.contract.internal.s49.AgentEvent
import com.capstone.decision.contract.internal.s49.Completed
import com.capstone.decision.contract.internal.s49.EvidenceItem
import com.capstone.decision.contract.internal.s49.HostEvent
import com.capstone.decision.contract.internal.s49.JudgementCandidate
import com.capstone.decision.contract.internal.s49.ProviderCallPermit
import com.capstone.decision.contract.internal.s49.StartRun
import com.capstone.decision.contract.internal.s49.StrongLlmAgentServiceGrpc
import com.capstone.decision.infrastructure.vertex.S49StrongLlmProperties
import io.grpc.ManagedChannel
import io.grpc.Metadata
import io.grpc.netty.shaded.io.grpc.netty.NettyChannelBuilder
import io.grpc.stub.MetadataUtils
import io.grpc.stub.StreamObserver
import jakarta.annotation.PreDestroy
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.math.BigDecimal
import java.math.RoundingMode
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Clock
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.HexFormat
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * Automation V3의 실제 evidence transport.
 *
 * Spring이 provider permit과 결과 검증을 소유하고 Python은 loopback stream 안에서 Vertex 대화만
 * 수행한다. SCREEN은 Google discovery 한 번만 허용하고 JUDGE는 tool-free provider 두 번(1차와
 * fallback)까지만 허용한다. 계좌·잔고·주문·보유량은 StartRun에 넣지 않는다.
 */
@Component
@ConditionalOnProperty(name = ["app.s4-9.strong-llm.enabled"], havingValue = "true")
internal class GrpcAutomationEvidenceProvider(
    private val properties: S49StrongLlmProperties,
    private val grpcProperties: StrongLlmAgentGrpcProperties,
    private val objectMapper: ObjectMapper,
    private val clock: Clock = Clock.systemUTC(),
) : AutomationEvidenceProvider {
    private val sourceRegistry = automationEvidenceSourceRegistry(objectMapper)
    private val channel: ManagedChannel
    private val stub: StrongLlmAgentServiceGrpc.StrongLlmAgentServiceStub

    init {
        properties.validateEnabled()
        grpcProperties.validate(properties.enabled)
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

    @PreDestroy
    fun close() {
        channel.shutdownNow()
    }

    override fun screen(
        runId: String,
        candidates: List<AutomationEvidenceCandidate>,
        settings: AutomationEvidenceSettings,
    ): RawAutomationScreeningBatch {
        require(settings.aiJudgementEnabled && settings.provider.equals("vertex", ignoreCase = true))
        require(candidates.isNotEmpty() && candidates.size <= 31)
        val symbols = candidates.map { it.symbol }
        val start =
            baseStart(runId, "SCREEN", settings)
                .setQuestion(
                    "Google Search로 다음 한국 종목 후보 전체의 최근 공개 악재·공시를 조사하세요. " +
                        "각 근거 문장은 반드시 해당 6자리 symbol로 시작하고, 확인되지 않은 사실은 쓰지 마세요. " +
                        "후보: ${symbols.joinToString(",")}",
                ).addAllRelatedSymbols(symbols)
                .addAllTopics(listOf("AUTOMATION_NEWS_SCREEN", "PUBLIC_ADVERSE_EVIDENCE"))
                .setGoogleSearchEnabled(true)
                .setGroundingDiscoveryOnly(true)
                .setMode("EXPLAIN")
                .build()
        val completed = execute(runId, "SCREEN", start, 1, setOf("GOOGLE_DISCOVERY"))
        require(completed.vertexGenerateCallCount == 1)
        require(completed.googleGroundingQueryCount in 0..32)
        require(completed.searchBackend in setOf("VERTEX_GOOGLE", "NONE"))
        val bySymbol = symbols.associateWith { mutableListOf<RawAutomationEvidence>() }
        completed.groundingRootsList.take(5).forEach { root ->
            val registeredDomain = registeredDomain(root.domain) ?: return@forEach
            val registration = sourceRegistry.getValue(registeredDomain)
            val support =
                completed.groundingSupportsList.firstOrNull {
                    root.chunkIndex in it.chunkIndicesList && it.text.isNotBlank()
                } ?: return@forEach
            val boundedQuote = support.text.take(240)
            symbols.filter { containsSymbol(boundedQuote, it) }.forEach { symbol ->
                if (bySymbol.getValue(symbol).none { it.citationId == root.citationId }) {
                    bySymbol.getValue(symbol) +=
                        RawAutomationEvidence(
                            citationId = root.citationId,
                            sourceId = registration.first,
                            sourceType = registration.second,
                            sourceEventDate = null,
                            uri = root.uri,
                            boundedQuote = boundedQuote,
                            supportObserved = true,
                            sourceDomain = registeredDomain,
                        )
                }
            }
        }
        return RawAutomationScreeningBatch(
            screenings =
                candidates.map { candidate ->
                    val evidence = bySymbol.getValue(candidate.symbol).take(5)
                    RawAutomationScreening(
                        symbol = candidate.symbol,
                        status = "AVAILABLE",
                        verdict = "NO_VETO",
                        scoreBps = 5_000,
                        reason = if (evidence.isEmpty()) "NO_EVIDENCE" else "GROUNDING_EVIDENCE_AVAILABLE",
                        promptInjectionDetected = false,
                        evidence = evidence,
                    )
                },
            providerCallCount = completed.vertexGenerateCallCount,
            groundingQueryCount = completed.googleGroundingQueryCount,
        )
    }

    override fun judge(
        runId: String,
        candidates: List<AutomationEvidenceCandidate>,
        evidence: Map<String, List<RawAutomationEvidence>>,
        settings: AutomationEvidenceSettings,
    ): RawAutomationJudgement {
        require(settings.aiJudgementEnabled)
        require(candidates.isNotEmpty() && candidates.size <= 31)
        val canonicalEvidence =
            evidence.values
                .flatten()
                .distinctBy { it.citationId }
                .take(5)
                .mapIndexed { index, item -> evidenceItem(index + 1, item) }
        require(canonicalEvidence.isNotEmpty())
        val start =
            baseStart(runId, "JUDGE", settings)
                .setQuestion(
                    "제공된 검증 근거만 사용해 각 후보를 평가하세요. score 또는 veto에 근거를 사용하면 " +
                        "evidenceSpans.quote에 해당 citation의 전체 문자열을 byte-for-byte 복사하세요.",
                ).addAllRelatedSymbols(candidates.map { it.symbol })
                .addAllTopics(listOf("AUTOMATION_EVIDENCE_JUDGE"))
                .addAllPublicEvidence(canonicalEvidence)
                .addAllCandidates(
                    candidates.map {
                        JudgementCandidate
                            .newBuilder()
                            .setSymbol(it.symbol)
                            .setExpectedReturn(it.expectedReturn.toDouble())
                            .setModelConfidence(it.modelConfidence.toDouble())
                            .setLstmSignal("BUY")
                            .setBaselineSignal("BUY")
                            .build()
                    },
                ).setGoogleSearchEnabled(false)
                .setGroundingDiscoveryOnly(false)
                .setMode("JUDGE")
                .build()
        val completed = execute(runId, "JUDGE", start, 2, setOf("FINAL"))
        require(completed.googleGroundingQueryCount == 0 && completed.groundingRootsCount == 0)
        val root = objectMapper.readTree(completed.answerJson)
        val items = root.path("candidates")
        require(items.isArray && items.size() == candidates.size)
        val verdicts =
            (0 until items.size()).map { index ->
                val item = items[index] ?: throw IllegalArgumentException("candidate missing")
                parseVerdict(item)
            }
        return RawAutomationJudgement(
            candidates = verdicts,
            confidenceBps = scoreBps(root.path("confidence")),
            summary = root.path("summary").stringValue().also { require(it.isNotBlank()) },
            providerCallCount = completed.vertexGenerateCallCount,
        )
    }

    private fun execute(
        ownerRunId: String,
        phase: String,
        start: StartRun,
        providerCallCap: Int,
        allowedProviderPhases: Set<String>,
    ): Completed {
        require(start.serializedSize <= grpcProperties.requestMaxBytes)
        val agentRunId = agentRunId(ownerRunId, phase)
        val inbound = LinkedBlockingQueue<AgentEvent>()
        val terminalError = AtomicReference<Throwable?>()
        var outboundSequence = 1L
        var inboundSequence = 0L
        var permitted = 0
        val requestObserver =
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

                        override fun onCompleted() {
                            terminalError.compareAndSet(null, IllegalStateException("AUTOMATION_EVIDENCE_STREAM_CLOSED"))
                        }
                    },
                )
        requestObserver.onNext(
            HostEvent
                .newBuilder()
                .setRunId(agentRunId)
                .setSequence(1)
                .setCallId("start")
                .setStartRun(start)
                .build(),
        )
        val deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(grpcProperties.deadlineMillis)
        try {
            while (true) {
                terminalError.get()?.let { throw it }
                val remaining = deadline - System.nanoTime()
                require(remaining > 0) { "AUTOMATION_EVIDENCE_GRPC_TIMEOUT" }
                val event = inbound.poll(remaining, TimeUnit.NANOSECONDS) ?: continue
                require(event.runId == agentRunId && event.sequence == ++inboundSequence)
                when (event.payloadCase) {
                    AgentEvent.PayloadCase.PROVIDER_CALL_PLANNED -> {
                        val planned = event.providerCallPlanned
                        require(planned.phase in allowedProviderPhases)
                        require(++permitted <= providerCallCap)
                        require((phase == "SCREEN") == planned.googleSearchAttached)
                        requestObserver.onNext(
                            HostEvent
                                .newBuilder()
                                .setRunId(agentRunId)
                                .setSequence(++outboundSequence)
                                .setCallId(event.callId)
                                .setProviderCallPermit(
                                    ProviderCallPermit
                                        .newBuilder()
                                        .setPlannedCallId(planned.plannedCallId)
                                        .build(),
                                ).build(),
                        )
                    }
                    AgentEvent.PayloadCase.REGISTER_GROUNDING_ROOTS -> Unit
                    AgentEvent.PayloadCase.WEB_SEARCH,
                    AgentEvent.PayloadCase.WEB_READ,
                    -> throw IllegalStateException("AUTOMATION_EVIDENCE_HOST_TOOL_FORBIDDEN")
                    AgentEvent.PayloadCase.COMPLETED -> {
                        val completed = event.completed
                        require(completed.vertexGenerateCallCount == permitted)
                        require(completed.serializedSize <= grpcProperties.responseMaxBytes)
                        requestObserver.onCompleted()
                        return completed
                    }
                    AgentEvent.PayloadCase.FAILED -> throw IllegalStateException(event.failed.failureLeaf)
                    else -> throw IllegalStateException("AUTOMATION_EVIDENCE_EVENT_INVALID")
                }
            }
        } catch (error: Throwable) {
            requestObserver.onError(error)
            throw error
        }
    }

    private fun baseStart(
        runId: String,
        phase: String,
        settings: AutomationEvidenceSettings,
    ): StartRun.Builder {
        val modelId = settings.modelId?.takeIf { it.isNotBlank() } ?: properties.modelId
        return StartRun
            .newBuilder()
            .setModelId(modelId)
            .setAnswerMode("CONCISE")
            .setMaxToolRounds(0)
            .setCurrentTime(DateTimeFormatter.ISO_INSTANT.format(clock.instant()))
            .setTimezone(ZoneId.of("Asia/Seoul").id)
            .setLanguage(settings.answerLanguage)
            .setThinkingLevel(settings.thinkingLevel)
            .also {
                require(runId.matches(OWNER_RUN_ID))
                require(phase in setOf("SCREEN", "JUDGE"))
                require(settings.thinkingLevel in setOf("minimal", "low", "medium"))
            }
    }

    private fun evidenceItem(
        ordinal: Int,
        item: RawAutomationEvidence,
    ): EvidenceItem {
        val digest = item.storedQuoteSha256 ?: sha256(item.boundedQuote)
        require(digest.matches(SHA256))
        return EvidenceItem
            .newBuilder()
            .setOrdinal(ordinal)
            .setCitationId(item.citationId)
            .setChunkRevisionId("rag_v2_chk_${digest.take(32)}")
            .setCanonicalText(item.boundedQuote)
            .setCanonicalTextSha256(digest)
            .setOwnerPrivate(false)
            .build()
    }

    private fun parseVerdict(item: JsonNode): RawAutomationJudgeVerdict {
        require(item.isObject)
        val spans = item.path("evidenceSpans")
        require(spans.isArray && spans.size() <= 5)
        return RawAutomationJudgeVerdict(
            symbol = item.path("symbol").stringValue(),
            scoreBps = scoreBps(item.path("score")),
            veto = item.path("veto").booleanValue(),
            reason = item.path("reason").stringValue(),
            evidenceSpans =
                (0 until spans.size()).map { index ->
                    val span = spans[index] ?: throw IllegalArgumentException("evidence span missing")
                    span.path("citationId").stringValue() to span.path("quote").stringValue()
                },
        )
    }

    private fun scoreBps(node: JsonNode): Int {
        require(node.isNumber)
        val value = node.decimalValue()
        require(value >= BigDecimal.ZERO && value <= BigDecimal.ONE)
        return value.multiply(TEN_THOUSAND).setScale(0, RoundingMode.HALF_UP).intValueExact()
    }

    private fun registeredDomain(raw: String): String? {
        val domain = raw.trim().lowercase().removeSuffix(".")
        return sourceRegistry.keys
            .sortedByDescending { it.length }
            .firstOrNull { domain == it || domain.endsWith(".$it") }
    }

    private fun containsSymbol(
        text: String,
        symbol: String,
    ): Boolean = Regex("(?<![0-9])${Regex.escape(symbol)}(?![0-9])").containsMatchIn(text)

    private fun agentRunId(
        ownerRunId: String,
        phase: String,
    ): String = "s49_run_${sha256("$ownerRunId|$phase").take(32)}"

    private fun sha256(value: String): String =
        HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.toByteArray(StandardCharsets.UTF_8)))

    private companion object {
        val AUTH_KEY: Metadata.Key<String> = Metadata.Key.of("x-decision-strong-llm-grpc-auth", Metadata.ASCII_STRING_MARSHALLER)
        val OWNER_RUN_ID = Regex("^auto_run_[0-9a-f]{32}$")
        val SHA256 = Regex("^[0-9a-f]{64}$")
        val TEN_THOUSAND = BigDecimal("10000")
    }
}
