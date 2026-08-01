package com.capstone.decision.application.risk.crossmarket

import com.capstone.decision.domain.risk.EvaluationBounds
import java.time.Instant

data class CrossMarketSnapshotReadRequest(
    val actorUserId: String,
    val ownerScopeHash: String,
    val configVersion: String,
    val evaluationAsOf: Instant,
) {
    init {
        require(actorUserId.isNotBlank() && actorUserId.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(SHA256.matches(ownerScopeHash))
        require(configVersion.isNotBlank() && configVersion.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
    }
}

data class CrossMarketRiskSnapshot(
    val logicalIdentityHash: String,
    val ownerScopeHash: String,
    val configVersion: String,
    val availability: String,
    val evidenceMode: String,
    val snapshotAvailableAt: Instant,
    val decisionAuthority: String,
    val orderAuthority: String,
    val validationStatus: String,
    val artifactHash: String,
    val canonicalPayloadJson: String,
) {
    init {
        require(SHA256.matches(logicalIdentityHash))
        require(SHA256.matches(ownerScopeHash))
        require(configVersion.isNotBlank() && configVersion.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(availability in setOf("AVAILABLE", "UNAVAILABLE"))
        require(evidenceMode in setOf("SYNTHETIC_FIXTURE", "MANUAL_EOD", "STORED_SNAPSHOT"))
        require(decisionAuthority in setOf("NONE", "NEW_BUY_ALLOW_TO_WARN_ONLY"))
        require(orderAuthority == "NONE")
        require(validationStatus in setOf("UNVALIDATED", "VALIDATED"))
        require(SHA256.matches(artifactHash))
        require(canonicalPayloadJson.toByteArray(Charsets.UTF_8).size <= MAX_PAYLOAD_BYTES)
    }
}

enum class CrossMarketSnapshotUnavailableReason {
    MISSING,
    DUPLICATE,
    FUTURE,
    SOURCE_UNAVAILABLE,
}

sealed interface CrossMarketSnapshotReadResult {
    data class Available(
        val snapshot: CrossMarketRiskSnapshot,
    ) : CrossMarketSnapshotReadResult

    data class Unavailable(
        val reason: CrossMarketSnapshotUnavailableReason,
    ) : CrossMarketSnapshotReadResult
}

/**
 * owner-scoped latest stored snapshot만 읽는다. 이 port에는 materialization, threshold 선택,
 * RiskDecision 또는 주문 authority가 없다.
 */
fun interface CrossMarketSnapshotReadPort {
    fun load(request: CrossMarketSnapshotReadRequest): CrossMarketSnapshotReadResult
}

private val SHA256 = Regex(EvaluationBounds.SANITIZED_SHA256_PATTERN)
private const val MAX_PAYLOAD_BYTES = 262_144
