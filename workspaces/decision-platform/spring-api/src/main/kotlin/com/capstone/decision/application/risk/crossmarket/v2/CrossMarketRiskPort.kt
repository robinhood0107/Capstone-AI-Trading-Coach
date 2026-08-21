package com.capstone.decision.application.risk.crossmarket.v2

import com.capstone.decision.application.risk.port.EvaluationSourceRequest
import com.capstone.decision.domain.risk.EvaluationBounds
import java.math.BigDecimal
import java.time.Instant
import java.util.UUID

enum class CrossMarketEvidenceMode {
    SYNTHETIC_FIXTURE,
    HISTORICAL_REPLAY,
    PROSPECTIVE_SHADOW,
    MANUAL_EOD,
}

enum class CrossMarketStorageMode {
    ARTIFACT_ONLY,
    STORED_SNAPSHOT,
}

enum class CrossMarketRuntimeMode {
    OFF,
    SHADOW,
    WARN_ONLY,
    ENFORCED,
}

enum class CrossMarketAvailability {
    AVAILABLE,
    UNAVAILABLE,
    STALE,
}

enum class CrossMarketExposureClassification {
    NEW_BUY,
    INCREASE_BUY,
    SELL,
    REDUCE,
    LIQUIDATION,
    EXISTING_POSITION,
    UNCLASSIFIED,
}

data class CrossMarketRiskSnapshot(
    val snapshotId: UUID,
    val ownerScopeHash: String,
    val symbol: String,
    val availableAt: Instant,
    val staleAt: Instant,
    val evidenceMode: CrossMarketEvidenceMode,
    val storageMode: CrossMarketStorageMode,
    val runtimeMode: CrossMarketRuntimeMode,
    val availability: CrossMarketAvailability,
    val score: BigDecimal?,
    val thresholdPercentile: BigDecimal?,
    val thresholdArtifactHash: String?,
    val configHash: String,
    val semanticInputHash: String,
    val artifactHash: String,
) {
    init {
        require(SHA256.matches(ownerScopeHash))
        require(symbol.matches(SYMBOL))
        require(!staleAt.isBefore(availableAt))
        require(score == null || score in BigDecimal.ZERO..BigDecimal("100"))
        require(thresholdPercentile == null || thresholdPercentile.isApprovedThreshold())
        require(thresholdArtifactHash == null || SHA256.matches(thresholdArtifactHash))
        require(SHA256.matches(configHash))
        require(SHA256.matches(semanticInputHash))
        require(SHA256.matches(artifactHash))
        require(
            availability != CrossMarketAvailability.AVAILABLE ||
                (score != null && thresholdPercentile != null && thresholdArtifactHash != null),
        )
    }
}

data class CrossMarketExposure(
    val symbol: String,
    val classification: CrossMarketExposureClassification,
    val availableAt: Instant,
    val catalogHash: String,
) {
    init {
        require(symbol.matches(SYMBOL))
        require(SHA256.matches(catalogHash))
    }
}

data class CrossMarketDecisionInput(
    val snapshot: CrossMarketRiskSnapshot,
    val exposure: CrossMarketExposure,
)

interface CrossMarketRiskPort {
    fun load(request: EvaluationSourceRequest): CrossMarketDecisionInput
}

class CrossMarketInputUnavailableException(
    val reason: String,
) : RuntimeException(reason) {
    init {
        require(reason.isNotBlank() && reason.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
    }
}

internal val SHA256 = Regex(EvaluationBounds.SANITIZED_SHA256_PATTERN)
internal val SYMBOL = Regex("^[0-9A-Z./-]{1,32}$")
internal val APPROVED_THRESHOLDS = setOf(BigDecimal("95"), BigDecimal("97.5"), BigDecimal("99"))

internal fun BigDecimal.isApprovedThreshold() = APPROVED_THRESHOLDS.any { compareTo(it) == 0 }
