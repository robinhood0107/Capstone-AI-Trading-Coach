package com.capstone.decision.application.async

import java.time.Instant

enum class AsyncJobType {
    RAG_INDEX,
    ARTIFACT_INGEST,
    MODEL_EVAL,
}

enum class AsyncJobStatus {
    REQUESTED,
    RUNNING,
    COMPLETED,
    FAILED,
    NEEDS_REVIEW,
}

data class AsyncJobError(
    val code: String,
    val errorClass: String,
)

data class AsyncJobView(
    val jobId: String,
    val type: AsyncJobType,
    val status: AsyncJobStatus,
    val requestedAt: Instant,
    val startedAt: Instant?,
    val completedAt: Instant?,
    val sourceId: String?,
    val artifactId: String?,
    val resultRef: String?,
    val error: AsyncJobError?,
)

data class AsyncJobPageQuery(
    val actorUserId: String,
    val securityVersion: Long,
    val status: AsyncJobStatus?,
    val type: AsyncJobType?,
    val beforeRequestedAt: Instant?,
    val beforeJobId: String?,
    val size: Int,
)

data class AsyncJobPage(
    val items: List<AsyncJobView>,
    val nextCursor: String?,
)

data class AsyncJobRequest(
    val type: AsyncJobType,
    val requestedBy: String,
    val references: Map<String, String>,
)

data class AcceptedAsyncJob(
    val jobId: String,
    val eventId: String,
)

interface AsyncJobStatusPort {
    fun find(
        actorUserId: String,
        securityVersion: Long,
        jobId: String,
    ): AsyncJobView?

    fun list(query: AsyncJobPageQuery): List<AsyncJobView>
}

interface AsyncPipelinePort {
    fun request(command: AsyncJobRequest): AcceptedAsyncJob
}

class AsyncJobStatusUnavailableException(
    cause: Throwable? = null,
) : RuntimeException("Async job status is unavailable.", cause)

class AsyncJobRequestConflictException : RuntimeException("Async job request conflicted.")
