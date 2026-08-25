package com.capstone.decision.domain.risk

import java.math.BigDecimal
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Instant

// HASH-CANONICALIZATION-S22-V2의 제한된 JSON type만 허용하는 표준-library canonicalizer다.
object CanonicalJson {
    fun encode(value: Any?): String =
        when (value) {
            null -> "null"
            is String -> quote(value)
            is Boolean -> value.toString()
            is Byte, is Short, is Int, is Long -> value.toString()
            is BigDecimal -> quote(decimal(value))
            is Instant -> quote(value.toString())
            is Enum<*> -> quote(value.name)
            is Map<*, *> ->
                value.entries
                    .map { entry ->
                        val key = entry.key as? String ?: throw IllegalArgumentException("Canonical object key must be a string.")
                        key to entry.value
                    }.sortedBy { it.first }
                    .joinToString(separator = ",", prefix = "{", postfix = "}") { (key, item) ->
                        "${quote(key)}:${encode(item)}"
                    }

            is Iterable<*> -> value.joinToString(separator = ",", prefix = "[", postfix = "]") { encode(it) }
            else -> throw IllegalArgumentException("Unsupported canonical value type.")
        }

    fun decimal(value: BigDecimal): String {
        if (value.compareTo(BigDecimal.ZERO) == 0) {
            return "0"
        }
        return value.stripTrailingZeros().toPlainString()
    }

    fun sha256(value: String): String = sha256(value.toByteArray(StandardCharsets.UTF_8))

    fun sha256(value: ByteArray): String =
        MessageDigest
            .getInstance("SHA-256")
            .digest(value)
            .joinToString("") { "%02x".format(java.util.Locale.ROOT, it) }

    private fun quote(value: String): String =
        buildString(value.length + 2) {
            append('"')
            value.forEach { character ->
                when (character) {
                    '"' -> append("\\\"")
                    '\\' -> append("\\\\")
                    '\b' -> append("\\b")
                    '\u000C' -> append("\\f")
                    '\n' -> append("\\n")
                    '\r' -> append("\\r")
                    '\t' -> append("\\t")
                    else ->
                        if (character.code < 0x20) {
                            append("\\u%04x".format(java.util.Locale.ROOT, character.code))
                        } else {
                            append(character)
                        }
                }
            }
            append('"')
        }
}

/**
 * 저장 artifact와 hash가 공유하는 유일한 S2.2 V2 canonical representation이다.
 * `canonicalBytes`를 그대로 저장하고 그대로 SHA-256해 별도 축약 hash model이 생기지 않게 한다.
 */
class MetricSnapshotArtifactV2 private constructor(
    private val canonicalValue: Map<String, Any?>,
) {
    val canonicalJson: String = CanonicalJson.encode(canonicalValue)
    private val canonicalByteValue: ByteArray = canonicalJson.toByteArray(StandardCharsets.UTF_8)
    val canonicalBytes: ByteArray
        get() = canonicalByteValue.copyOf()
    val sha256: String = CanonicalJson.sha256(canonicalByteValue)

    fun semanticInput(): MetricSnapshotSemanticInputV2 {
        val semantic = canonicalValue.toMutableMap()
        semantic.remove("evaluationId")
        semantic.remove("retrievedAt")
        @Suppress("UNCHECKED_CAST")
        val metrics = semantic.getValue("metrics") as List<Map<String, Any?>>
        semantic["metrics"] =
            metrics.map { metric ->
                metric.filterKeys { key -> key != "retrievedAt" }
            }
        @Suppress("UNCHECKED_CAST")
        val disclosure = semantic["disclosureEvidence"] as? Map<String, Any?>
        if (disclosure != null) {
            // v2 rule은 score/mapping/applicability만 소비하므로 event code는 artifact provenance에만 둔다.
            semantic["disclosureEvidence"] = disclosure.filterKeys { key -> key != "eventCodes" }
        }
        return MetricSnapshotSemanticInputV2(semantic.toMap())
    }

    companion object {
        fun from(snapshot: MetricSnapshot): MetricSnapshotArtifactV2 =
            MetricSnapshotArtifactV2(
                mapOf(
                    "actorUserId" to snapshot.actorUserId,
                    "disclosureEvidence" to disclosureEvidence(snapshot),
                    "evaluationAsOf" to snapshot.evaluationAsOf,
                    "evaluationId" to snapshot.evaluationId,
                    "metrics" to
                        MetricKey.entries
                            .sortedBy(MetricKey::wireName)
                            .map { key -> metric(key, snapshot.metric(key)) },
                    "observedOptionalComponentEvidence" to
                        snapshot.observedOptionalComponentEvidence
                            .sortedBy(OptionalComponentEvidence::componentId)
                            .map(::optionalEvidence),
                    "orderIntent" to orderIntent(snapshot.orderIntent),
                    "portfolio" to
                        mapOf(
                            "ownerScopeHash" to snapshot.portfolio.ownerScopeHash,
                            "positionCount" to snapshot.portfolio.positionCount,
                            "revision" to snapshot.portfolio.revision,
                            "source" to snapshot.portfolio.source,
                        ),
                    "principle" to
                        mapOf(
                            "mode" to snapshot.principle.mode,
                            "principleId" to snapshot.principle.principleId,
                            "principleVersion" to snapshot.principle.version,
                            "principleVersionId" to snapshot.principle.principleVersionId,
                            "rulesHash" to snapshot.principle.rulesHash,
                        ),
                    "provenanceRefs" to snapshot.provenanceRefs.sorted(),
                    "readinessPolicyVersion" to snapshot.readinessPolicyVersion,
                    "requestedOptionalComponents" to snapshot.requestedOptionalComponents.sorted(),
                    "retrievedAt" to snapshot.retrievedAt,
                    "snapshotSchemaVersion" to snapshot.snapshotSchemaVersion,
                    "systemRuleCatalogVersion" to snapshot.systemRuleCatalogVersion,
                ),
            )

        private fun disclosureEvidence(snapshot: MetricSnapshot): Map<String, Any?>? =
            snapshot.disclosureEvidence?.let { evidence ->
                mapOf(
                    "completeness" to evidence.completeness,
                    "eventCodes" to evidence.eventCodes.sorted(),
                    "mappingVersion" to evidence.mappingVersion,
                    "sourceRefs" to evidence.sourceRefs.sorted(),
                )
            }

        private fun optionalEvidence(evidence: OptionalComponentEvidence): Map<String, Any?> =
            mapOf(
                "available" to evidence.available,
                "completeness" to evidence.completeness,
                "componentId" to evidence.componentId,
                "evidenceVersion" to evidence.evidenceVersion,
                "reasonCode" to evidence.reasonCode,
                "sourceRefs" to evidence.sourceRefs.sorted(),
            )

        private fun orderIntent(order: OrderIntentSnapshot): Map<String, Any?> =
            mapOf(
                "estimatedAmount" to order.estimatedAmount.toString(),
                "estimatedPrice" to order.estimatedPrice.toString(),
                "orderType" to order.orderType,
                "quantity" to order.quantity.toString(),
                "side" to order.side,
                "strategyId" to order.strategyId,
                "symbol" to order.symbol,
                "timeframe" to order.timeframe,
            )

        private fun metric(
            key: MetricKey,
            cell: MetricCell<MetricValue>,
        ): Map<String, Any?> =
            when (cell) {
                is MetricCell.Available ->
                    buildMap {
                        put("availability", "AVAILABLE")
                        put("declaredScale", cell.value.declaredScale)
                        put("freshUntil", cell.freshUntil)
                        put("metric", key.wireName)
                        put("observedAt", cell.observedAt)
                        put("retrievedAt", cell.retrievedAt)
                        put("source", cell.source)
                        put("sourceRef", cell.sourceRef)
                        put("sourceVersion", cell.sourceVersion)
                        put("unit", cell.value.unit)
                        put("value", CanonicalJson.decimal(cell.value.asBigDecimal()))
                        if (cell.value is MetricValue.RatioFraction) {
                            put("ratioNumerator", cell.value.numerator.toString())
                            put("ratioDenominator", cell.value.denominator.toString())
                        }
                    }

                is MetricCell.Missing -> unavailableMetric(key, "MISSING", cell.reason)
                is MetricCell.Stale ->
                    unavailableMetric(key, "STALE", cell.reason) +
                        mapOf("observedAt" to cell.observedAt, "freshUntil" to cell.freshUntil)

                is MetricCell.Error -> unavailableMetric(key, "ERROR", cell.reason)
                is MetricCell.Incomplete -> unavailableMetric(key, "INCOMPLETE", cell.reason)
                is MetricCell.Abstained -> unavailableMetric(key, "ABSTAINED", cell.reason)
                is MetricCell.NotApplicable -> unavailableMetric(key, "NOT_APPLICABLE", cell.reason)
            }

        private fun unavailableMetric(
            key: MetricKey,
            availability: String,
            reason: MetricIssueCode,
        ): Map<String, Any?> =
            mapOf(
                "availability" to availability,
                "metric" to key.wireName,
                "reason" to reason,
            )
    }
}

class MetricSnapshotSemanticInputV2 internal constructor(
    private val canonicalValue: Map<String, Any?>,
) {
    val canonicalJson: String = CanonicalJson.encode(canonicalValue)
    private val canonicalByteValue: ByteArray = canonicalJson.toByteArray(StandardCharsets.UTF_8)
    val canonicalBytes: ByteArray
        get() = canonicalByteValue.copyOf()
    val sha256: String = CanonicalJson.sha256(canonicalByteValue)
}

// semantic hash와 full artifact hash를 분리해 재현성과 저장 artifact 무결성을 서로 다른 목적으로 사용한다.
class SnapshotHashService {
    fun semanticInputHash(snapshot: MetricSnapshot): String = artifact(snapshot).semanticInput().sha256

    fun snapshotArtifactHash(snapshot: MetricSnapshot): String = artifact(snapshot).sha256

    fun semanticInputCanonicalJson(snapshot: MetricSnapshot): String = artifact(snapshot).semanticInput().canonicalJson

    fun snapshotArtifactCanonicalJson(snapshot: MetricSnapshot): String = artifact(snapshot).canonicalJson

    fun snapshotArtifactCanonicalBytes(snapshot: MetricSnapshot): ByteArray = artifact(snapshot).canonicalBytes

    private fun artifact(snapshot: MetricSnapshot): MetricSnapshotArtifactV2 = MetricSnapshotArtifactV2.from(snapshot)
}
