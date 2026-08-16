package com.capstone.decision.application.signal

import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.json.JsonMapper

/** Signal v1의 frozen top-level field 집합을 검증해 인접 권한 필드 삽입을 거부한다. */
object SignalV1Contract {
    private val mapper =
        JsonMapper
            .builder(
                JsonFactory
                    .builder()
                    .streamReadConstraints(
                        StreamReadConstraints
                            .builder()
                            .maxDocumentLength(65_536)
                            .maxNestingDepth(10)
                            .maxNameLength(128)
                            .maxStringLength(2048)
                            .maxTokenCount(2_000)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).build()

    /** frozen v1 success payload의 exact root를 검사하며 API 게시나 downstream 권한을 만들지 않는다. */
    fun validate(payload: ByteArray) {
        try {
            require(payload.isNotEmpty() && payload.size <= 65_536) { "Signal v1 payload size is invalid." }
            val root = mapper.readTree(payload)
            require(root.isObject) { "Signal v1 payload must be an object." }
            val fields = root.propertyNames().asSequence().toSet()
            require(fields.containsAll(REQUIRED) && ALLOWED.containsAll(fields)) {
                "Signal v1 fields drifted."
            }
        } catch (error: IllegalArgumentException) {
            throw error
        } catch (error: Exception) {
            throw IllegalArgumentException("Signal v1 validation failed.", error)
        }
    }

    private val REQUIRED =
        setOf(
            "symbol",
            "producer",
            "sourceWorkspace",
            "asOf",
            "timeframe",
            "finalSignal",
            "confidence",
            "predictedReturn",
            "featureSummary",
            "ruleBaseline",
            "lstm",
            "lightgbm",
            "newsSentiment",
            "hmmRegime",
        )
    private val ALLOWED = REQUIRED + "modelReportId"
}
