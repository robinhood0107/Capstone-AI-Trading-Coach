package com.capstone.decision.application.risk.crossmarket.v2

import com.capstone.decision.application.risk.port.EvaluationSourceRequest
import com.capstone.decision.domain.risk.EvaluationAction
import com.capstone.decision.domain.risk.EvaluationResult
import java.math.BigDecimal

data class CrossMarketOverlayConfig(
    val mode: CrossMarketRuntimeMode = CrossMarketRuntimeMode.OFF,
    val thresholdPercentile: BigDecimal? = null,
    val thresholdArtifactHash: String? = null,
    val configHash: String? = null,
) {
    init {
        require(mode != CrossMarketRuntimeMode.ENFORCED) { "MODE_NOT_APPROVED" }
        require(thresholdPercentile == null || thresholdPercentile in APPROVED_THRESHOLDS)
        require(thresholdArtifactHash == null || SHA256.matches(thresholdArtifactHash))
        require(configHash == null || SHA256.matches(configHash))
    }

    val hasFrozenThreshold: Boolean
        get() = thresholdPercentile != null && thresholdArtifactHash != null && configHash != null
}

enum class CrossMarketOverlayStatus {
    NOT_EVALUATED,
    OBSERVED,
    WARNED,
    UNAVAILABLE,
    STALE,
    INELIGIBLE,
}

data class CrossMarketOverlayResult(
    val baseResult: EvaluationResult,
    val finalAction: EvaluationAction,
    val status: CrossMarketOverlayStatus,
    val semanticInputHash: String?,
) {
    init {
        require(finalAction != EvaluationAction.HOLD && finalAction != EvaluationAction.BLOCK || finalAction == baseResult.action) {
            "P1 cross-market overlay cannot add HOLD or BLOCK authority."
        }
    }
}

/**
 * 기존 exact-14 결과 뒤에서만 평가하는 P1 overlay다. v1 violation/warning 배열과 decision hash를
 * 재작성하지 않으며 WARN_ONLY에서 fresh NEW_BUY trigger의 ALLOW -> WARN만 허용한다.
 */
class CrossMarketRiskOverlay(
    private val port: CrossMarketRiskPort,
    private val config: CrossMarketOverlayConfig,
) {
    fun evaluate(
        request: EvaluationSourceRequest,
        baseResult: EvaluationResult,
    ): CrossMarketOverlayResult {
        if (config.mode == CrossMarketRuntimeMode.OFF) {
            return result(baseResult, baseResult.action, CrossMarketOverlayStatus.NOT_EVALUATED)
        }
        if (!config.hasFrozenThreshold) {
            return result(baseResult, baseResult.action, CrossMarketOverlayStatus.UNAVAILABLE)
        }
        val input =
            try {
                port.load(request)
            } catch (_: CrossMarketInputUnavailableException) {
                return result(baseResult, baseResult.action, CrossMarketOverlayStatus.UNAVAILABLE)
            }
        val snapshot = input.snapshot
        val exposure = input.exposure
        if (snapshot.availableAt != exposure.availableAt) {
            return result(baseResult, baseResult.action, CrossMarketOverlayStatus.UNAVAILABLE)
        }
        if (snapshot.staleAt.isBefore(request.evaluationAsOf) || snapshot.availability == CrossMarketAvailability.STALE) {
            return result(baseResult, baseResult.action, CrossMarketOverlayStatus.STALE, snapshot.semanticInputHash)
        }
        if (
            snapshot.availability != CrossMarketAvailability.AVAILABLE ||
            snapshot.thresholdPercentile != config.thresholdPercentile ||
            snapshot.thresholdArtifactHash != config.thresholdArtifactHash ||
            snapshot.configHash != config.configHash ||
            snapshot.runtimeMode == CrossMarketRuntimeMode.ENFORCED ||
            (
                config.mode == CrossMarketRuntimeMode.WARN_ONLY &&
                    snapshot.runtimeMode != CrossMarketRuntimeMode.WARN_ONLY
            ) ||
            snapshot.symbol != request.orderIntent.symbol ||
            exposure.symbol != request.orderIntent.symbol
        ) {
            return result(baseResult, baseResult.action, CrossMarketOverlayStatus.UNAVAILABLE, snapshot.semanticInputHash)
        }
        val trigger = snapshot.score!! >= snapshot.thresholdPercentile!!
        val eligible =
            request.orderIntent.side == "BUY" &&
                exposure.classification == CrossMarketExposureClassification.NEW_BUY
        if (!eligible) {
            return result(baseResult, baseResult.action, CrossMarketOverlayStatus.INELIGIBLE, snapshot.semanticInputHash)
        }
        if (
            config.mode == CrossMarketRuntimeMode.WARN_ONLY &&
            trigger &&
            baseResult.action == EvaluationAction.ALLOW
        ) {
            return result(baseResult, EvaluationAction.WARN, CrossMarketOverlayStatus.WARNED, snapshot.semanticInputHash)
        }
        return result(baseResult, baseResult.action, CrossMarketOverlayStatus.OBSERVED, snapshot.semanticInputHash)
    }

    private fun result(
        base: EvaluationResult,
        action: EvaluationAction,
        status: CrossMarketOverlayStatus,
        hash: String? = null,
    ) = CrossMarketOverlayResult(base, action, status, hash)
}
