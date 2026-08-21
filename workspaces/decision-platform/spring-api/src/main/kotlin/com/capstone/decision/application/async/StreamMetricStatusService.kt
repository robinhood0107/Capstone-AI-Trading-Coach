package com.capstone.decision.application.async

import org.springframework.stereotype.Service

@Service
class StreamMetricStatusService(
    private val port: StreamMetricStatusPort,
) {
    fun read(
        actorUserId: String,
        securityVersion: Long,
    ): StreamMetricStatus? = port.read(actorUserId, securityVersion)
}
