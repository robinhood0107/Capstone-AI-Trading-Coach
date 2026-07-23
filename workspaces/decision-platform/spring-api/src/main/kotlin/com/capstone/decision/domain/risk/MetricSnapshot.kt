package com.capstone.decision.domain.risk

import com.capstone.decision.domain.principle.PrincipleMode
import java.math.BigDecimal
import java.time.Instant

data class OrderIntentSnapshot(
    val symbol: String,
    val side: String,
    val orderType: String,
    val quantity: Long,
    val limitPrice: BigDecimal?,
) {
    init {
        require(symbol.isNotBlank() && symbol.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(side in setOf("BUY", "SELL"))
        require(orderType in setOf("MARKET", "LIMIT"))
        require(quantity > 0)
        require((orderType == "LIMIT") == (limitPrice != null))
        require(limitPrice == null || limitPrice.signum() > 0)
    }
}

data class PrincipleSnapshotIdentity(
    val principleId: String,
    val principleVersionId: String,
    val version: Int,
    val mode: PrincipleMode,
    val rulesHash: String,
) {
    init {
        require(principleId.isNotBlank() && principleId.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(principleVersionId.isNotBlank() && principleVersionId.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(version > 0)
        require(SHA256.matches(rulesHash))
    }
}

data class PortfolioSnapshotIdentity(
    val source: PortfolioSource,
    val revision: String,
    val ownerScopeHash: String,
    val positionCount: Int,
) {
    init {
        require(revision.isNotBlank() && revision.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(SHA256.matches(ownerScopeHash))
        require(positionCount in 0..EvaluationBounds.MAX_POSITIONS)
    }
}

data class OptionalComponentEvidence(
    val componentId: String,
    val available: Boolean,
    val reasonCode: String?,
    val evidenceVersion: String? = null,
    val completeness: String? = null,
    val sourceRefs: List<String> = emptyList(),
) {
    init {
        require(componentId.isNotBlank() && componentId.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(
            reasonCode == null ||
                (reasonCode.isNotBlank() && reasonCode.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS),
        )
        require(available == (reasonCode == null))
        require(
            evidenceVersion == null ||
                (evidenceVersion.isNotBlank() && evidenceVersion.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS),
        )
        require(
            completeness == null ||
                (completeness.isNotBlank() && completeness.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS),
        )
        require(sourceRefs.size <= EvaluationBounds.MAX_SOURCE_REFS)
        require(sourceRefs.distinct().size == sourceRefs.size)
        require(sourceRefs.all(SHA256::matches))
    }
}

data class DisclosureEvidenceIdentity(
    val completeness: String,
    val mappingVersion: String,
    val sourceRefs: List<String>,
) {
    init {
        require(completeness in setOf("COMPLETE", "EMPTY"))
        require(mappingVersion.isNotBlank() && mappingVersion.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(sourceRefs.size <= EvaluationBounds.MAX_SOURCE_REFS)
        require(sourceRefs.distinct().size == sourceRefs.size)
        require(sourceRefs.all(SHA256::matches))
    }
}

// application assembler가 만든 immutable 평가 입력이다. raw 계좌번호·token·provider body는 포함하지 않는다.
data class MetricSnapshot(
    val snapshotSchemaVersion: String,
    val evaluationId: String,
    val evaluationAsOf: Instant,
    val retrievedAt: Instant,
    val actorUserId: String,
    val principle: PrincipleSnapshotIdentity,
    val systemRuleCatalogVersion: Int,
    val readinessPolicyVersion: String,
    val portfolio: PortfolioSnapshotIdentity,
    val orderIntent: OrderIntentSnapshot,
    val metrics: Map<MetricKey, MetricCell<MetricValue>>,
    val provenanceRefs: List<String>,
    val requestedOptionalComponents: List<String> = emptyList(),
    val observedOptionalComponentEvidence: List<OptionalComponentEvidence> = emptyList(),
    val disclosureEvidence: DisclosureEvidenceIdentity? = null,
) {
    init {
        require(snapshotSchemaVersion.isNotBlank() && snapshotSchemaVersion.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(evaluationId.isNotBlank() && evaluationId.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(actorUserId.isNotBlank() && actorUserId.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        require(systemRuleCatalogVersion > 0)
        require(
            readinessPolicyVersion.isNotBlank() &&
                readinessPolicyVersion.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS,
        )
        require(provenanceRefs.size <= EvaluationBounds.MAX_SOURCE_REFS)
        require(provenanceRefs.distinct().size == provenanceRefs.size)
        require(provenanceRefs.all(SHA256::matches))
        require(metrics.keys.size == metrics.size)
        require(requestedOptionalComponents.size <= EvaluationBounds.MAX_ABSTENTIONS)
        require(requestedOptionalComponents.distinct().size == requestedOptionalComponents.size)
        require(
            requestedOptionalComponents.all {
                it.isNotBlank() && it.length <= EvaluationBounds.MAX_ID_OR_CODE_CHARS
            },
        )
        require(observedOptionalComponentEvidence.size <= EvaluationBounds.MAX_ABSTENTIONS)
        require(
            observedOptionalComponentEvidence
                .map(OptionalComponentEvidence::componentId)
                .distinct()
                .size == observedOptionalComponentEvidence.size,
        )
    }

    fun metric(key: MetricKey): MetricCell<MetricValue> = metrics[key] ?: MetricCell.Missing(MetricIssueCode.SOURCE_MISSING)

    companion object {
        // 순수 단위 테스트 fixture만을 위한 deterministic 기본값이며 production assembler는 모든 identity를 명시한다.
        fun fixture(
            evaluationId: String = "eval_fixture",
            evaluationAsOf: Instant = Instant.parse("2030-01-02T03:04:05Z"),
            retrievedAt: Instant = evaluationAsOf,
            metrics: Map<MetricKey, MetricCell<MetricValue>> = emptyMap(),
            provenanceRefs: List<String> = emptyList(),
            requestedOptionalComponents: List<String> = emptyList(),
            observedOptionalComponentEvidence: List<OptionalComponentEvidence> = emptyList(),
            disclosureEvidence: DisclosureEvidenceIdentity? = null,
        ): MetricSnapshot =
            MetricSnapshot(
                snapshotSchemaVersion = "s2.2-metric-snapshot-v1",
                evaluationId = evaluationId,
                evaluationAsOf = evaluationAsOf,
                retrievedAt = retrievedAt,
                actorUserId = "usr_fixture",
                principle =
                    PrincipleSnapshotIdentity(
                        principleId = "prc_0123456789abcdef0123456789abcdef",
                        principleVersionId = "pvr_0123456789abcdef0123456789abcdef",
                        version = 1,
                        mode = PrincipleMode.GUIDE,
                        rulesHash = "1".repeat(64),
                    ),
                systemRuleCatalogVersion = 1,
                readinessPolicyVersion = "s2-2-readiness-v1",
                portfolio =
                    PortfolioSnapshotIdentity(
                        source = PortfolioSource.INTERNAL_PAPER,
                        revision = "paper-revision-1",
                        ownerScopeHash = "2".repeat(64),
                        positionCount = 0,
                    ),
                orderIntent =
                    OrderIntentSnapshot(
                        symbol = "005930",
                        side = "BUY",
                        orderType = "MARKET",
                        quantity = 1,
                        limitPrice = null,
                    ),
                metrics = metrics.toMap(),
                provenanceRefs = provenanceRefs.toList(),
                requestedOptionalComponents = requestedOptionalComponents.toList(),
                observedOptionalComponentEvidence = observedOptionalComponentEvidence.toList(),
                disclosureEvidence = disclosureEvidence,
            )
    }
}

// evaluator에는 AVAILABLE+fresh 판정을 통과한 exact metric만 전달한다.
class ReadyMetricSnapshot private constructor(
    val evaluationAsOf: Instant,
    private val metrics: Map<MetricKey, MetricCell.Available<MetricValue>>,
) {
    fun value(key: MetricKey): MetricValue =
        metrics[key]?.value
            ?: throw IllegalArgumentException("Ready metric is unavailable.")

    fun available(key: MetricKey): MetricCell.Available<MetricValue> =
        metrics[key] ?: throw IllegalArgumentException("Ready metric is unavailable.")

    companion object {
        fun of(
            evaluationAsOf: Instant,
            metrics: Map<MetricKey, MetricCell.Available<MetricValue>>,
        ): ReadyMetricSnapshot {
            require(metrics.isNotEmpty()) { "Ready snapshot must contain metrics." }
            return ReadyMetricSnapshot(evaluationAsOf, metrics.toMap())
        }

        internal fun single(
            evaluationAsOf: Instant,
            key: MetricKey,
            metric: MetricCell.Available<MetricValue>,
        ): ReadyMetricSnapshot = of(evaluationAsOf, mapOf(key to metric))
    }
}

private val SHA256 = Regex(EvaluationBounds.SANITIZED_SHA256_PATTERN)
