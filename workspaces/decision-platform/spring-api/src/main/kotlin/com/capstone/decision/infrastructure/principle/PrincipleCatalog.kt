package com.capstone.decision.infrastructure.principle

import org.springframework.core.io.ClassPathResource
import org.springframework.stereotype.Component
import tools.jackson.databind.ObjectMapper
import java.math.BigDecimal

// runtime 검증은 사람이 옮겨 적은 상수가 아니라 amendment가 고정한 catalog resource를 직접 읽는다.
@Component
class PrincipleCatalog(
    objectMapper: ObjectMapper,
) {
    final val presetIds: Set<String>
    final val modes: Set<String>
    final val statuses: Set<String>
    final val disclaimerKo: String
    final val disclaimerEn: String
    final val titleMinCodePoints: Int
    final val titleMaxCodePoints: Int
    final val rulesMinItems: Int
    final val rulesMaxItems: Int
    final val pageDefault: Int
    final val pageMin: Int
    final val pageMax: Int
    final val cursorMaxChars: Int
    final val cursorTtlSeconds: Long
    final val maxVersion: Int
    final val ruleDefinitions: Map<String, CatalogRuleDefinition>

    init {
        val root =
            ClassPathResource(CATALOG_RESOURCE).inputStream.use(objectMapper::readTree)
        check(root.path("contractId").stringValue() == "s2-1-principle-contract")
        check(root.path("contractVersion").intValue() == 1)

        presetIds =
            root
                .path("presets")
                .values()
                .map { it.path("presetId").stringValue() }
                .toSet()
        modes =
            root
                .path("enums")
                .path("modes")
                .values()
                .map { it.stringValue() }
                .toSet()
        statuses =
            root
                .path("enums")
                .path("statuses")
                .values()
                .map { it.stringValue() }
                .toSet()
        disclaimerKo = root.path("disclaimer").path("ko").stringValue()
        disclaimerEn = root.path("disclaimer").path("en").stringValue()

        val limits = root.path("limits")
        titleMinCodePoints = limits.path("titleMinCodePoints").intValue()
        titleMaxCodePoints = limits.path("titleMaxCodePoints").intValue()
        rulesMinItems = limits.path("rulesMinItems").intValue()
        rulesMaxItems = limits.path("rulesMaxItems").intValue()
        pageDefault = limits.path("pageDefault").intValue()
        pageMin = limits.path("pageMin").intValue()
        pageMax = limits.path("pageMax").intValue()
        cursorMaxChars = limits.path("cursorMaxChars").intValue()
        cursorTtlSeconds = limits.path("cursorTtlSeconds").longValue()
        maxVersion = limits.path("maxVersion").intValue()

        ruleDefinitions =
            root
                .path("ruleDefinitions")
                .values()
                .map { definition ->
                    val threshold = definition.path("thresholdSchema")
                    CatalogRuleDefinition(
                        order = definition.path("order").intValue(),
                        ruleId = definition.path("ruleId").stringValue(),
                        ruleType = definition.path("ruleType").stringValue(),
                        metric = definition.path("metric").stringValue(),
                        operator = definition.path("operator").stringValue(),
                        jsonType = threshold.path("jsonType").stringValue(),
                        minimum = threshold.path("minimum").decimalValue(),
                        maximum = threshold.path("maximum").decimalValue(),
                        maxNormalizedScale = threshold.path("maxNormalizedScale").intValue(),
                        enabledSeverities =
                            definition
                                .path("enabledSeverities")
                                .values()
                                .map { it.stringValue() }
                                .toSet(),
                        disabledSeverity = definition.path("disabledSeverity").stringValue(),
                    )
                }.associateBy(CatalogRuleDefinition::ruleId)
        check(ruleDefinitions.size == rulesMaxItems)
    }

    companion object {
        private const val CATALOG_RESOURCE = "contracts/s2-1-principle-contract.v1.json"
    }
}

data class CatalogRuleDefinition(
    val order: Int,
    val ruleId: String,
    val ruleType: String,
    val metric: String,
    val operator: String,
    val jsonType: String,
    val minimum: BigDecimal,
    val maximum: BigDecimal,
    val maxNormalizedScale: Int,
    val enabledSeverities: Set<String>,
    val disabledSeverity: String,
)
