package com.capstone.decision.api.decision

import com.capstone.decision.application.decision.DecisionFieldViolation
import com.capstone.decision.application.decision.DecisionValidationException
import com.capstone.decision.application.decision.EvaluateOrderCommand
import com.capstone.decision.domain.principle.PrincipleId
import com.capstone.decision.domain.risk.EvaluationBounds
import com.capstone.decision.domain.risk.OrderIntentSnapshot
import jakarta.servlet.http.HttpServletRequest
import org.springframework.stereotype.Component
import tools.jackson.core.JacksonException
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.JsonNode
import tools.jackson.databind.json.JsonMapper

/**
 * evaluate-order wire parser는 duplicate/unknown/type/overflow를 coercion 전에 닫고 rejected 값을 응답에 반사하지 않는다.
 */
@Component
class DecisionRequestParser {
    private val strictMapper =
        JsonMapper
            .builder(
                JsonFactory
                    .builder()
                    .streamReadConstraints(
                        StreamReadConstraints
                            .builder()
                            .maxNestingDepth(8)
                            .maxDocumentLength(EvaluationBounds.MAX_REQUEST_BYTES.toLong())
                            .maxTokenCount(128)
                            .maxNumberLength(32)
                            .maxStringLength(256)
                            .maxNameLength(64)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).build()

    fun parseEvaluate(body: String): EvaluateOrderCommand {
        val root = parseObject(body)
        val violations = mutableListOf<DecisionFieldViolation>()
        rejectUnknown(root, ROOT_FIELDS, "", violations)
        val principleId =
            requiredString(root, "principleId", "/principleId", violations)
                ?.takeIf { value ->
                    if (PrincipleId.isValid(value)) {
                        true
                    } else {
                        violations.add(DecisionFieldViolation("/principleId", "INVALID_FORMAT"))
                        false
                    }
                }?.let(::PrincipleId)
        val portfolioSource =
            requiredString(root, "portfolioSource", "/portfolioSource", violations)
                ?.takeIf { value ->
                    if (value in PORTFOLIO_SOURCES) {
                        true
                    } else {
                        violations.add(DecisionFieldViolation("/portfolioSource", "INVALID_ENUM"))
                        false
                    }
                }
        val orderIntent = parseOrderIntent(root.path("orderIntent"), violations)
        throwIfInvalid(violations)
        return EvaluateOrderCommand(
            principleId = requireNotNull(principleId),
            portfolioSource = requireNotNull(portfolioSource),
            orderIntent = requireNotNull(orderIntent),
        )
    }

    fun requireIdempotencyKey(value: String?): String {
        if (
            value == null ||
            value.length !in 16..128 ||
            !IDEMPOTENCY_KEY.matches(value)
        ) {
            throw DecisionValidationException(
                listOf(DecisionFieldViolation("/headers/X-Idempotency-Key", "INVALID_FORMAT")),
            )
        }
        return value
    }

    fun parseDecisionId(value: String): String {
        if (!DECISION_ID.matches(value)) {
            throw DecisionValidationException(
                listOf(DecisionFieldViolation("/path/decisionId", "INVALID_FORMAT")),
            )
        }
        return value
    }

    fun requireNoQuery(request: HttpServletRequest) {
        val violations =
            request.parameterMap.keys.map { name ->
                DecisionFieldViolation(
                    field = "/query/${name.replace("~", "~0").replace("/", "~1")}",
                    reason = "UNKNOWN_FIELD",
                )
            }
        throwIfInvalid(violations)
    }

    private fun parseOrderIntent(
        node: JsonNode,
        violations: MutableList<DecisionFieldViolation>,
    ): OrderIntentSnapshot? {
        if (!node.isObject) {
            violations.add(DecisionFieldViolation("/orderIntent", "INVALID_FORMAT"))
            return null
        }
        rejectUnknown(node, ORDER_FIELDS, "/orderIntent", violations)
        val symbol = requiredString(node, "symbol", "/orderIntent/symbol", violations)
        if (symbol != null && !SYMBOL.matches(symbol)) {
            violations.add(DecisionFieldViolation("/orderIntent/symbol", "INVALID_FORMAT"))
        }
        val side = enumString(node, "side", SIDES, violations)
        val orderType = enumString(node, "orderType", ORDER_TYPES, violations)
        val quantity = positiveLong(node, "quantity", violations)
        val estimatedPrice = positiveLong(node, "estimatedPrice", violations)
        val estimatedAmount = positiveLong(node, "estimatedAmount", violations)
        val timeframe = enumString(node, "timeframe", TIMEFRAMES, violations)
        val strategyId = requiredString(node, "strategyId", "/orderIntent/strategyId", violations)
        if (
            strategyId != null &&
            (
                strategyId.codePointCount(0, strategyId.length) !in 1..120 ||
                    strategyId.codePoints().anyMatch { Character.isISOControl(it) }
            )
        ) {
            violations.add(DecisionFieldViolation("/orderIntent/strategyId", "INVALID_FORMAT"))
        }
        if (quantity != null && estimatedPrice != null && estimatedAmount != null) {
            val exactAmount =
                try {
                    Math.multiplyExact(quantity, estimatedPrice)
                } catch (_: ArithmeticException) {
                    violations.add(DecisionFieldViolation("/orderIntent/estimatedAmount", "OVERFLOW"))
                    null
                }
            if (exactAmount != null && exactAmount != estimatedAmount) {
                violations.add(DecisionFieldViolation("/orderIntent/estimatedAmount", "MISMATCH"))
            }
        }
        if (violations.isNotEmpty()) {
            return null
        }
        return OrderIntentSnapshot(
            symbol = requireNotNull(symbol),
            side = requireNotNull(side),
            orderType = requireNotNull(orderType),
            quantity = requireNotNull(quantity),
            estimatedPrice = requireNotNull(estimatedPrice),
            estimatedAmount = requireNotNull(estimatedAmount),
            timeframe = requireNotNull(timeframe),
            strategyId = requireNotNull(strategyId),
        )
    }

    private fun enumString(
        root: JsonNode,
        field: String,
        allowed: Set<String>,
        violations: MutableList<DecisionFieldViolation>,
    ): String? {
        val path = "/orderIntent/$field"
        val value = requiredString(root, field, path, violations) ?: return null
        if (value !in allowed) {
            violations.add(DecisionFieldViolation(path, "INVALID_ENUM"))
            return null
        }
        return value
    }

    private fun positiveLong(
        root: JsonNode,
        field: String,
        violations: MutableList<DecisionFieldViolation>,
    ): Long? {
        val path = "/orderIntent/$field"
        val node = root.get(field)
        if (node == null) {
            violations.add(DecisionFieldViolation(path, "REQUIRED"))
            return null
        }
        if (!node.isIntegralNumber || !node.canConvertToLong()) {
            violations.add(DecisionFieldViolation(path, "INVALID_FORMAT"))
            return null
        }
        val value = node.longValue()
        if (value <= 0) {
            violations.add(DecisionFieldViolation(path, "OUT_OF_RANGE"))
            return null
        }
        return value
    }

    private fun requiredString(
        root: JsonNode,
        field: String,
        path: String,
        violations: MutableList<DecisionFieldViolation>,
    ): String? {
        val node = root.get(field)
        if (node == null) {
            violations.add(DecisionFieldViolation(path, "REQUIRED"))
            return null
        }
        if (!node.isString || node.stringValue().isBlank()) {
            violations.add(DecisionFieldViolation(path, "INVALID_FORMAT"))
            return null
        }
        return node.stringValue()
    }

    private fun rejectUnknown(
        root: JsonNode,
        allowed: Set<String>,
        base: String,
        violations: MutableList<DecisionFieldViolation>,
    ) {
        root.properties().forEach { (name, _) ->
            if (name !in allowed) {
                violations.add(DecisionFieldViolation("$base/${name.replace("~", "~0").replace("/", "~1")}", "UNKNOWN_FIELD"))
            }
        }
    }

    private fun parseObject(body: String): JsonNode {
        val parsed =
            try {
                strictMapper.readTree(body)
            } catch (_: JacksonException) {
                null
            } catch (_: IllegalArgumentException) {
                null
            }
        if (parsed == null || !parsed.isObject) {
            throw DecisionValidationException(listOf(DecisionFieldViolation("/", "INVALID_FORMAT")))
        }
        return parsed
    }

    private fun throwIfInvalid(violations: List<DecisionFieldViolation>) {
        if (violations.isNotEmpty()) {
            throw DecisionValidationException(violations)
        }
    }

    private companion object {
        val ROOT_FIELDS = setOf("principleId", "portfolioSource", "orderIntent")
        val ORDER_FIELDS =
            setOf(
                "symbol",
                "side",
                "orderType",
                "quantity",
                "estimatedPrice",
                "estimatedAmount",
                "timeframe",
                "strategyId",
            )
        val PORTFOLIO_SOURCES = setOf("KIS_MOCK", "INTERNAL_PAPER")
        val SIDES = setOf("BUY", "SELL")
        val ORDER_TYPES = setOf("MARKET", "LIMIT")
        val TIMEFRAMES = setOf("1d", "60m")
        val SYMBOL = Regex("^[0-9A-Z._:-]{1,20}$")
        val IDEMPOTENCY_KEY = Regex("^[A-Za-z0-9._:-]+$")
        val DECISION_ID = Regex("^dec_[0-9a-f]{32}$")
    }
}
