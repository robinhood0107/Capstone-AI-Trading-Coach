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

    fun latestArtifactRun(
        actorUserId: String,
        securityVersion: Long,
        kind: DashboardArtifactKind,
    ): LatestArtifactRunView?

    fun risk(
        actorUserId: String,
        securityVersion: Long,
        decisionId: String,
    ): JsonNode?

    fun latestRisk(
        actorUserId: String,
        securityVersion: Long,
    ): RecentRiskResultView?

    fun recentRisks(
        actorUserId: String,
        securityVersion: Long,
    ): List<RecentRiskResultView>

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

    fun latestArtifactRun(
        actorUserId: String,
        securityVersion: Long,
        kind: DashboardArtifactKind,
    ): LatestArtifactRunView? = port.latestArtifactRun(actorUserId, securityVersion, kind)

    fun risk(
        actorUserId: String,
        securityVersion: Long,
        decisionId: String,
    ): JsonNode? = port.risk(actorUserId, securityVersion, decisionId)

    fun latestRisk(
        actorUserId: String,
        securityVersion: Long,
    ): RecentRiskResultView? = port.latestRisk(actorUserId, securityVersion)

    fun recentRisks(
        actorUserId: String,
        securityVersion: Long,
    ): List<RecentRiskResultView> = port.recentRisks(actorUserId, securityVersion)

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

data class LatestArtifactRunView(
    val runId: String,
    val fixtureClass: String,
    val asOf: java.time.Instant,
)

data class RecentRiskResultView(
    val decisionId: String,
    val action: String,
    val symbol: String,
    val asOf: Instant,
    val validUntil: Instant,
)

class DashboardUnavailableException(
    cause: Throwable? = null,
) : RuntimeException("Dashboard projection is unavailable.", cause)
