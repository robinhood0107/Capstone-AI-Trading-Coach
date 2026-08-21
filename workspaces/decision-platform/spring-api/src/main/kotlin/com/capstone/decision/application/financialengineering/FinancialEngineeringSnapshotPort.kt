package com.capstone.decision.application.financialengineering

import tools.jackson.databind.JsonNode
import java.time.Instant
import java.time.LocalDate

data class StoredFinancialEngineeringSnapshot(
    val snapshotId: String,
    val schemaVersion: Int,
    val symbol: String,
    val sessionDate: LocalDate,
    val asOf: Instant,
    val availableAt: Instant,
    val sourceManifestHash: String,
    val configHash: String,
    val numericPayloadHash: String,
    val artifactHash: String,
    val availability: String,
    val quality: String,
    val staleness: String,
    val numericPayload: JsonNode,
    val reportArtifactHash: String,
)

interface FinancialEngineeringSnapshotPort {
    /** evaluationAsOf 이후 availableAt은 DB 함수와 adapter 양쪽에서 거부한다. */
    fun loadLatest(
        symbol: String,
        evaluationAsOf: Instant,
    ): StoredFinancialEngineeringSnapshot?
}
