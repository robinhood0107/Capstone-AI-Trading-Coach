package com.capstone.decision.infrastructure.risk

import com.capstone.decision.application.risk.CatalogApplicability
import com.capstone.decision.application.risk.CatalogEvaluationRule
import com.capstone.decision.application.risk.CatalogEvidenceCriticality
import com.capstone.decision.application.risk.CatalogRuleOwnership
import com.capstone.decision.application.risk.SystemRuleContract
import com.capstone.decision.domain.risk.EvaluationBounds
import com.capstone.decision.domain.risk.MetricKey
import com.capstone.decision.domain.risk.PortfolioSource
import org.springframework.core.io.ClassPathResource
import org.springframework.stereotype.Component
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import java.security.MessageDigest

// 8 public + 6 system disposition과 bounds를 생성된 canonical resource에서 직접 읽어 drift를 fail-fast한다.
@Component
class ClasspathSystemRuleCatalog(
    objectMapper: ObjectMapper,
) : SystemRuleContract {
    final override val catalogVersion: Int
    final override val readinessPolicyVersion: String
    final override val rules: List<CatalogEvaluationRule>
    private val storageMappings: Map<PortfolioSource, String>

    init {
        val catalogBytes = ClassPathResource(CATALOG_RESOURCE).inputStream.use { it.readBytes() }
        check(sha256(catalogBytes) == CATALOG_SHA256) {
            "S2.2 canonical catalog digest mismatch."
        }
        val root = objectMapper.readTree(catalogBytes)
        check(root.path("catalogId").stringValue() == "s2-2-system-rule-catalog")
        catalogVersion = root.path("catalogVersion").intValue()
        check(catalogVersion == 1)
        readinessPolicyVersion = requiredText(root, "readinessPolicyVersion")
        validateBounds(root.path("bounds"))

        storageMappings =
            root
                .path("portfolioPolicy")
                .path("storageMapping")
                .properties()
                .associate { (source, storage) -> PortfolioSource.valueOf(source) to storage.stringValue() }
        check(storageMappings.keys == PortfolioSource.entries.toSet())
        check(storageMappings.getValue(PortfolioSource.KIS_MOCK) == "KIS_MOCK")
        check(storageMappings.getValue(PortfolioSource.INTERNAL_PAPER) == "PAPER")
        check(root.path("portfolioPolicy").path("noAutomaticFallback").booleanValue())

        rules =
            root
                .path("rules")
                .values()
                .map(::toRule)
                .sortedBy(CatalogEvaluationRule::order)
                .toList()
        check(rules.size == 14)
        check(rules.map(CatalogEvaluationRule::order) == (1..14).toList())
        check(rules.map(CatalogEvaluationRule::ruleId).distinct().size == 14)
        check(rules.count { it.executionKind == "THRESHOLD" } == 12)
        check(rules.count { it.executionKind == "READINESS" } == 1)
        check(rules.count { it.executionKind == "NOT_APPLICABLE" } == 1)
        rules.filter { it.executionKind == "THRESHOLD" }.forEach { rule ->
            check(MetricKey.fromWire(rule.metric).unit.name == canonicalUnit(root, rule.ruleId))
        }
    }

    override fun storageSource(source: PortfolioSource): String = storageMappings.getValue(source)

    private fun toRule(node: JsonNode): CatalogEvaluationRule =
        CatalogEvaluationRule(
            order = node.path("order").intValue(),
            ruleId = requiredText(node, "ruleId"),
            metric = requiredText(node, "metric"),
            operator = node.path("operator").takeUnless(JsonNode::isNull)?.stringValue(),
            defaultThreshold = node.path("defaultThreshold").takeUnless(JsonNode::isNull)?.decimalValue(),
            scale = node.path("scale").takeUnless(JsonNode::isNull)?.intValue(),
            defaultSeverity = node.path("defaultSeverity").takeUnless(JsonNode::isNull)?.stringValue(),
            ownership = CatalogRuleOwnership.valueOf(requiredText(node, "ownership")),
            evidenceCriticality = CatalogEvidenceCriticality.valueOf(requiredText(node, "evidenceCriticality")),
            executionKind = requiredText(node, "executionKind"),
            applicability = CatalogApplicability.valueOf(requiredText(node, "applicability")),
        )

    private fun validateBounds(bounds: JsonNode) {
        check(bounds.path("requestMaxBytes").intValue() == EvaluationBounds.MAX_REQUEST_BYTES)
        check(bounds.path("responseMaxBytes").intValue() == EvaluationBounds.MAX_RESPONSE_BYTES)
        check(bounds.path("positionMaxItems").intValue() == EvaluationBounds.MAX_POSITIONS)
        check(bounds.path("violationMaxItems").intValue() == EvaluationBounds.MAX_VIOLATIONS)
        check(bounds.path("issueMaxItems").intValue() == EvaluationBounds.MAX_ISSUES)
        check(bounds.path("warningMaxItems").intValue() == EvaluationBounds.MAX_WARNINGS)
        check(bounds.path("abstentionMaxItems").intValue() == EvaluationBounds.MAX_ABSTENTIONS)
        check(bounds.path("disclosureEventMaxItems").intValue() == EvaluationBounds.MAX_DISCLOSURE_EVENTS)
        check(bounds.path("sourceRefMaxItems").intValue() == EvaluationBounds.MAX_SOURCE_REFS)
        check(bounds.path("idMaxChars").intValue() == EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        check(bounds.path("codeMaxChars").intValue() == EvaluationBounds.MAX_ID_OR_CODE_CHARS)
        check(bounds.path("messageMaxChars").intValue() == EvaluationBounds.MAX_SAFE_MESSAGE_CHARS)
        check(bounds.path("sourceRefPattern").stringValue() == EvaluationBounds.SANITIZED_SHA256_PATTERN)
        check(bounds.path("perPortLogicalCallMax").intValue() == EvaluationBounds.MAX_LOGICAL_CALLS_PER_PORT)
        check(bounds.path("concurrencyMax").intValue() == EvaluationBounds.MAX_CONCURRENCY)
        check(bounds.path("sourceDeadlineMillis").longValue() == EvaluationBounds.SOURCE_DEADLINE.toMillis())
        check(bounds.path("totalDeadlineMillis").longValue() == EvaluationBounds.EVALUATION_DEADLINE.toMillis())
    }

    private fun canonicalUnit(
        root: JsonNode,
        ruleId: String,
    ): String {
        val unit =
            root
                .path("rules")
                .values()
                .single { it.path("ruleId").stringValue() == ruleId }
                .path("unit")
                .stringValue()
        return unit
    }

    private fun requiredText(
        node: JsonNode,
        field: String,
    ): String {
        val value = node.path(field)
        check(value.isString && value.stringValue().isNotBlank()) {
            "S2.2 catalog text field is invalid."
        }
        return value.stringValue()
    }

    companion object {
        private const val CATALOG_RESOURCE = "contracts/s2-2-system-rule-catalog.v1.json"
        private const val CATALOG_SHA256 = "a4714ee9ce3031199b9067919b15931fb42e106857da5f8d8ad7a95bafa8ad7b"

        private fun sha256(bytes: ByteArray): String =
            MessageDigest
                .getInstance("SHA-256")
                .digest(bytes)
                .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
    }
}
