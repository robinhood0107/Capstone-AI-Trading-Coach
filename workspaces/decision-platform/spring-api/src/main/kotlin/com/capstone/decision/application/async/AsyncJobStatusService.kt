package com.capstone.decision.application.async

import org.springframework.stereotype.Service

@Service
class AsyncJobStatusService(
    private val statusPort: AsyncJobStatusPort,
) {
    fun get(
        actorUserId: String,
        securityVersion: Long,
        jobId: String,
    ): AsyncJobView? = statusPort.find(actorUserId, securityVersion, jobId)

    fun list(query: AsyncJobPageQuery): List<AsyncJobView> = statusPort.list(query)
}
