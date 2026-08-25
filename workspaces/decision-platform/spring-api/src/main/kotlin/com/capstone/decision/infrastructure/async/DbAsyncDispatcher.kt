package com.capstone.decision.infrastructure.async

import com.capstone.decision.contract.asyncworker.v1.AsyncWorkOutcome
import org.slf4j.LoggerFactory
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.security.MessageDigest
import java.util.HexFormat

@Component
@ConditionalOnProperty(name = ["app.async.adapter"], havingValue = "db", matchIfMissing = true)
@ConditionalOnProperty(name = ["app.async.worker.enabled"], havingValue = "true")
class DbAsyncDispatcher(
    private val outboxQueue: DbAsyncOutboxQueue,
    private val workerQueue: DbAsyncWorkerQueue,
    private val workerClient: GrpcAsyncWorkerClient,
    private val objectMapper: ObjectMapper,
    private val properties: AsyncProperties,
) {
    fun poll() {
        try {
            outboxQueue.quarantineUnknown(properties.claimPageSize)
            outboxQueue.claim(WORKER_ID, properties.claimPageSize).forEach(::dispatch)
        } catch (_: RuntimeException) {
            logger.error("S7 DB async poll failed closed.")
        }
    }

    private fun dispatch(event: ClaimedOutboxEvent) {
        var job: ClaimedAsyncJob? = null
        try {
            val payload = validatePayload(event)
            val jobId = payload.path("jobId").stringValue()
            val jobType = EVENT_JOB_TYPES.getValue(event.eventType)
            val payloadHash = "sha256:${sha256(event.payloadJson.toByteArray(Charsets.UTF_8))}"
            require(outboxQueue.bindPayloadHash(event, payloadHash))
            job = workerQueue.claimByEvent(WORKER_ID, event, payloadHash)
            if (job == null) {
                if (workerQueue.resolveCompleted(event, payloadHash)) {
                    require(outboxQueue.complete(event))
                    return
                }
                error("event-bound async job is unavailable")
            }
            require(job.jobId == event.aggregateId && job.jobType == jobType)
            require(objectMapper.readTree(job.payloadJson) == payload)
            val result = workerClient.process(event, job, jobId, jobType)
            when (result.outcome) {
                AsyncWorkOutcome.ASYNC_WORK_COMPLETED,
                AsyncWorkOutcome.ASYNC_WORK_DUPLICATE,
                -> require(outboxQueue.complete(event))
                AsyncWorkOutcome.ASYNC_WORK_FAILED,
                -> outboxQueue.fail(event, result.failureCode ?: "WORKER_REJECTED", "WORKER_FAILURE")
                AsyncWorkOutcome.ASYNC_WORK_NEEDS_REVIEW,
                -> outboxQueue.quarantine(event, result.failureCode ?: "WORKER_REJECTED")
                else -> error("unsupported async worker outcome")
            }
        } catch (_: AsyncPayloadContractException) {
            outboxQueue.quarantine(event, "INVALID_EVENT_PAYLOAD")
        } catch (_: RuntimeException) {
            if (job != null) workerQueue.fail(job, "WORKER_RPC_FAILED", "RETRYABLE_TRANSIENT")
            outboxQueue.fail(event, "WORKER_RPC_FAILED", "RETRYABLE_TRANSIENT")
        }
    }

    private fun validatePayload(event: ClaimedOutboxEvent): JsonNode {
        if (event.schemaVersion != 1 || event.eventType !in EVENT_JOB_TYPES) throw AsyncPayloadContractException()
        val bytes = event.payloadJson.toByteArray(Charsets.UTF_8)
        if (bytes.size > 32_768) throw AsyncPayloadContractException()
        val root = runCatching { objectMapper.readTree(bytes) }.getOrElse { throw AsyncPayloadContractException() }
        val fields = root.properties().asSequence().toList()
        if (!root.isObject ||
            fields.size > 64 ||
            fields.any { (key, value) ->
                key !in ALLOWED_KEYS || !value.isString || value.stringValue().toByteArray(Charsets.UTF_8).size > 2_048
            }
        ) {
            throw AsyncPayloadContractException()
        }
        val jobId = root.path("jobId").takeIf(JsonNode::isString)?.stringValue()
        if (jobId == null || !JOB_ID.matches(jobId) || jobId != event.aggregateId) throw AsyncPayloadContractException()
        return root
    }

    private fun sha256(value: ByteArray): String = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value))

    private companion object {
        const val WORKER_ID = "spring-db-dispatcher"
        val JOB_ID = Regex("^job_[A-Za-z0-9_-]{8,96}$")
        val ALLOWED_KEYS =
            setOf(
                "jobId",
                "ownerRef",
                "sourceId",
                "sourceRevisionId",
                "importTicketId",
                "profileId",
                "artifactId",
                "runId",
                "contentHash",
                "resultRef",
                "replayOf",
            )
        val EVENT_JOB_TYPES =
            mapOf(
                "rag.index-requested.v1" to "RAG_INDEX",
                "artifact.ingest-requested.v1" to "ARTIFACT_INGEST",
                "model.eval-requested.v1" to "MODEL_EVAL",
            )
        val logger: org.slf4j.Logger = LoggerFactory.getLogger(DbAsyncDispatcher::class.java)
    }
}

class AsyncPayloadContractException : RuntimeException()
