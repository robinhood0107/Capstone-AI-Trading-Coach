package com.capstone.decision.infrastructure.async

import com.capstone.decision.contract.asyncworker.v1.AsyncTransport
import com.capstone.decision.contract.asyncworker.v1.AsyncWorkOutcome
import com.capstone.decision.contract.asyncworker.v1.AsyncWorkRequest
import com.capstone.decision.contract.asyncworker.v1.AsyncWorkerServiceGrpc
import io.grpc.ManagedChannel
import io.grpc.Metadata
import io.grpc.netty.shaded.io.grpc.netty.NettyChannelBuilder
import io.grpc.stub.MetadataUtils
import jakarta.annotation.PreDestroy
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.HexFormat
import java.util.concurrent.TimeUnit

data class AsyncWorkerResult(
    val outcome: AsyncWorkOutcome,
    val failureCode: String?,
)

@Component
@ConditionalOnProperty(name = ["app.async.adapter"], havingValue = "db", matchIfMissing = true)
@ConditionalOnProperty(name = ["app.async.worker.enabled"], havingValue = "true")
class GrpcAsyncWorkerClient(
    private val properties: AsyncWorkerProperties,
) {
    private val channel: ManagedChannel =
        NettyChannelBuilder
            .forTarget(properties.grpcTarget)
            .usePlaintext()
            .maxInboundMessageSize(properties.responseMaxBytes)
            .build()
    private val stub: AsyncWorkerServiceGrpc.AsyncWorkerServiceBlockingStub

    init {
        val metadata = Metadata()
        metadata.put(AUTH_KEY, properties.grpcSharedSecret)
        stub = AsyncWorkerServiceGrpc.newBlockingStub(channel).withInterceptors(MetadataUtils.newAttachHeadersInterceptor(metadata))
    }

    fun process(
        event: ClaimedOutboxEvent,
        job: ClaimedAsyncJob?,
        jobId: String,
        jobType: String,
    ): AsyncWorkerResult {
        val payload = event.payloadJson.toByteArray(StandardCharsets.UTF_8)
        require(payload.size <= properties.requestMaxBytes)
        val request =
            AsyncWorkRequest
                .newBuilder()
                .setEventId(event.eventId)
                .setEventType(event.eventType)
                .setSchemaVersion(event.schemaVersion)
                .setPayloadHash("sha256:${sha256(payload)}")
                .setJobId(jobId)
                .setJobType(jobType)
                .setPayloadJson(
                    com.google.protobuf.ByteString
                        .copyFrom(payload),
                ).setClaimToken(job?.claimToken?.toString().orEmpty())
                .setTransport(AsyncTransport.ASYNC_TRANSPORT_DB)
                .setAttempt(event.attempt)
                .build()
        require(request.serializedSize <= properties.requestMaxBytes)
        val response =
            stub
                .withDeadlineAfter(properties.grpcDeadline.toMillis(), TimeUnit.MILLISECONDS)
                .process(request)
        require(response.jobId == jobId)
        require(response.outcome != AsyncWorkOutcome.ASYNC_WORK_OUTCOME_UNSPECIFIED)
        val failureCode = response.failureCode.takeIf(String::isNotEmpty)
        if (failureCode != null) require(FAILURE_CODE.matches(failureCode))
        return AsyncWorkerResult(response.outcome, failureCode)
    }

    @PreDestroy
    fun close() {
        channel.shutdown()
        if (!channel.awaitTermination(2, TimeUnit.SECONDS)) channel.shutdownNow()
    }

    private fun sha256(value: ByteArray): String = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value))

    private companion object {
        val AUTH_KEY: Metadata.Key<String> =
            Metadata.Key.of("x-async-worker-auth", Metadata.ASCII_STRING_MARSHALLER)
        val FAILURE_CODE = Regex("^[A-Z][A-Z0-9_]{2,63}$")
    }
}
