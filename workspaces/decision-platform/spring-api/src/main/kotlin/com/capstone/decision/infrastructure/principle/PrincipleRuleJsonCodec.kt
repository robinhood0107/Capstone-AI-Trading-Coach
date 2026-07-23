package com.capstone.decision.infrastructure.principle

import com.capstone.decision.application.principle.CatalogRuleDefinition
import com.capstone.decision.application.principle.PrincipleContract
import com.capstone.decision.domain.principle.EvidenceRequirement
import com.capstone.decision.domain.principle.PrincipleRule
import org.springframework.stereotype.Component
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper

// immutable version JSON의 기존 필드는 그대로 두고, 읽기 경계에서만 승인된 legacy requiredness를 보충한다.
@Component
class PrincipleRuleJsonCodec(
    private val objectMapper: ObjectMapper,
    private val contract: PrincipleContract,
) {
    /**
     * 새 Principle version은 evidence requiredness를 항상 명시해 이후 평가가 암묵적 기본값에 의존하지 않게 한다.
     */
    fun encode(rules: List<PrincipleRule>): String =
        objectMapper.writeValueAsString(
            rules
                .sortedBy { rule -> contract.ruleDefinitions.getValue(rule.ruleId).order }
                .map { rule ->
                    linkedMapOf(
                        "ruleId" to rule.ruleId,
                        "ruleType" to rule.ruleType,
                        "metric" to rule.metric,
                        "operator" to rule.operator,
                        "threshold" to rule.threshold,
                        "severity" to rule.severity,
                        "enabled" to rule.enabled,
                        "evidenceRequirement" to rule.evidenceRequirement.name,
                    )
                },
        )

    /**
     * field가 없는 legacy snapshot만 결정적으로 정규화하며, 명시됐지만 잘못된 값은 조용히 추론하지 않는다.
     */
    fun decode(raw: String): List<PrincipleRule> {
        val root = objectMapper.readTree(raw)
        check(root.isArray && root.size() in contract.rulesMinItems..contract.rulesMaxItems) {
            "Principle rules JSON violated the catalog item bounds."
        }
        val seen = mutableSetOf<String>()
        return root
            .values()
            .map { node ->
                check(node.isObject) { "Principle rules JSON item must be an object." }
                val fields = node.propertyNames().asSequence().toSet()
                check(fields == LEGACY_RULE_FIELDS || fields == CURRENT_RULE_FIELDS) {
                    "Principle rules JSON contained a missing or unknown field."
                }
                val ruleId = requiredText(node, "ruleId")
                check(seen.add(ruleId)) { "Principle rules JSON contained a duplicate rule ID." }
                val definition =
                    contract.ruleDefinitions[ruleId]
                        ?: error("Principle rules JSON contained an unknown rule ID.")
                node.toRule(definition)
            }.sortedBy { rule -> contract.ruleDefinitions.getValue(rule.ruleId).order }
    }

    private fun JsonNode.toRule(definition: CatalogRuleDefinition): PrincipleRule {
        val ruleType = requiredText(this, "ruleType")
        val metric = requiredText(this, "metric")
        val operator = requiredText(this, "operator")
        val severity = requiredText(this, "severity")
        val enabledNode = path("enabled")
        val thresholdNode = path("threshold")
        check(enabledNode.isBoolean && thresholdNode.isNumber) {
            "Principle rules JSON contained an invalid scalar type."
        }
        check(
            ruleType == definition.ruleType &&
                metric == definition.metric &&
                operator == definition.operator,
        ) { "Principle rules JSON drifted from the canonical tuple." }
        val threshold = thresholdNode.decimalValue()
        check(
            threshold >= definition.minimum &&
                threshold <= definition.maximum &&
                threshold.stripTrailingZeros().scale().coerceAtLeast(0) <= definition.maxNormalizedScale,
        ) { "Principle rules JSON contained an out-of-contract threshold." }
        val enabled = enabledNode.booleanValue()
        check(
            if (enabled) {
                severity in definition.enabledSeverities
            } else {
                severity == definition.disabledSeverity
            },
        ) { "Principle rules JSON contained an invalid severity combination." }
        val requirement = evidenceRequirement(this, definition, enabled)
        return PrincipleRule(
            ruleId = definition.ruleId,
            ruleType = definition.ruleType,
            metric = definition.metric,
            operator = definition.operator,
            threshold = threshold,
            severity = severity,
            enabled = enabled,
            evidenceRequirement = requirement,
        )
    }

    private fun evidenceRequirement(
        node: JsonNode,
        definition: CatalogRuleDefinition,
        enabled: Boolean,
    ): EvidenceRequirement {
        val explicit = node.get("evidenceRequirement")
        if (explicit != null) {
            check(explicit.isString) {
                "Principle rules JSON contained an invalid evidence requirement type."
            }
            val requirement =
                EvidenceRequirement.entries.firstOrNull { it.name == explicit.stringValue() }
                    ?: error("Principle rules JSON contained an unknown evidence requirement.")
            check(requirement in definition.evidenceRequirements) {
                "Principle rules JSON contained an invalid evidence requirement combination."
            }
            return requirement
        }

        // 승인된 legacy 정책: 활성 optional-evidence rule은 REQUIRED, 비활성은 catalog 기본값이다.
        return if (enabled && EvidenceRequirement.REQUIRED in definition.evidenceRequirements) {
            EvidenceRequirement.REQUIRED
        } else {
            definition.defaultEvidenceRequirement
        }
    }

    private fun requiredText(
        node: JsonNode,
        field: String,
    ): String {
        val value = node.path(field)
        check(value.isString) { "Principle rules JSON contained an invalid text field." }
        return value.stringValue()
    }

    private companion object {
        val LEGACY_RULE_FIELDS =
            setOf(
                "ruleId",
                "ruleType",
                "metric",
                "operator",
                "threshold",
                "severity",
                "enabled",
            )
        val CURRENT_RULE_FIELDS = LEGACY_RULE_FIELDS + "evidenceRequirement"
    }
}
