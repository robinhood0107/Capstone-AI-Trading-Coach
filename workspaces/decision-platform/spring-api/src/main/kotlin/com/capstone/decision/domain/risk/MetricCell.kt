package com.capstone.decision.domain.risk

import java.time.Instant

// availability를 nullable 값과 분리해 missing/stale/error가 PASS로 해석될 수 없게 한다.
sealed interface MetricCell<out T> {
    data class Available<T>(
        val value: T,
        val observedAt: Instant,
        val retrievedAt: Instant,
        val freshUntil: Instant,
        val source: MetricSource,
        val sourceRef: String,
        val sourceVersion: String,
    ) : MetricCell<T> {
        init {
            require(!retrievedAt.isBefore(observedAt)) { "Metric retrieval precedes observation." }
            require(!freshUntil.isBefore(observedAt)) { "Metric freshness precedes observation." }
            require(SOURCE_REF.matches(sourceRef)) { "Metric sourceRef must be sanitized SHA-256 hex." }
            require(sourceVersion.isNotBlank() && sourceVersion.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS) {
                "Metric source version is invalid."
            }
        }
    }

    data class Missing(
        val reason: MetricIssueCode,
    ) : MetricCell<Nothing>

    data class Stale(
        val observedAt: Instant,
        val freshUntil: Instant,
        val reason: MetricIssueCode,
    ) : MetricCell<Nothing>

    data class Error(
        val reason: MetricIssueCode,
    ) : MetricCell<Nothing>

    data class Incomplete(
        val reason: MetricIssueCode,
    ) : MetricCell<Nothing>

    data class Abstained(
        val reason: MetricIssueCode,
    ) : MetricCell<Nothing>

    data class NotApplicable(
        val reason: MetricIssueCode,
    ) : MetricCell<Nothing>

    private companion object {
        val SOURCE_REF = Regex(EvaluationBounds.SANITIZED_SHA256_PATTERN)
    }
}
