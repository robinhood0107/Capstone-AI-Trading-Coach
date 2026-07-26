package com.capstone.decision.api.risk

import com.capstone.decision.application.risk.RiskFieldViolation
import com.capstone.decision.application.risk.RiskValidationException
import com.capstone.decision.domain.risk.KillSwitchReasonClass
import jakarta.servlet.http.HttpServletRequest
import org.springframework.stereotype.Component
import tools.jackson.core.JacksonException
import tools.jackson.core.StreamReadConstraints
import tools.jackson.core.StreamReadFeature
import tools.jackson.core.json.JsonFactory
import tools.jackson.databind.JsonNode
import tools.jackson.databind.json.JsonMapper
import java.text.Normalizer

data class KillSwitchChangeRequest(
    val active: Boolean,
    val reason: String?,
)

// coercion 전에 duplicate/unknown/type/bound를 닫고 자유 서술 값은 오류 응답에 반사하지 않는다.
@Component
class RiskRequestParser {
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
                            .maxTokenCount(16)
                            .maxStringLength(KillSwitchReasonClass.MAX_REASON_CHARS)
                            .maxNameLength(64)
                            .build(),
                    ).enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
                    .build(),
            ).build()

    fun parseKillSwitchChange(body: String): KillSwitchChangeRequest {
        val root = parseObject(body)
        val violations = mutableListOf<RiskFieldViolation>()
        root.properties().forEach { (name, _) ->
            if (name !in FIELDS) {
                violations.add(RiskFieldViolation("/${escape(name)}", "UNKNOWN_FIELD"))
            }
        }
        val activeNode = root.get("active")
        val active =
            if (activeNode == null) {
                violations.add(RiskFieldViolation("/active", "REQUIRED"))
                null
            } else if (!activeNode.isBoolean) {
                violations.add(RiskFieldViolation("/active", "INVALID_FORMAT"))
                null
            } else {
                activeNode.booleanValue()
            }
        val reason = parseReason(root.get("reason"), violations)
        throwIfInvalid(violations)
        return KillSwitchChangeRequest(requireNotNull(active), reason)
    }

    fun requireIdempotencyKey(value: String?) {
        if (value == null || value.length !in 16..128 || !IDEMPOTENCY_KEY.matches(value)) {
            throw RiskValidationException(
                listOf(RiskFieldViolation("/headers/X-Idempotency-Key", "INVALID_FORMAT")),
            )
        }
    }

    fun requireNoQuery(request: HttpServletRequest) {
        throwIfInvalid(
            request.parameterMap.keys.map { name ->
                RiskFieldViolation("/query/${escape(name)}", "UNKNOWN_FIELD")
            },
        )
    }

    private fun parseReason(
        node: JsonNode?,
        violations: MutableList<RiskFieldViolation>,
    ): String? {
        if (node == null) {
            return null
        }
        if (!node.isString) {
            violations.add(RiskFieldViolation("/reason", "INVALID_FORMAT"))
            return null
        }
        val normalized = Normalizer.normalize(node.stringValue(), Normalizer.Form.NFC)
        val codePointLength = normalized.codePointCount(0, normalized.length)
        if (
            normalized.isBlank() ||
            codePointLength !in 1..KillSwitchReasonClass.MAX_REASON_CHARS ||
            normalized.codePoints().anyMatch { codePoint ->
                val type = Character.getType(codePoint)
                Character.isISOControl(codePoint) ||
                    type == Character.FORMAT.toInt()
            }
        ) {
            violations.add(RiskFieldViolation("/reason", "INVALID_FORMAT"))
            return null
        }
        if (runCatching { KillSwitchReasonClass.validateManualReason(normalized) }.isFailure) {
            violations.add(RiskFieldViolation("/reason", "INVALID_FORMAT"))
            return null
        }
        return normalized
    }

    private fun parseObject(body: String): JsonNode {
        val root =
            try {
                strictMapper.readTree(body)
            } catch (_: JacksonException) {
                null
            } catch (_: IllegalArgumentException) {
                null
            }
        if (root == null || !root.isObject) {
            throw RiskValidationException(listOf(RiskFieldViolation("/", "INVALID_FORMAT")))
        }
        return root
    }

    private fun throwIfInvalid(violations: List<RiskFieldViolation>) {
        if (violations.isNotEmpty()) {
            throw RiskValidationException(violations)
        }
    }

    private fun escape(value: String): String = value.replace("~", "~0").replace("/", "~1")

    private companion object {
        const val MAX_DOCUMENT_BYTES: Long = 1_048_576
        val FIELDS = setOf("active", "reason")
        val IDEMPOTENCY_KEY = Regex("^[A-Za-z0-9._:-]+$")
    }
}
