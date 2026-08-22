package com.capstone.decision.application.dashboard

import tools.jackson.databind.JsonNode
import java.time.Instant

enum class DashboardArtifactKind {
    MODEL_EVALUATION,
    BACKTEST,
}

data class ArtifactIngestStatusView(
    val artifactId: String,
    val fileName: String,
    val producer: String,
    val runId: String,
    val fileHash: String,
    val schemaVersion: String,
    val status: String,
    val lastIngestedAt: Instant?,
    val duplicate: Boolean,
)

interface DashboardViewPort {
    fun artifact(
        actorUserId: String,
        securityVersion: Long,
        kind: DashboardArtifactKind,
        runId: String,
    ): JsonNode?

    fun risk(
        actorUserId: String,
        securityVersion: Long,
        decisionId: String,
    ): JsonNode?

    fun rag(
        actorUserId: String,
        securityVersion: Long,
        answerId: String,
    ): JsonNode?

    fun artifactStatuses(
        actorUserId: String,
        securityVersion: Long,
    ): List<ArtifactIngestStatusView>?
}

class DashboardViewService(
    private val port: DashboardViewPort,
) {
    fun artifact(
        actorUserId: String,
        securityVersion: Long,
        kind: DashboardArtifactKind,
        runId: String,
    ): JsonNode? = port.artifact(actorUserId, securityVersion, kind, runId)

    fun risk(
        actorUserId: String,
        securityVersion: Long,
        decisionId: String,
    ): JsonNode? = port.risk(actorUserId, securityVersion, decisionId)

    fun rag(
        actorUserId: String,
        securityVersion: Long,
        answerId: String,
    ): JsonNode? = port.rag(actorUserId, securityVersion, answerId)

    fun artifactStatuses(
        actorUserId: String,
        securityVersion: Long,
    ): List<ArtifactIngestStatusView>? = port.artifactStatuses(actorUserId, securityVersion)
}

class DashboardUnavailableException(
    cause: Throwable? = null,
) : RuntimeException("Dashboard projection is unavailable.", cause)
