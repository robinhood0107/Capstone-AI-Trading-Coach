package com.capstone.decision.application.signal

import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.JsonNode
import tools.jackson.databind.json.JsonMapper
import java.time.Instant

/** Signal v2가 표현할 수 있는 예측값이며 HOLD도 정상적인 중립 예측이다. */
enum class SignalV2Prediction {
    BUY,
    HOLD,
    SELL,
}

/** 예측 component의 AVAILABLE/ABSTAIN closed union을 나타낸다. */
sealed interface SignalV2PredictiveComponent

/** 사용 가능한 예측만 signal과 confidence를 소유한다. */
data class SignalV2PredictiveAvailable(
    val producer: String,
    val sourceWorkspace: String,
    val asOf: Instant,
    val signal: SignalV2Prediction,
    val confidence: Double,
    val predictedReturn: Double?,
) : SignalV2PredictiveComponent

/** 사용할 수 없는 예측은 사유만 전달하고 예측값이나 시각을 위조하지 않는다. */
data class SignalV2PredictiveAbstain(
    val producer: String,
    val sourceWorkspace: String,
    val reason: String,
) : SignalV2PredictiveComponent

/** HMM regime component의 AVAILABLE/ABSTAIN closed union을 나타낸다. */
sealed interface SignalV2RegimeComponent

/** 사용 가능한 HMM 결과만 state, confidence, asOf를 소유한다. */
data class SignalV2RegimeAvailable(
    val asOf: Instant,
    val state: String,
    val confidence: Double,
) : SignalV2RegimeComponent

/** 사용할 수 없는 HMM 결과는 state와 asOf를 만들지 않고 사유만 전달한다. */
data class SignalV2RegimeAbstain(
    val reason: String,
) : SignalV2RegimeComponent

/** 네 required component를 고정해 누락 또는 임의 component 삽입을 타입 경계에서 막는다. */
data class SignalV2Components(
    val ruleBaseline: SignalV2PredictiveComponent,
    val lstm: SignalV2PredictiveComponent,
    val lightgbm: SignalV2PredictiveComponent,
    val hmmRegime: SignalV2RegimeComponent,
)

/** Signal v2 composite의 AVAILABLE/ABSTAIN closed union을 나타낸다. */
sealed interface SignalV2Composite

/** 모든 required component가 사용 가능할 때만 composite 예측을 표현한다. */
data class SignalV2CompositeAvailable(
    val signal: SignalV2Prediction,
    val confidence: Double,
    val predictedReturn: Double?,
) : SignalV2Composite

/** required component가 하나라도 없으면 composite도 명시적으로 기권한다. */
data object SignalV2CompositeAbstain : SignalV2Composite

/** 검증된 contract-only Signal v2 값이며 API 게시나 RiskDecision 권한을 포함하지 않는다. */
data class ValidatedSignalV2(
    val symbol: String,
    val asOf: Instant,
    val timeframe: String,
    val composite: SignalV2Composite,
    val components: SignalV2Components,
)

object SignalV2Contract {
    private val strictMapper =
        JsonMapper
            .builder(
                JsonFactory
                    .builder()
                    .streamReadConstraints(
                        StreamReadConstraints
                            .builder()
                            .maxDocumentLength(MAX_PAYLOAD_BYTES)
                            .maxNestingDepth(10)
                            .maxNameLength(128)
                            .maxStringLength(2048)
                            .maxTokenCount(2_000)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).build()

    /**
     * Python generator와 같은 closed union 및 semantic 불변식을 bounded JSON bytes에서 검증한다.
     * 이 parser는 Spring bean, endpoint, RiskDecision 또는 주문 경로에 연결되지 않는다.
     */
    fun validate(payload: ByteArray): ValidatedSignalV2 {
        try {
            require(payload.isNotEmpty() && payload.size <= MAX_PAYLOAD_BYTES) {
                "Signal v2 payload size is invalid."
            }
            return parseRoot(strictMapper.readTree(payload))
        } catch (error: IllegalArgumentException) {
            if (error.message?.contains("Signal v2") == true) {
                throw error
            }
            throw IllegalArgumentException("Signal v2 validation failed.", error)
        } catch (error: Exception) {
            throw IllegalArgumentException("Signal v2 validation failed.", error)
        }
    }

    private fun parseRoot(root: JsonNode): ValidatedSignalV2 {
        exactFields(root, ROOT_REQUIRED, ROOT_ALLOWED, "root")
        val symbol = requiredText(root, "symbol")
        require(SYMBOL_PATTERN.matches(symbol)) { "Signal v2 symbol is invalid." }
        val asOf = requiredInstant(root, "asOf")
        val timeframe = requiredText(root, "timeframe")
        require(timeframe in TIMEFRAMES) { "Signal v2 timeframe is invalid." }
        optionalBoundedText(root, "modelReportId")
        validateTextArray(root.path("warnings"), maximum = 10, field = "warnings")

        val componentNode = root.path("components")
        exactFields(componentNode, COMPONENT_FIELDS, COMPONENT_FIELDS, "components")
        val components =
            SignalV2Components(
                ruleBaseline =
                    parsePredictive(
                        componentNode.path("ruleBaseline"),
                        producer = "RULE_BASELINE",
                        workspace = "return-engine",
                    ),
                lstm =
                    parsePredictive(
                        componentNode.path("lstm"),
                        producer = "LSTM",
                        workspace = "return-engine",
                    ),
                lightgbm =
                    parsePredictive(
                        componentNode.path("lightgbm"),
                        producer = "LIGHTGBM",
                        workspace = "decision-platform",
                    ),
                hmmRegime = parseRegime(componentNode.path("hmmRegime")),
            )
        val composite = parseComposite(root.path("composite"))
        validateCompositeAvailability(components, composite)
        validateLatestAsOf(components, asOf)
        return ValidatedSignalV2(symbol, asOf, timeframe, composite, components)
    }

    private fun parsePredictive(
        node: JsonNode,
        producer: String,
        workspace: String,
    ): SignalV2PredictiveComponent =
        when (requiredText(node, "status")) {
            "AVAILABLE" -> {
                exactFields(node, PREDICTIVE_AVAILABLE_REQUIRED, PREDICTIVE_AVAILABLE_ALLOWED, producer)
                validateProducer(node, producer, workspace)
                validateOptionalModelFields(node)
                node.path("featureSummary").takeUnless(JsonNode::isMissingNode)?.let {
                    validateTextArray(it, maximum = 32, field = "$producer featureSummary")
                }
                SignalV2PredictiveAvailable(
                    producer = producer,
                    sourceWorkspace = workspace,
                    asOf = requiredInstant(node, "asOf"),
                    signal = requiredPrediction(node, "signal"),
                    confidence = requiredConfidence(node, "confidence"),
                    predictedReturn = optionalFiniteNumber(node, "predictedReturn"),
                )
            }

            "ABSTAIN" -> {
                exactFields(node, PREDICTIVE_ABSTAIN_REQUIRED, PREDICTIVE_ABSTAIN_ALLOWED, producer)
                validateProducer(node, producer, workspace)
                validateOptionalModelFields(node)
                node.path("warnings").takeUnless(JsonNode::isMissingNode)?.let {
                    validateTextArray(it, maximum = 10, field = "$producer warnings")
                }
                SignalV2PredictiveAbstain(
                    producer = producer,
                    sourceWorkspace = workspace,
                    reason = requiredAbstainReason(node),
                )
            }

            else -> throw IllegalArgumentException("Signal v2 component status is invalid.")
        }

    private fun parseRegime(node: JsonNode): SignalV2RegimeComponent =
        when (requiredText(node, "status")) {
            "AVAILABLE" -> {
                exactFields(node, REGIME_AVAILABLE_REQUIRED, REGIME_AVAILABLE_ALLOWED, "HMM")
                validateProducer(node, "HMM", "decision-platform")
                validateOptionalModelFields(node)
                val state = requiredText(node, "state")
                require(state in REGIME_STATES) { "Signal v2 HMM state is invalid." }
                SignalV2RegimeAvailable(
                    asOf = requiredInstant(node, "asOf"),
                    state = state,
                    confidence = requiredConfidence(node, "confidence"),
                )
            }

            "ABSTAIN" -> {
                exactFields(node, PREDICTIVE_ABSTAIN_REQUIRED, PREDICTIVE_ABSTAIN_ALLOWED, "HMM")
                validateProducer(node, "HMM", "decision-platform")
                validateOptionalModelFields(node)
                node.path("warnings").takeUnless(JsonNode::isMissingNode)?.let {
                    validateTextArray(it, maximum = 10, field = "HMM warnings")
                }
                SignalV2RegimeAbstain(requiredAbstainReason(node))
            }

            else -> throw IllegalArgumentException("Signal v2 HMM status is invalid.")
        }

    private fun parseComposite(node: JsonNode): SignalV2Composite =
        when (requiredText(node, "status")) {
            "AVAILABLE" -> {
                exactFields(node, COMPOSITE_AVAILABLE_REQUIRED, COMPOSITE_AVAILABLE_ALLOWED, "composite")
                SignalV2CompositeAvailable(
                    signal = requiredPrediction(node, "signal"),
                    confidence = requiredConfidence(node, "confidence"),
                    predictedReturn = optionalFiniteNumber(node, "predictedReturn"),
                )
            }

            "ABSTAIN" -> {
                exactFields(node, COMPOSITE_ABSTAIN_FIELDS, COMPOSITE_ABSTAIN_FIELDS, "composite")
                require(requiredText(node, "reason") == "REQUIRED_COMPONENT_UNAVAILABLE") {
                    "Signal v2 composite ABSTAIN reason is invalid."
                }
                SignalV2CompositeAbstain
            }

            else -> throw IllegalArgumentException("Signal v2 composite status is invalid.")
        }

    private fun validateCompositeAvailability(
        components: SignalV2Components,
        composite: SignalV2Composite,
    ) {
        val allAvailable =
            components.ruleBaseline is SignalV2PredictiveAvailable &&
                components.lstm is SignalV2PredictiveAvailable &&
                components.lightgbm is SignalV2PredictiveAvailable &&
                components.hmmRegime is SignalV2RegimeAvailable
        require(allAvailable == (composite is SignalV2CompositeAvailable)) {
            "Signal v2 required component availability and composite status diverged."
        }
    }

    private fun validateLatestAsOf(
        components: SignalV2Components,
        topAsOf: Instant,
    ) {
        val availableTimes =
            listOfNotNull(
                (components.ruleBaseline as? SignalV2PredictiveAvailable)?.asOf,
                (components.lstm as? SignalV2PredictiveAvailable)?.asOf,
                (components.lightgbm as? SignalV2PredictiveAvailable)?.asOf,
                (components.hmmRegime as? SignalV2RegimeAvailable)?.asOf,
            )
        require(availableTimes.isNotEmpty() && topAsOf == availableTimes.max()) {
            "Signal v2 top-level asOf must equal the latest AVAILABLE component."
        }
    }

    private fun validateProducer(
        node: JsonNode,
        producer: String,
        workspace: String,
    ) {
        require(requiredText(node, "producer") == producer) {
            "Signal v2 producer drifted."
        }
        require(requiredText(node, "sourceWorkspace") == workspace) {
            "Signal v2 source workspace drifted."
        }
    }

    private fun validateOptionalModelFields(node: JsonNode) {
        optionalBoundedText(node, "modelVersion")
        optionalBoundedText(node, "modelReportId")
    }

    private fun optionalBoundedText(
        node: JsonNode,
        field: String,
    ) {
        if (node.has(field)) {
            val value = requiredText(node, field)
            require(value.length <= 128) { "Signal v2 $field exceeds its bound." }
        }
    }

    private fun requiredAbstainReason(node: JsonNode): String {
        val reason = requiredText(node, "reason")
        require(reason in ABSTAIN_REASONS) { "Signal v2 ABSTAIN reason is invalid." }
        return reason
    }

    private fun requiredPrediction(
        node: JsonNode,
        field: String,
    ): SignalV2Prediction =
        try {
            SignalV2Prediction.valueOf(requiredText(node, field))
        } catch (error: IllegalArgumentException) {
            throw IllegalArgumentException("Signal v2 prediction is invalid.", error)
        }

    private fun requiredConfidence(
        node: JsonNode,
        field: String,
    ): Double {
        val value = requiredFiniteNumber(node, field)
        require(value in 0.0..1.0) { "Signal v2 confidence is out of range." }
        return value
    }

    private fun optionalFiniteNumber(
        node: JsonNode,
        field: String,
    ): Double? {
        val value = node.path(field)
        if (value.isMissingNode || value.isNull) {
            return null
        }
        return requiredFiniteNumber(node, field)
    }

    private fun requiredFiniteNumber(
        node: JsonNode,
        field: String,
    ): Double {
        val value = node.path(field)
        require(value.isNumber) { "Signal v2 $field must be numeric." }
        val number = value.doubleValue()
        require(number.isFinite()) { "Signal v2 $field must be finite." }
        return number
    }

    private fun requiredInstant(
        node: JsonNode,
        field: String,
    ): Instant =
        try {
            Instant.parse(requiredText(node, field))
        } catch (error: Exception) {
            throw IllegalArgumentException("Signal v2 $field must be a timezone-aware timestamp.", error)
        }

    private fun requiredText(
        node: JsonNode,
        field: String,
    ): String {
        val value = node.path(field)
        require(value.isTextual) { "Signal v2 $field must be text." }
        val text = value.stringValue()
        require(text.isNotEmpty()) { "Signal v2 $field must not be empty." }
        return text
    }

    private fun validateTextArray(
        node: JsonNode,
        maximum: Int,
        field: String,
    ) {
        require(node.isArray && node.size() <= maximum) {
            "Signal v2 $field array is invalid."
        }
        val values =
            node
                .values()
                .asSequence()
                .map { value ->
                    require(value.isTextual) { "Signal v2 $field values must be text." }
                    value.stringValue()
                }.toList()
        require(values.all { it.isNotEmpty() && it.length <= 256 } && values.distinct().size == values.size) {
            "Signal v2 $field values are invalid."
        }
    }

    private fun exactFields(
        node: JsonNode,
        required: Set<String>,
        allowed: Set<String>,
        label: String,
    ) {
        require(node.isObject) { "Signal v2 $label must be an object." }
        val fields = node.propertyNames().asSequence().toSet()
        require(fields.containsAll(required) && allowed.containsAll(fields)) {
            "Signal v2 $label fields drifted."
        }
    }

    private const val MAX_PAYLOAD_BYTES = 65_536L
    private val SYMBOL_PATTERN = Regex("^[0-9A-Z._:-]{1,20}$")
    private val TIMEFRAMES = setOf("1d", "60m")
    private val ABSTAIN_REASONS =
        setOf(
            "ARTIFACT_DRIFT",
            "CALIBRATION_FAILED",
            "MISSING_EVIDENCE",
            "POSTERIOR_BELOW_THRESHOLD",
            "PRODUCER_FAILED",
            "STALE_EVIDENCE",
            "UNIDENTIFIABLE_OUTPUT",
        )
    private val REGIME_STATES =
        setOf("NORMAL", "SIDEWAYS", "HIGH_VOLATILITY", "RISK_OFF", "RISK_ON")
    private val ROOT_REQUIRED = setOf("symbol", "asOf", "timeframe", "composite", "components", "warnings")
    private val ROOT_ALLOWED = ROOT_REQUIRED + "modelReportId"
    private val COMPONENT_FIELDS = setOf("ruleBaseline", "lstm", "lightgbm", "hmmRegime")
    private val PREDICTIVE_AVAILABLE_REQUIRED =
        setOf("status", "producer", "sourceWorkspace", "asOf", "signal", "confidence")
    private val PREDICTIVE_AVAILABLE_ALLOWED =
        PREDICTIVE_AVAILABLE_REQUIRED +
            setOf("modelVersion", "modelReportId", "predictedReturn", "featureSummary")
    private val PREDICTIVE_ABSTAIN_REQUIRED = setOf("status", "producer", "sourceWorkspace", "reason")
    private val PREDICTIVE_ABSTAIN_ALLOWED =
        PREDICTIVE_ABSTAIN_REQUIRED + setOf("modelVersion", "modelReportId", "warnings")
    private val REGIME_AVAILABLE_REQUIRED =
        setOf("status", "producer", "sourceWorkspace", "asOf", "state", "confidence")
    private val REGIME_AVAILABLE_ALLOWED =
        REGIME_AVAILABLE_REQUIRED + setOf("modelVersion", "modelReportId")
    private val COMPOSITE_AVAILABLE_REQUIRED = setOf("status", "signal", "confidence")
    private val COMPOSITE_AVAILABLE_ALLOWED = COMPOSITE_AVAILABLE_REQUIRED + "predictedReturn"
    private val COMPOSITE_ABSTAIN_FIELDS = setOf("status", "reason")
}
