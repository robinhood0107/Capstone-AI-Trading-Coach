package com.capstone.decision.api.brokerage

import com.capstone.decision.application.brokerage.BrokerageFieldViolation
import com.capstone.decision.application.brokerage.BrokerageValidationException
import com.capstone.decision.application.brokerage.SubmitMockOrderCommand
import com.capstone.decision.application.brokerage.UserAcknowledgement
import com.capstone.decision.application.security.IdempotencyKeyPolicy
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
 * Brokerage write parser는 body-supplied actor/account/provider 필드를 모두 unknown으로 닫아 인증 principal만 권위로 둔다.
 */
@Component
class BrokerageRequestParser {
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

    fun parseSubmit(body: String): SubmitMockOrderCommand {
        val root = parseObject(body)
        val violations = mutableListOf<BrokerageFieldViolation>()
        rejectUnknown(root, ROOT_FIELDS, "", violations)
        val decisionId =
            requiredString(root, "decisionId", "/decisionId", violations)
                ?.takeIf { value ->
                    if (DECISION_ID.matches(value)) {
                        true
                    } else {
                        violations.add(BrokerageFieldViolation("/decisionId", "INVALID_FORMAT"))
                        false
                    }
                }
        val orderIntent = parseOrderIntent(root.path("orderIntent"), violations)
        val acknowledgement = parseAcknowledgement(root.path("userAcknowledgement"), violations)
        throwIfInvalid(violations)
        return SubmitMockOrderCommand(
            decisionId = requireNotNull(decisionId),
            orderIntent = requireNotNull(orderIntent),
            userAcknowledgement = requireNotNull(acknowledgement),
        )
    }

    fun parseOrderId(value: String): String {
        if (!ORDER_ID.matches(value)) {
            throw BrokerageValidationException(
                listOf(BrokerageFieldViolation("/path/orderId", "INVALID_FORMAT")),
            )
        }
        return value
    }

    fun parseAccountId(value: String): String {
        if (!ACCOUNT_ID.matches(value)) {
            throw BrokerageValidationException(
                listOf(BrokerageFieldViolation("/path/accountId", "INVALID_FORMAT")),
            )
        }
        return value
    }

    fun parseEmptyObject(body: String) {
        if (body.isBlank()) {
            return
        }
        val root = parseObject(body)
        val violations = mutableListOf<BrokerageFieldViolation>()
        rejectUnknown(root, emptySet(), "", violations)
        throwIfInvalid(violations)
    }

    fun parseBuyableQuery(request: HttpServletRequest): BuyableQuery {
        val violations =
            request.parameterMap.keys
                .filterNot { it in BUYABLE_QUERY_FIELDS }
                .map { name ->
                    BrokerageFieldViolation(
                        field = "/query/${escapePointer(name)}",
                        reason = "UNKNOWN_FIELD",
                    )
                }.toMutableList()
        val symbol =
            singleQuery(request, "symbol", violations)
                ?.takeIf { value ->
                    if (SYMBOL.matches(value)) {
                        true
                    } else {
                        violations.add(BrokerageFieldViolation("/query/symbol", "INVALID_FORMAT"))
                        false
                    }
                }
        val price =
            singleQuery(request, "price", violations)
                ?.let { value ->
                    value.toLongOrNull()?.takeIf { it > 0 }
                        ?: run {
                            violations.add(BrokerageFieldViolation("/query/price", "INVALID_FORMAT"))
                            null
                        }
                }
        throwIfInvalid(violations)
        return BuyableQuery(
            symbol = requireNotNull(symbol),
            price = requireNotNull(price),
        )
    }

    fun requireIdempotencyKey(value: String?): String {
        if (!IdempotencyKeyPolicy.isValid(value)) {
            throw BrokerageValidationException(
                listOf(BrokerageFieldViolation("/headers/X-Idempotency-Key", "INVALID_FORMAT")),
            )
        }
        return requireNotNull(value)
    }

    fun requireNoQuery(request: HttpServletRequest) {
        val violations =
            request.parameterMap.keys.map { name ->
                BrokerageFieldViolation(
                    field = "/query/${escapePointer(name)}",
                    reason = "UNKNOWN_FIELD",
                )
            }
        throwIfInvalid(violations)
    }

    private fun singleQuery(
        request: HttpServletRequest,
        name: String,
        violations: MutableList<BrokerageFieldViolation>,
    ): String? {
        val values = request.parameterMap[name]
        if (values == null || values.size != 1 || values.single().isBlank()) {
            violations.add(BrokerageFieldViolation("/query/$name", "REQUIRED"))
            return null
        }
        return values.single()
    }

    private fun parseAcknowledgement(
        node: JsonNode,
        violations: MutableList<BrokerageFieldViolation>,
    ): UserAcknowledgement? {
        if (!node.isObject) {
            violations.add(BrokerageFieldViolation("/userAcknowledgement", "INVALID_FORMAT"))
            return null
        }
        rejectUnknown(node, ACK_FIELDS, "/userAcknowledgement", violations)
        val accepted = node.get("warningsAccepted")
        if (accepted == null || !accepted.isBoolean) {
            violations.add(BrokerageFieldViolation("/userAcknowledgement/warningsAccepted", "INVALID_FORMAT"))
            return null
        }
        return UserAcknowledgement(accepted.booleanValue())
    }

    private fun parseOrderIntent(
        node: JsonNode,
        violations: MutableList<BrokerageFieldViolation>,
    ): OrderIntentSnapshot? {
        if (!node.isObject) {
            violations.add(BrokerageFieldViolation("/orderIntent", "INVALID_FORMAT"))
            return null
        }
        rejectUnknown(node, ORDER_FIELDS, "/orderIntent", violations)
        val symbol = requiredString(node, "symbol", "/orderIntent/symbol", violations)
        if (symbol != null && !SYMBOL.matches(symbol)) {
            violations.add(BrokerageFieldViolation("/orderIntent/symbol", "INVALID_FORMAT"))
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
            violations.add(BrokerageFieldViolation("/orderIntent/strategyId", "INVALID_FORMAT"))
        }
        if (quantity != null && estimatedPrice != null && estimatedAmount != null) {
            val exactAmount =
                try {
                    Math.multiplyExact(quantity, estimatedPrice)
                } catch (_: ArithmeticException) {
                    violations.add(BrokerageFieldViolation("/orderIntent/estimatedAmount", "OVERFLOW"))
                    null
                }
            if (exactAmount != null && exactAmount != estimatedAmount) {
                violations.add(BrokerageFieldViolation("/orderIntent/estimatedAmount", "MISMATCH"))
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
        violations: MutableList<BrokerageFieldViolation>,
    ): String? {
        val path = "/orderIntent/$field"
        val value = requiredString(root, field, path, violations) ?: return null
        if (value !in allowed) {
            violations.add(BrokerageFieldViolation(path, "INVALID_ENUM"))
            return null
        }
        return value
    }

    private fun positiveLong(
        root: JsonNode,
        field: String,
        violations: MutableList<BrokerageFieldViolation>,
    ): Long? {
        val path = "/orderIntent/$field"
        val node = root.get(field)
        if (node == null) {
            violations.add(BrokerageFieldViolation(path, "REQUIRED"))
            return null
        }
        if (!node.isIntegralNumber || !node.canConvertToLong()) {
            violations.add(BrokerageFieldViolation(path, "INVALID_FORMAT"))
            return null
        }
        val value = node.longValue()
        if (value <= 0) {
            violations.add(BrokerageFieldViolation(path, "OUT_OF_RANGE"))
            return null
        }
        return value
    }

    private fun requiredString(
        root: JsonNode,
        field: String,
        path: String,
        violations: MutableList<BrokerageFieldViolation>,
    ): String? {
        val node = root.get(field)
        if (node == null) {
            violations.add(BrokerageFieldViolation(path, "REQUIRED"))
            return null
        }
        if (!node.isString || node.stringValue().isBlank()) {
            violations.add(BrokerageFieldViolation(path, "INVALID_FORMAT"))
            return null
        }
        return node.stringValue()
    }

    private fun rejectUnknown(
        root: JsonNode,
        allowed: Set<String>,
        base: String,
        violations: MutableList<BrokerageFieldViolation>,
    ) {
        root.properties().forEach { (name, _) ->
            if (name !in allowed) {
                violations.add(BrokerageFieldViolation("$base/${escapePointer(name)}", "UNKNOWN_FIELD"))
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
            throw BrokerageValidationException(listOf(BrokerageFieldViolation("/", "INVALID_FORMAT")))
        }
        return parsed
    }

    private fun throwIfInvalid(violations: List<BrokerageFieldViolation>) {
        if (violations.isNotEmpty()) {
            throw BrokerageValidationException(violations)
        }
    }

    private fun escapePointer(value: String): String = value.replace("~", "~0").replace("/", "~1")

    private companion object {
        val ROOT_FIELDS = setOf("decisionId", "orderIntent", "userAcknowledgement")
        val ACK_FIELDS = setOf("warningsAccepted")
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
        val SIDES = setOf("BUY", "SELL")
        val ORDER_TYPES = setOf("MARKET", "LIMIT")
        val TIMEFRAMES = setOf("1d", "60m")
        val SYMBOL = Regex("^[0-9A-Z._:-]{1,20}$")
        val DECISION_ID = Regex("^dec_[0-9a-f]{32}$")
        val ORDER_ID = Regex("^ord_mock_[0-9a-f]{32}$")
        val ACCOUNT_ID = Regex("^acct_[0-9a-f]{32}$")
        val BUYABLE_QUERY_FIELDS = setOf("symbol", "price")
    }
}

data class BuyableQuery(
    val symbol: String,
    val price: Long,
)
