package com.capstone.decision.api.principle

import com.capstone.decision.application.principle.CatalogRuleDefinition
import com.capstone.decision.application.principle.CreatePrincipleCommand
import com.capstone.decision.application.principle.HistoryPageQuery
import com.capstone.decision.application.principle.HistorySort
import com.capstone.decision.application.principle.OwnerPageQuery
import com.capstone.decision.application.principle.OwnerSort
import com.capstone.decision.application.principle.PrincipleContract
import com.capstone.decision.application.principle.UpdatePrincipleCommand
import com.capstone.decision.domain.principle.EvidenceRequirement
import com.capstone.decision.domain.principle.PrincipleId
import com.capstone.decision.domain.principle.PrincipleMode
import com.capstone.decision.domain.principle.PrinciplePresetId
import com.capstone.decision.domain.principle.PrincipleRule
import com.capstone.decision.domain.principle.PrincipleStatus
import com.capstone.decision.domain.principle.PrincipleValidationException
import com.capstone.decision.domain.principle.PrincipleViolation
import jakarta.servlet.http.HttpServletRequest
import org.springframework.stereotype.Component
import tools.jackson.core.JacksonException
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.DeserializationFeature
import tools.jackson.databind.JsonNode
import tools.jackson.databind.json.JsonMapper
import java.math.BigDecimal
import java.text.Normalizer

// wire parser는 duplicate/unknown/type 오류를 DTO coercion 전에 닫고 rejected raw value를 오류에 반사하지 않는다.
@Component
class PrincipleRequestParser(
    private val catalog: PrincipleContract,
) {
    private val strictMapper =
        JsonMapper
            .builder(
                JsonFactory
                    .builder()
                    .streamReadConstraints(
                        StreamReadConstraints
                            .builder()
                            .maxNestingDepth(MAX_JSON_NESTING_DEPTH)
                            .maxDocumentLength(MAX_JSON_DOCUMENT_CHARS)
                            .maxTokenCount(MAX_JSON_TOKENS)
                            .maxNumberLength(MAX_JSON_NUMBER_CHARS)
                            .maxStringLength(MAX_JSON_STRING_CHARS)
                            .maxNameLength(MAX_JSON_NAME_CHARS)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).enable(DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS)
            .build()

    fun parseCreate(body: String): CreatePrincipleCommand {
        val root = parseObject(body)
        val violations = mutableListOf<PrincipleViolation>()
        rejectUnknownFields(root, CREATE_FIELDS, "", violations)

        val presetIdValue = requiredString(root, "presetId", "/presetId", violations)
        val presetId =
            presetIdValue?.let { value ->
                if (value !in catalog.presetIds) {
                    violations.add(PrincipleViolation("/presetId", "INVALID_ENUM"))
                    null
                } else {
                    PrinciplePresetId(value)
                }
            }
        val title = parseTitle(root, violations)
        val mode =
            if (root.has("mode")) {
                parseMode(root.get("mode"), "/mode", violations)
            } else {
                null
            }
        val rules =
            if (root.has("rules")) {
                parseRules(root.get("rules"), violations)
            } else {
                null
            }

        throwIfInvalid(violations)
        return CreatePrincipleCommand(
            presetId = requireNotNull(presetId),
            title = requireNotNull(title),
            mode = mode,
            rules = rules,
        )
    }

    fun parseUpdate(body: String): UpdatePrincipleCommand {
        val root = parseObject(body)
        val violations = mutableListOf<PrincipleViolation>()
        rejectUnknownFields(root, UPDATE_FIELDS, "", violations)

        val expectedVersion = parseExpectedVersion(root, violations)
        val title = parseTitle(root, violations)
        val mode = parseMode(requiredNode(root, "mode", "/mode", violations), "/mode", violations)
        val status = parseStatus(requiredNode(root, "status", "/status", violations), violations)
        val rules =
            requiredNode(root, "rules", "/rules", violations)
                ?.let { parseRules(it, violations) }

        throwIfInvalid(violations)
        return UpdatePrincipleCommand(
            expectedVersion = requireNotNull(expectedVersion),
            title = requireNotNull(title),
            mode = requireNotNull(mode),
            status = requireNotNull(status),
            rules = requireNotNull(rules),
        )
    }

    fun parsePrincipleId(value: String): PrincipleId {
        if (!PrincipleId.isValid(value)) {
            throw PrincipleValidationException(
                listOf(PrincipleViolation("/path/principleId", "INVALID_FORMAT")),
            )
        }
        return PrincipleId(value)
    }

    fun requireNoQuery(request: HttpServletRequest) {
        val violations =
            request.parameterMap.keys.map { name ->
                PrincipleViolation("/query/${pointerToken(name)}", "UNKNOWN_FIELD")
            }
        throwIfInvalid(violations)
    }

    fun parseOwnerQuery(request: HttpServletRequest): OwnerPageQuery {
        val violations = mutableListOf<PrincipleViolation>()
        rejectUnknownQuery(request, OWNER_QUERY_FIELDS, violations)
        val cursor = singleQueryValue(request, "cursor", violations)
        val size = parseSize(singleQueryValue(request, "size", violations), violations)
        val sort =
            parseSort(
                raw = singleQueryValue(request, "sort", violations),
                path = "/query/sort",
                values = OwnerSort.entries.associateBy(Enum<*>::name),
                violations = violations,
            )
        validateCursorShape(cursor, violations)
        throwIfInvalid(violations)
        return OwnerPageQuery(cursor = cursor, size = size, sort = sort)
    }

    fun parseHistoryQuery(request: HttpServletRequest): HistoryPageQuery {
        val violations = mutableListOf<PrincipleViolation>()
        rejectUnknownQuery(request, HISTORY_QUERY_FIELDS, violations)
        val cursor = singleQueryValue(request, "cursor", violations)
        val size = parseSize(singleQueryValue(request, "size", violations), violations)
        val sort =
            parseSort(
                raw = singleQueryValue(request, "sort", violations),
                path = "/query/sort",
                values = HistorySort.entries.associateBy(Enum<*>::name),
                violations = violations,
            )
        validateCursorShape(cursor, violations)
        throwIfInvalid(violations)
        return HistoryPageQuery(cursor = cursor, size = size, sort = sort)
    }

    private fun parseObject(body: String): JsonNode {
        val parsed =
            try {
                strictMapper.readTree(body)
            } catch (_: JacksonException) {
                throw invalidJson()
            } catch (_: IllegalArgumentException) {
                throw invalidJson()
            }
        if (parsed == null || !parsed.isObject) {
            throw invalidJson()
        }
        return parsed
    }

    private fun parseExpectedVersion(
        root: JsonNode,
        violations: MutableList<PrincipleViolation>,
    ): Int? {
        val node = requiredNode(root, "expectedVersion", "/expectedVersion", violations) ?: return null
        if (!node.isIntegralNumber) {
            violations.add(PrincipleViolation("/expectedVersion", "INVALID_FORMAT"))
            return null
        }
        if (!node.canConvertToInt() || node.longValue() !in 1..Int.MAX_VALUE.toLong()) {
            violations.add(PrincipleViolation("/expectedVersion", "OUT_OF_RANGE"))
            return null
        }
        return node.intValue()
    }

    private fun parseTitle(
        root: JsonNode,
        violations: MutableList<PrincipleViolation>,
    ): String? {
        val raw = requiredString(root, "title", "/title", violations) ?: return null
        val hasForbiddenCodePoint =
            raw.codePoints().anyMatch { codePoint ->
                val type = Character.getType(codePoint)
                codePoint == 0 ||
                    codePoint == '\r'.code ||
                    codePoint == '\n'.code ||
                    type == Character.CONTROL.toInt() ||
                    type == Character.FORMAT.toInt()
            }
        if (hasForbiddenCodePoint) {
            violations.add(PrincipleViolation("/title", "INVALID_FORMAT"))
        }
        val normalized = Normalizer.normalize(stripUnicodeWhitespace(raw), Normalizer.Form.NFC)
        val length = normalized.codePointCount(0, normalized.length)
        if (length !in catalog.titleMinCodePoints..catalog.titleMaxCodePoints) {
            violations.add(PrincipleViolation("/title", "OUT_OF_RANGE"))
        }
        return normalized
    }

    private fun parseMode(
        node: JsonNode?,
        path: String,
        violations: MutableList<PrincipleViolation>,
    ): PrincipleMode? {
        if (node == null) {
            return null
        }
        if (!node.isString) {
            violations.add(PrincipleViolation(path, "INVALID_FORMAT"))
            return null
        }
        return PrincipleMode.entries.firstOrNull { it.name == node.stringValue() }
            ?: run {
                violations.add(PrincipleViolation(path, "INVALID_ENUM"))
                null
            }
    }

    private fun parseStatus(
        node: JsonNode?,
        violations: MutableList<PrincipleViolation>,
    ): PrincipleStatus? {
        if (node == null) {
            return null
        }
        if (!node.isString) {
            violations.add(PrincipleViolation("/status", "INVALID_FORMAT"))
            return null
        }
        return PrincipleStatus.entries.firstOrNull { it.name == node.stringValue() }
            ?: run {
                violations.add(PrincipleViolation("/status", "INVALID_ENUM"))
                null
            }
    }

    private fun parseRules(
        node: JsonNode?,
        violations: MutableList<PrincipleViolation>,
    ): List<PrincipleRule>? {
        if (node == null || !node.isArray) {
            violations.add(PrincipleViolation("/rules", "INVALID_FORMAT"))
            return null
        }
        if (node.size() < catalog.rulesMinItems) {
            violations.add(PrincipleViolation("/rules", "TOO_FEW_ITEMS"))
        }
        if (node.size() > catalog.rulesMaxItems) {
            violations.add(PrincipleViolation("/rules", "TOO_MANY_ITEMS"))
        }

        val seenRuleIds = mutableSetOf<String>()
        val parsed =
            node.values().take(catalog.rulesMaxItems + 1).mapIndexedNotNull { index, item ->
                parseRule(item, index, seenRuleIds, violations)
            }
        return parsed.sortedBy { catalog.ruleDefinitions[it.ruleId]?.order ?: Int.MAX_VALUE }
    }

    private fun parseRule(
        node: JsonNode,
        index: Int,
        seenRuleIds: MutableSet<String>,
        violations: MutableList<PrincipleViolation>,
    ): PrincipleRule? {
        val base = "/rules/$index"
        if (!node.isObject) {
            violations.add(PrincipleViolation(base, "INVALID_FORMAT"))
            return null
        }
        rejectUnknownFields(node, RULE_FIELDS, base, violations)
        val ruleId = requiredString(node, "ruleId", "$base/ruleId", violations)
        val ruleType = requiredString(node, "ruleType", "$base/ruleType", violations)
        val metric = requiredString(node, "metric", "$base/metric", violations)
        val operator = requiredString(node, "operator", "$base/operator", violations)
        val thresholdNode = requiredNode(node, "threshold", "$base/threshold", violations)
        val severity = requiredString(node, "severity", "$base/severity", violations)
        val enabled = requiredBoolean(node, "enabled", "$base/enabled", violations)
        val evidenceRequirement =
            parseEvidenceRequirement(
                requiredNode(node, "evidenceRequirement", "$base/evidenceRequirement", violations),
                "$base/evidenceRequirement",
                violations,
            )

        if (ruleId != null && !seenRuleIds.add(ruleId)) {
            violations.add(PrincipleViolation("$base/ruleId", "DUPLICATE"))
        }
        val definition =
            ruleId?.let(catalog.ruleDefinitions::get)
                ?: run {
                    if (ruleId != null) {
                        violations.add(PrincipleViolation("$base/ruleId", "INVALID_ENUM"))
                    }
                    null
                }
        if (definition != null) {
            validateTuple(definition, ruleType, metric, operator, base, violations)
        }
        val threshold =
            definition?.let {
                parseThreshold(thresholdNode, it, "$base/threshold", violations)
            }
        if (definition != null && severity != null && enabled != null) {
            val validSeverity =
                if (enabled) {
                    severity in definition.enabledSeverities
                } else {
                    severity == definition.disabledSeverity
                }
            if (!validSeverity) {
                violations.add(PrincipleViolation("$base/severity", "INVALID_COMBINATION"))
            }
        }
        if (
            definition != null &&
            evidenceRequirement != null &&
            evidenceRequirement !in definition.evidenceRequirements
        ) {
            violations.add(PrincipleViolation("$base/evidenceRequirement", "INVALID_COMBINATION"))
        }

        if (
            definition == null ||
            ruleType == null ||
            metric == null ||
            operator == null ||
            threshold == null ||
            severity == null ||
            enabled == null ||
            evidenceRequirement == null
        ) {
            return null
        }
        return PrincipleRule(
            ruleId = definition.ruleId,
            ruleType = definition.ruleType,
            metric = definition.metric,
            operator = definition.operator,
            threshold = threshold,
            severity = severity,
            enabled = enabled,
            evidenceRequirement = evidenceRequirement,
        )
    }

    private fun parseEvidenceRequirement(
        node: JsonNode?,
        path: String,
        violations: MutableList<PrincipleViolation>,
    ): EvidenceRequirement? {
        if (node == null) {
            return null
        }
        if (!node.isString) {
            violations.add(PrincipleViolation(path, "INVALID_FORMAT"))
            return null
        }
        return EvidenceRequirement.entries.firstOrNull { it.name == node.stringValue() }
            ?: run {
                violations.add(PrincipleViolation(path, "INVALID_ENUM"))
                null
            }
    }

    private fun validateTuple(
        definition: CatalogRuleDefinition,
        ruleType: String?,
        metric: String?,
        operator: String?,
        base: String,
        violations: MutableList<PrincipleViolation>,
    ) {
        if (ruleType != null && ruleType != definition.ruleType) {
            violations.add(PrincipleViolation("$base/ruleType", "INVALID_COMBINATION"))
        }
        if (metric != null && metric != definition.metric) {
            violations.add(PrincipleViolation("$base/metric", "INVALID_COMBINATION"))
        }
        if (operator != null && operator != definition.operator) {
            violations.add(PrincipleViolation("$base/operator", "INVALID_COMBINATION"))
        }
    }

    private fun parseThreshold(
        node: JsonNode?,
        definition: CatalogRuleDefinition,
        path: String,
        violations: MutableList<PrincipleViolation>,
    ): BigDecimal? {
        if (node == null) {
            return null
        }
        if (!node.isNumber || definition.jsonType !in setOf("integer", "number")) {
            violations.add(PrincipleViolation(path, "INVALID_FORMAT"))
            return null
        }
        val value =
            try {
                node.decimalValue()
            } catch (_: ArithmeticException) {
                violations.add(PrincipleViolation(path, "INVALID_FORMAT"))
                return null
            }
        val normalized = value.stripTrailingZeros()
        // JSON의 정수 계약은 lexical token이 아니라 exact decimal의 수학적 값으로 판정한다.
        if (definition.jsonType == "integer" && normalized.scale() > 0) {
            violations.add(PrincipleViolation(path, "INVALID_FORMAT"))
            return null
        }
        if (value < definition.minimum || value > definition.maximum) {
            violations.add(PrincipleViolation(path, "OUT_OF_RANGE"))
            // 범위를 벗어난 지수 표기는 거대한 정수로 rescale하지 않고 즉시 닫는다.
            return null
        }
        val normalizedScale = normalized.scale().coerceAtLeast(0)
        if (normalizedScale > definition.maxNormalizedScale) {
            violations.add(PrincipleViolation(path, "INVALID_SCALE"))
        }
        return if (definition.jsonType == "integer" || normalized.scale() < 0) {
            normalized.setScale(0)
        } else {
            normalized
        }
    }

    private fun requiredString(
        node: JsonNode,
        field: String,
        path: String,
        violations: MutableList<PrincipleViolation>,
    ): String? {
        val value = requiredNode(node, field, path, violations) ?: return null
        if (!value.isString) {
            violations.add(PrincipleViolation(path, "INVALID_FORMAT"))
            return null
        }
        return value.stringValue()
    }

    private fun requiredBoolean(
        node: JsonNode,
        field: String,
        path: String,
        violations: MutableList<PrincipleViolation>,
    ): Boolean? {
        val value = requiredNode(node, field, path, violations) ?: return null
        if (!value.isBoolean) {
            violations.add(PrincipleViolation(path, "INVALID_FORMAT"))
            return null
        }
        return value.booleanValue()
    }

    private fun requiredNode(
        node: JsonNode,
        field: String,
        path: String,
        violations: MutableList<PrincipleViolation>,
    ): JsonNode? {
        val value = node.get(field)
        if (value == null || value.isNull) {
            violations.add(PrincipleViolation(path, "REQUIRED"))
            return null
        }
        return value
    }

    private fun rejectUnknownFields(
        node: JsonNode,
        allowed: Set<String>,
        base: String,
        violations: MutableList<PrincipleViolation>,
    ) {
        node
            .propertyNames()
            .filterNot(allowed::contains)
            .forEach { field ->
                violations.add(PrincipleViolation("$base/${pointerToken(field)}", "UNKNOWN_FIELD"))
            }
    }

    private fun rejectUnknownQuery(
        request: HttpServletRequest,
        allowed: Set<String>,
        violations: MutableList<PrincipleViolation>,
    ) {
        request.parameterMap.keys
            .filterNot(allowed::contains)
            .forEach { name ->
                violations.add(PrincipleViolation("/query/${pointerToken(name)}", "UNKNOWN_FIELD"))
            }
    }

    private fun singleQueryValue(
        request: HttpServletRequest,
        name: String,
        violations: MutableList<PrincipleViolation>,
    ): String? {
        val values = request.parameterMap[name] ?: return null
        if (values.size != 1) {
            violations.add(PrincipleViolation("/query/$name", "INVALID_FORMAT"))
            return null
        }
        return values.single()
    }

    private fun parseSize(
        raw: String?,
        violations: MutableList<PrincipleViolation>,
    ): Int? {
        if (raw == null) {
            return null
        }
        val size = raw.toIntOrNull()
        if (size == null) {
            violations.add(PrincipleViolation("/query/size", "INVALID_FORMAT"))
            return null
        }
        if (size !in catalog.pageMin..catalog.pageMax) {
            violations.add(PrincipleViolation("/query/size", "OUT_OF_RANGE"))
            return null
        }
        return size
    }

    private fun <T> parseSort(
        raw: String?,
        path: String,
        values: Map<String, T>,
        violations: MutableList<PrincipleViolation>,
    ): T? {
        if (raw == null) {
            return null
        }
        return values[raw]
            ?: run {
                violations.add(PrincipleViolation(path, "INVALID_ENUM"))
                null
            }
    }

    private fun validateCursorShape(
        cursor: String?,
        violations: MutableList<PrincipleViolation>,
    ) {
        if (cursor != null && (cursor.isEmpty() || cursor.length > catalog.cursorMaxChars)) {
            violations.add(PrincipleViolation("/query/cursor", "INVALID_CURSOR"))
        }
    }

    private fun stripUnicodeWhitespace(value: String): String {
        var start = 0
        var end = value.length
        while (start < end) {
            val codePoint = value.codePointAt(start)
            if (!isUnicodeWhitespace(codePoint)) break
            start += Character.charCount(codePoint)
        }
        while (start < end) {
            val codePoint = value.codePointBefore(end)
            if (!isUnicodeWhitespace(codePoint)) break
            end -= Character.charCount(codePoint)
        }
        return value.substring(start, end)
    }

    private fun isUnicodeWhitespace(codePoint: Int): Boolean = Character.isWhitespace(codePoint) || Character.isSpaceChar(codePoint)

    private fun pointerToken(value: String): String = value.replace("~", "~0").replace("/", "~1")

    private fun throwIfInvalid(violations: List<PrincipleViolation>) {
        if (violations.isNotEmpty()) {
            throw PrincipleValidationException(violations)
        }
    }

    private fun invalidJson(): PrincipleValidationException =
        PrincipleValidationException(listOf(PrincipleViolation("/", "INVALID_FORMAT")))

    companion object {
        private val CREATE_FIELDS = setOf("presetId", "title", "mode", "rules")
        private val UPDATE_FIELDS = setOf("expectedVersion", "title", "mode", "status", "rules")
        private val RULE_FIELDS =
            setOf(
                "ruleId",
                "ruleType",
                "metric",
                "operator",
                "threshold",
                "severity",
                "enabled",
                "evidenceRequirement",
            )
        private val OWNER_QUERY_FIELDS = setOf("cursor", "size", "sort")
        private val HISTORY_QUERY_FIELDS = setOf("cursor", "size", "sort")
        private const val MAX_JSON_NESTING_DEPTH = 16
        private const val MAX_JSON_DOCUMENT_CHARS = 1_048_576L
        private const val MAX_JSON_TOKENS = 256L
        private const val MAX_JSON_NUMBER_CHARS = 128
        private const val MAX_JSON_STRING_CHARS = 512
        private const val MAX_JSON_NAME_CHARS = 128
    }
}
