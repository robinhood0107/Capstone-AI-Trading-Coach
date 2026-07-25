package com.capstone.decision.infrastructure.grpc

import com.capstone.decision.application.risk.port.DisclosureEventEvidence
import com.capstone.decision.application.risk.port.DisclosureRiskPort
import com.capstone.decision.application.risk.port.DisclosureRiskSnapshot
import com.capstone.decision.application.risk.port.EvaluationSourceRequest
import com.capstone.decision.contract.v1.DisclosureObservationServiceGrpc
import com.capstone.decision.contract.v1.GetDisclosureEventsRequest
import com.capstone.decision.contract.v1.GetDisclosureEventsResponse
import com.capstone.decision.domain.risk.CanonicalJson
import com.capstone.decision.domain.risk.EvaluationBounds
import com.capstone.decision.domain.risk.MetricCell
import com.capstone.decision.domain.risk.MetricIssueCode
import com.capstone.decision.domain.risk.MetricSource
import io.grpc.ManagedChannel
import io.grpc.Metadata
import io.grpc.Status
import io.grpc.StatusRuntimeException
import io.grpc.netty.shaded.io.grpc.netty.NettyChannelBuilder
import io.grpc.stub.MetadataUtils
import jakarta.annotation.PreDestroy
import org.slf4j.LoggerFactory
import org.slf4j.MDC
import org.springframework.stereotype.Component
import java.math.BigDecimal
import java.time.Clock
import java.time.DateTimeException
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.util.UUID
import java.util.concurrent.Semaphore
import java.util.concurrent.TimeUnit
import kotlin.math.max
import kotlin.math.min

class DisclosureGrpcProtocolException : IllegalStateException("Stored disclosure gRPC response violated its bounded contract.")

/**
 * loopback stored-observation RPC만 단일 시도하며 provider HTTP나 health RPC를 business evidence로 사용하지 않는다.
 */
@Component
class GrpcDisclosureRiskAdapter(
    private val properties: DecisionGrpcProperties,
    private val clock: Clock,
) : DisclosureRiskPort,
    AutoCloseable {
    private val channel: ManagedChannel
    private val concurrency = Semaphore(properties.concurrencyMax, true)

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

    override fun load(request: EvaluationSourceRequest): MetricCell<DisclosureRiskSnapshot> {
        val startedAtNanos = System.nanoTime()
        val traceId = MDC.get(TRACE_ID_MDC_KEY) ?: TRACE_UNAVAILABLE
        val parentSpanId = MDC.get(SPAN_ID_MDC_KEY) ?: TRACE_UNAVAILABLE
        val spanId =
            UUID
                .randomUUID()
                .toString()
                .replace("-", "")
                .take(16)
        MDC.put(SPAN_ID_MDC_KEY, spanId)
        return try {
            loadOnce(request).also { result ->
                logSpan(request, traceId, parentSpanId, spanId, resultKind(result), startedAtNanos)
            }
        } catch (exception: Exception) {
            logSpan(request, traceId, parentSpanId, spanId, RESULT_TECHNICAL_ERROR, startedAtNanos)
            throw exception
        } finally {
            if (parentSpanId == TRACE_UNAVAILABLE) {
                MDC.remove(SPAN_ID_MDC_KEY)
            } else {
                MDC.put(SPAN_ID_MDC_KEY, parentSpanId)
            }
        }
    }

    private fun loadOnce(request: EvaluationSourceRequest): MetricCell<DisclosureRiskSnapshot> {
        val callNow = clock.instant()
        val deadlineMillis = effectiveDeadlineMillis(request.evaluationAsOf, callNow)
        if (deadlineMillis <= 0) {
            return MetricCell.Error(MetricIssueCode.DISCLOSURE_UNAVAILABLE)
        }
        val asOf = request.evaluationAsOf.atZone(SEOUL).toLocalDate()
        val windowFrom = asOf.minusDays(DISCLOSURE_LOOKBACK_DAYS)
        val rpcRequest =
            GetDisclosureEventsRequest
                .newBuilder()
                .setSymbol(request.orderIntent.symbol)
                .setCorpCode("")
                .setAsOf(asOf.toString())
                .setWindowFrom(windowFrom.toString())
                .setWindowTo(asOf.toString())
                .build()
        if (rpcRequest.serializedSize > properties.requestMaxBytes) {
            throw DisclosureGrpcProtocolException()
        }
        if (!concurrency.tryAcquire()) {
            return MetricCell.Error(MetricIssueCode.DISCLOSURE_UNAVAILABLE)
        }
        val response =
            try {
                DisclosureObservationServiceGrpc
                    .newBlockingStub(channel)
                    .withInterceptors(MetadataUtils.newAttachHeadersInterceptor(authHeaders()))
                    .withDeadlineAfter(deadlineMillis, TimeUnit.MILLISECONDS)
                    .getDisclosureEvents(rpcRequest)
            } catch (exception: StatusRuntimeException) {
                return mapTransportFailure(exception)
            } finally {
                concurrency.release()
            }
        return responseCell(
            response = response,
            expectedSymbol = request.orderIntent.symbol,
            expectedAsOf = asOf,
            expectedWindowFrom = windowFrom,
            retrievedAt = callNow,
        )
    }

    private fun logSpan(
        request: EvaluationSourceRequest,
        traceId: String,
        parentSpanId: String,
        spanId: String,
        result: String,
        startedAtNanos: Long,
    ) {
        log
            .atInfo()
            .addKeyValue("trace_id", traceId)
            .addKeyValue("span_id", spanId)
            .addKeyValue("parent_span_id", parentSpanId)
            .addKeyValue("evaluationId", request.evaluationId)
            .addKeyValue("decisionId", request.decisionId)
            .addKeyValue("rpc.system", "grpc")
            .addKeyValue("rpc.method", RPC_METHOD)
            .addKeyValue("result", result)
            .addKeyValue(
                "durationMs",
                TimeUnit.NANOSECONDS.toMillis((System.nanoTime() - startedAtNanos).coerceAtLeast(0)),
            ).log("grpc.stored_observation.completed")
    }

    private fun resultKind(result: MetricCell<DisclosureRiskSnapshot>): String =
        when (result) {
            is MetricCell.Available -> RESULT_AVAILABLE
            else -> RESULT_TYPED_UNAVAILABLE
        }

    private fun responseCell(
        response: GetDisclosureEventsResponse,
        expectedSymbol: String,
        expectedAsOf: LocalDate,
        expectedWindowFrom: LocalDate,
        retrievedAt: Instant,
    ): MetricCell<DisclosureRiskSnapshot> {
        validateEnvelope(response, expectedSymbol, expectedAsOf, expectedWindowFrom)
        val sourceRefs = response.sourceRefsList.toList()
        if (
            sourceRefs.size > EvaluationBounds.MAX_SOURCE_REFS ||
            sourceRefs.distinct().size != sourceRefs.size ||
            sourceRefs != sourceRefs.sorted() ||
            sourceRefs.any { !SOURCE_REF.matches(it) }
        ) {
            throw DisclosureGrpcProtocolException()
        }
        if (
            response.eventsCount > EvaluationBounds.MAX_DISCLOSURE_EVENTS ||
            response.warningsCount > EvaluationBounds.MAX_WARNINGS
        ) {
            throw DisclosureGrpcProtocolException()
        }
        val eventIdentities =
            response.eventsList.map { event ->
                val occurredOn =
                    try {
                        LocalDate.parse(event.occurredOn)
                    } catch (_: DateTimeException) {
                        throw DisclosureGrpcProtocolException()
                    }
                if (
                    !EVENT_CODE.matches(event.eventCode) ||
                    !RECEIPT_NO.matches(event.receiptNo) ||
                    occurredOn.isBefore(expectedWindowFrom) ||
                    occurredOn.isAfter(expectedAsOf)
                ) {
                    throw DisclosureGrpcProtocolException()
                }
                Triple(event.eventCode, event.receiptNo, event.occurredOn)
            }
        if (eventIdentities.distinct().size != eventIdentities.size) {
            throw DisclosureGrpcProtocolException()
        }
        response.warningsList.forEach { warning ->
            if (
                warning.code.isBlank() ||
                warning.code.length > EvaluationBounds.MAX_ID_OR_CODE_CHARS ||
                warning.message.isBlank() ||
                warning.message.length > EvaluationBounds.MAX_SAFE_MESSAGE_CHARS
            ) {
                throw DisclosureGrpcProtocolException()
            }
        }
        val observedAt =
            try {
                Instant.parse(response.observedAt)
            } catch (_: DateTimeException) {
                throw DisclosureGrpcProtocolException()
            }
        if (!response.score.isFinite()) {
            throw DisclosureGrpcProtocolException()
        }
        val score = BigDecimal.valueOf(response.score)
        if (score < BigDecimal.ZERO || score > BigDecimal.ONE) {
            throw DisclosureGrpcProtocolException()
        }
        if (!response.complete) {
            return MetricCell.Incomplete(MetricIssueCode.SOURCE_INCOMPLETE)
        }
        if (sourceRefs.isEmpty()) {
            throw DisclosureGrpcProtocolException()
        }
        val events =
            response.eventsList
                .map { it.eventCode }
                .distinct()
                .sorted()
                .map { eventCode ->
                    DisclosureEventEvidence(
                        eventCode = eventCode,
                        state = "ACTIVE",
                    )
                }
        val sourceRef =
            CanonicalJson.sha256(
                CanonicalJson.encode(
                    mapOf(
                        "mappingVersion" to response.mappingVersion,
                        "sourceRefs" to sourceRefs,
                    ),
                ),
            )
        return MetricCell.Available(
            value =
                DisclosureRiskSnapshot(
                    score = score,
                    mappingVersion = response.mappingVersion,
                    completeness = "COMPLETE",
                    events = events,
                    warnings =
                        response.warningsList
                            .map { it.code }
                            .distinct()
                            .sorted(),
                    sourceRefs = sourceRefs,
                ),
            observedAt = observedAt,
            retrievedAt = maxInstant(retrievedAt, observedAt),
            freshUntil = observedAt.plus(Duration.ofHours(24)),
            source = MetricSource.OPENDART,
            sourceRef = sourceRef,
            sourceVersion = response.mappingVersion,
        )
    }

    private fun validateEnvelope(
        response: GetDisclosureEventsResponse,
        expectedSymbol: String,
        expectedAsOf: LocalDate,
        expectedWindowFrom: LocalDate,
    ) {
        if (
            response.serializedSize > properties.responseMaxBytes ||
            response.symbol != expectedSymbol ||
            (response.corpCode.isNotEmpty() && !CORP_CODE.matches(response.corpCode)) ||
            (response.complete && !CORP_CODE.matches(response.corpCode)) ||
            response.asOf != expectedAsOf.toString() ||
            response.windowFrom != expectedWindowFrom.toString() ||
            response.windowTo != expectedAsOf.toString() ||
            response.mappingVersion.isBlank() ||
            response.mappingVersion.length > EvaluationBounds.MAX_ID_OR_CODE_CHARS
        ) {
            throw DisclosureGrpcProtocolException()
        }
    }

    private fun mapTransportFailure(exception: StatusRuntimeException): MetricCell<DisclosureRiskSnapshot> =
        when (exception.status.code) {
            Status.Code.UNAVAILABLE,
            Status.Code.DEADLINE_EXCEEDED,
            -> MetricCell.Error(MetricIssueCode.DISCLOSURE_UNAVAILABLE)

            else -> throw DisclosureGrpcProtocolException()
        }

    private fun effectiveDeadlineMillis(
        evaluationAsOf: Instant,
        callNow: Instant,
    ): Long {
        if (callNow.isBefore(evaluationAsOf)) {
            throw DisclosureGrpcProtocolException()
        }
        val elapsed = Duration.between(evaluationAsOf, callNow)
        val remainingNanos =
            Duration
                .ofMillis(properties.totalEvaluationDeadlineMillis)
                .minus(elapsed)
                .toNanos()
        if (remainingNanos <= 0) {
            return 0
        }
        val remainingMillis = max(1, remainingNanos / 1_000_000)
        return min(
            properties.hardDeadlineMillis,
            min(properties.sourceDeadlineMillis, remainingMillis),
        )
    }

    fun isShutdown(): Boolean = channel.isShutdown

    @PreDestroy
    override fun close() {
        channel.shutdownNow()
    }

    private fun maxInstant(
        left: Instant,
        right: Instant,
    ): Instant = if (left.isAfter(right)) left else right

    private fun authHeaders(): Metadata {
        val headers = Metadata()
        // plaintext loopback v1에서 business RPC 호출자를 같은 배포 단위의 Spring app으로 한 번 더 결속한다.
        headers.put(AUTH_HEADER, properties.sharedSecret)
        return headers
    }

    private companion object {
        val log = LoggerFactory.getLogger(GrpcDisclosureRiskAdapter::class.java)
        val AUTH_HEADER: Metadata.Key<String> =
            Metadata.Key.of("x-decision-grpc-auth", Metadata.ASCII_STRING_MARSHALLER)
        const val TRACE_ID_MDC_KEY = "trace_id"
        const val SPAN_ID_MDC_KEY = "span_id"
        const val TRACE_UNAVAILABLE = "unavailable"
        const val RPC_METHOD = "GetDisclosureEvents"
        const val RESULT_AVAILABLE = "AVAILABLE"
        const val RESULT_TYPED_UNAVAILABLE = "TYPED_UNAVAILABLE"
        const val RESULT_TECHNICAL_ERROR = "TECHNICAL_ERROR"
        val SEOUL: ZoneId = ZoneId.of("Asia/Seoul")
        val SOURCE_REF = Regex(EvaluationBounds.SANITIZED_SHA256_PATTERN)
        val EVENT_CODE = Regex("""[A-Za-z0-9._:-]{1,128}""")
        val RECEIPT_NO = Regex("""[0-9]{14}""")
        val CORP_CODE = Regex("""[0-9]{8}""")
        const val DISCLOSURE_LOOKBACK_DAYS = 365L
    }
}
