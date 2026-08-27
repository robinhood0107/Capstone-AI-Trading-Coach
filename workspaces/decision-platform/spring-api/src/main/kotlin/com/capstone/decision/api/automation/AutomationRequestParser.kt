package com.capstone.decision.api.automation

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.application.automation.ArmAutomationCommand
import com.capstone.decision.application.automation.DisarmAutomationCommand
import com.capstone.decision.application.security.IdempotencyKeyPolicy
import jakarta.servlet.http.HttpServletRequest
import org.springframework.stereotype.Component
import tools.jackson.core.JacksonException
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.JsonNode
import tools.jackson.databind.json.JsonMapper
import java.text.Normalizer

data class AutomationRunQuery(
    val size: Int,
    val cursor: String?,
)

/** Duplicate/unknown/coercion/depth를 DTO binding 전에 차단하는 Automation 전용 parser다. */
@Component
class AutomationRequestParser {
    private val strictMapper =
        JsonMapper
            .builder(
                JsonFactory
                    .builder()
                    .streamReadConstraints(
                        StreamReadConstraints
                            .builder()
                            .maxNestingDepth(4)
                            .maxDocumentLength(MAX_DOCUMENT_BYTES)
                            .maxTokenCount(32)
                            .maxStringLength(128)
                            .maxNameLength(64)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).build()

    fun parseArm(body: String): ArmAutomationCommand {
        val root = parseObject(body, ARM_FIELDS)
        return ArmAutomationCommand(
            brokerageMode = requiredString(root, "brokerageMode", BROKERAGE_MODES),
            accountId = requiredPattern(root, "accountId", ACCOUNT_ID),
            principleId = requiredPattern(root, "principleId", PRINCIPLE_ID),
            strategyId = requiredPattern(root, "strategyId", STRATEGY_ID),
            expectedVersion = requiredVersion(root, "expectedVersion"),
        )
    }

    fun parseDisarm(body: String): DisarmAutomationCommand {
        val root = parseObject(body, DISARM_FIELDS)
        return DisarmAutomationCommand(requiredVersion(root, "expectedVersion"))
    }

    fun parseRunsQuery(request: HttpServletRequest): AutomationRunQuery {
        rejectUnknownOrRepeatedQuery(request, RUN_QUERY_FIELDS)
        val rawSize = request.getParameter("size")
        val size = rawSize?.toIntOrNull() ?: if (rawSize == null) DEFAULT_PAGE_SIZE else invalid("/query/size")
        if (size !in 1..MAX_PAGE_SIZE) invalid("/query/size")
        val cursor = request.getParameter("cursor")
        if (cursor != null && (cursor.length !in 1..MAX_CURSOR_CHARS || !BASE64_URL.matches(cursor))) {
            invalid("/query/cursor")
        }
        return AutomationRunQuery(size, cursor)
    }

    fun requireNoQuery(request: HttpServletRequest) {
        rejectUnknownOrRepeatedQuery(request, emptySet())
    }

    fun requireIdempotencyKey(value: String?): String =
        value?.takeIf(IdempotencyKeyPolicy::isValid)
            ?: invalid("/headers/X-Idempotency-Key")

    private fun parseObject(
        body: String,
        fields: Set<String>,
    ): JsonNode {
        if (body.toByteArray(Charsets.UTF_8).size !in 2..MAX_DOCUMENT_BYTES.toInt()) invalid("/")
        val root =
            try {
                strictMapper.readTree(body)
            } catch (_: JacksonException) {
                null
            } catch (_: IllegalArgumentException) {
                null
            }
        if (root == null || !root.isObject) invalid("/")
        root.properties().forEach { (name, _) -> if (name !in fields) invalid("/${escape(name)}") }
        return root
    }

    private fun requiredString(
        root: JsonNode,
        field: String,
        allowed: Set<String>,
    ): String {
        val value = root.get(field)
        if (value == null || !value.isString || value.stringValue() !in allowed) invalid("/$field")
        return value.stringValue()
    }

    private fun requiredPattern(
        root: JsonNode,
        field: String,
        pattern: Regex,
    ): String {
        val value = root.get(field)
        if (value == null || !value.isString) invalid("/$field")
        val raw = value.stringValue()
        if (Normalizer.normalize(raw, Normalizer.Form.NFC) != raw || !pattern.matches(raw)) invalid("/$field")
        return raw
    }

    private fun requiredVersion(
        root: JsonNode,
        field: String,
    ): Int {
        val value = root.get(field)
        if (value == null || !value.isIntegralNumber || !value.canConvertToInt()) invalid("/$field")
        return value.intValue().takeIf { it >= 1 } ?: invalid("/$field")
    }

    private fun rejectUnknownOrRepeatedQuery(
        request: HttpServletRequest,
        allowed: Set<String>,
    ) {
        request.parameterMap.forEach { (name, values) ->
            if (name !in allowed || values.size != 1) invalid("/query/${escape(name)}")
        }
    }

    private fun invalid(field: String): Nothing =
        throw ApiException(
            ErrorCode.VALIDATION_ERROR,
            details = mapOf("violations" to listOf(mapOf("field" to field, "reason" to "INVALID_FORMAT"))),
        )

    private fun escape(value: String): String = value.take(256).replace("~", "~0").replace("/", "~1")

    private companion object {
        const val MAX_DOCUMENT_BYTES = 4096L
        const val DEFAULT_PAGE_SIZE = 20
        const val MAX_PAGE_SIZE = 100
        const val MAX_CURSOR_CHARS = 512
        val ARM_FIELDS = setOf("brokerageMode", "accountId", "principleId", "strategyId", "expectedVersion")
        val DISARM_FIELDS = setOf("expectedVersion")
        val RUN_QUERY_FIELDS = setOf("size", "cursor")
        val BROKERAGE_MODES = setOf("KIS_MOCK", "INTERNAL_PAPER")
        val ACCOUNT_ID = Regex("^acct_[A-Za-z0-9_-]{8,96}$")
        val PRINCIPLE_ID = Regex("^prc_[A-Za-z0-9_-]{8,96}$")
        val STRATEGY_ID = Regex("^strategy_[A-Za-z0-9_-]{8,96}$")
        val BASE64_URL = Regex("^[A-Za-z0-9_-]+$")
    }
}
