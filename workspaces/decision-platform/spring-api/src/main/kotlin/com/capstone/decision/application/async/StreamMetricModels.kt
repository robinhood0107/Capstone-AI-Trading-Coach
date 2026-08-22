package com.capstone.decision.application.async

import java.math.BigDecimal
import java.time.Instant

enum class StreamMetricComponentStatus {
    OK,
    EMPTY,
    UNAVAILABLE,
}

enum class PipelineHealth {
    OK,
    DEGRADED,
    UNAVAILABLE,
}

data class StreamMetricComponent(
    val status: StreamMetricComponentStatus,
    val observedAt: Instant?,
)

data class DecisionDistribution(
    val allow: Long,
    val warn: Long,
    val hold: Long,
    val block: Long,
)

data class StreamMetricStatus(
    val lastUpdatedAt: Instant?,
    val pipelineHealth: PipelineHealth,
    val signalStaleRatio: BigDecimal?,
    val decisionDistribution: DecisionDistribution,
    val failedJobCount: Long,
    val dlqEventCount: Long,
    val decisionComponent: StreamMetricComponent,
    val signalComponent: StreamMetricComponent,
    val failedJobComponent: StreamMetricComponent,
    val dlqComponent: StreamMetricComponent,
)

interface StreamMetricStatusPort {
    fun read(
        actorUserId: String,
        securityVersion: Long,
    ): StreamMetricStatus?
}

class StreamMetricUnavailableException(
    cause: Throwable? = null,
) : RuntimeException("Stream metrics are unavailable.", cause)
