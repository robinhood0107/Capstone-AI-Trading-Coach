package com.capstone.decision.api.automation

import com.capstone.decision.api.common.ApiException
import com.capstone.decision.api.common.ErrorCode
import com.capstone.decision.application.automation.ArmAutomationCommand
import com.capstone.decision.application.automation.ArmAutomationV2Command
import com.capstone.decision.application.automation.ArmAutomationV3Command
import com.capstone.decision.application.automation.DisarmAutomationCommand
import com.capstone.decision.application.automation.PutAutomationPolicyV2Command
import com.capstone.decision.application.automation.PutAutomationPolicyV3Command
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

    fun parsePutPolicyV2(body: String): PutAutomationPolicyV2Command {
        val root = parseObject(body, POLICY_V2_FIELDS)
        val capital = requiredLong(root, "capitalLimitKrw")
        val stopLoss = requiredInt(root, "stopLossBps")
        val takeProfit = requiredInt(root, "takeProfitBps")
        if (capital !in 10_000L..10_000_000_000L || capital % 10_000L != 0L) invalid("/capitalLimitKrw")
        if (stopLoss !in 100..1_500) invalid("/stopLossBps")
        if (takeProfit !in 200..3_000 || takeProfit <= stopLoss) invalid("/takeProfitBps")
        return PutAutomationPolicyV2Command(
            capitalLimitKrw = capital,
            stopLossBps = stopLoss,
            takeProfitBps = takeProfit,
            expectedVersion = requiredNonnegativeVersion(root, "expectedVersion"),
        )
    }

    fun parseArmV2(body: String): ArmAutomationV2Command {
        val root = parseObject(body, ARM_V2_FIELDS)
        return ArmAutomationV2Command(
            accountId = requiredPattern(root, "accountId", ACCOUNT_ID),
            policyId = requiredPattern(root, "policyId", POLICY_ID),
            expectedPolicyVersion = requiredVersion(root, "expectedPolicyVersion"),
            expectedControlVersion = requiredVersion(root, "expectedControlVersion"),
        )
    }

    fun parsePutPolicyV3(body: String): PutAutomationPolicyV3Command {
        val root = parseObject(body, POLICY_V3_FIELDS)
        val capital = requiredLong(root, "capitalLimitKrw")
        val stopLoss = requiredInt(root, "stopLossBps")
        val takeProfit = requiredInt(root, "takeProfitBps")
        val holding = requiredInt(root, "maxHoldingSessions")
        val atrPeriod = requiredInt(root, "atrPeriod")
        val atrMultiplier = requiredInt(root, "atrMultiplierMilli")
        if (capital !in 10_000L..10_000_000_000L || capital % 10_000L != 0L) invalid("/capitalLimitKrw")
        if (stopLoss !in 100..1_500) invalid("/stopLossBps")
        if (takeProfit !in 200..3_000 || takeProfit <= stopLoss) invalid("/takeProfitBps")
        if (holding !in 0..1_260) invalid("/maxHoldingSessions")
        if (atrPeriod !in 5..100) invalid("/atrPeriod")
        if (atrMultiplier !in 1_000..10_000 || atrMultiplier % 100 != 0) invalid("/atrMultiplierMilli")
        return PutAutomationPolicyV3Command(
            capitalLimitKrw = capital,
            stopLossBps = stopLoss,
            takeProfitBps = takeProfit,
            maxHoldingSessions = holding,
            atrPeriod = atrPeriod,
            atrMultiplierMilli = atrMultiplier,
            modelSellEnabled = requiredBoolean(root, "modelSellEnabled"),
            expectedVersion = requiredNonnegativeVersion(root, "expectedVersion"),
        )
    }

    fun parseArmV3(body: String): ArmAutomationV3Command {
        val root = parseObject(body, ARM_V2_FIELDS)
        return ArmAutomationV3Command(
            accountId = requiredPattern(root, "accountId", ACCOUNT_ID),
            policyId = requiredPattern(root, "policyId", POLICY_ID),
            expectedPolicyVersion = requiredVersion(root, "expectedPolicyVersion"),
            expectedControlVersion = requiredVersion(root, "expectedControlVersion"),
        )
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

    private fun requiredNonnegativeVersion(
        root: JsonNode,
        field: String,
    ): Int {
        val value = root.get(field)
        if (value == null || !value.isIntegralNumber || !value.canConvertToInt()) invalid("/$field")
        return value.intValue().takeIf { it >= 0 } ?: invalid("/$field")
    }

    private fun requiredInt(
        root: JsonNode,
        field: String,
    ): Int {
        val value = root.get(field)
        if (value == null || !value.isIntegralNumber || !value.canConvertToInt()) invalid("/$field")
        return value.intValue()
    }

    private fun requiredLong(
        root: JsonNode,
        field: String,
    ): Long {
        val value = root.get(field)
        if (value == null || !value.isIntegralNumber || !value.canConvertToLong()) invalid("/$field")
        return value.longValue()
    }

    private fun requiredBoolean(
        root: JsonNode,
        field: String,
    ): Boolean {
        val value = root.get(field)
        if (value == null || !value.isBoolean) invalid("/$field")
        return value.booleanValue()
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
        val POLICY_V2_FIELDS = setOf("capitalLimitKrw", "stopLossBps", "takeProfitBps", "expectedVersion")
        val POLICY_V3_FIELDS =
            setOf(
                "capitalLimitKrw",
                "stopLossBps",
                "takeProfitBps",
                "maxHoldingSessions",
                "atrPeriod",
                "atrMultiplierMilli",
                "modelSellEnabled",
                "expectedVersion",
            )
        val ARM_V2_FIELDS = setOf("accountId", "policyId", "expectedPolicyVersion", "expectedControlVersion")
        val RUN_QUERY_FIELDS = setOf("size", "cursor")
        val BROKERAGE_MODES = setOf("KIS_MOCK", "INTERNAL_PAPER")
        val ACCOUNT_ID = Regex("^acct_[A-Za-z0-9_-]{8,96}$")
        val PRINCIPLE_ID = Regex("^prc_[A-Za-z0-9_-]{8,96}$")
        val STRATEGY_ID = Regex("^strategy_[A-Za-z0-9_-]{8,96}$")
        val POLICY_ID = Regex("^auto_pol_[0-9a-f]{32}$")
        val BASE64_URL = Regex("^[A-Za-z0-9_-]+$")
    }
}
