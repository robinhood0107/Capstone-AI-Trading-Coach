package com.capstone.decision.infrastructure.principle

import com.capstone.decision.application.principle.CatalogRuleDefinition
import com.capstone.decision.application.principle.PrincipleContract
import com.capstone.decision.domain.principle.EvidenceRequirement
import org.springframework.core.io.ClassPathResource
import org.springframework.stereotype.Component
import tools.jackson.databind.ObjectMapper

// runtime 검증은 사람이 옮겨 적은 상수가 아니라 amendment가 고정한 catalog resource를 직접 읽는다.
@Component
class PrincipleCatalog(
    objectMapper: ObjectMapper,
) : PrincipleContract {
    final override val presetIds: Set<String>
    final override val modes: Set<String>
    final override val statuses: Set<String>
    final override val disclaimerKo: String
    final override val disclaimerEn: String
    final override val titleMinCodePoints: Int
    final override val titleMaxCodePoints: Int
    final override val rulesMinItems: Int
    final override val rulesMaxItems: Int
    final override val pageDefault: Int
    final override val pageMin: Int
    final override val pageMax: Int
    final override val cursorMaxChars: Int
    final override val cursorTtlSeconds: Long
    final override val maxVersion: Int
    final override val evidenceRequirements: Set<EvidenceRequirement>
    final override val ruleDefinitions: Map<String, CatalogRuleDefinition>

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
        evidenceRequirements =
            root
                .path("enums")
                .path("evidenceRequirements")
                .values()
                .map { EvidenceRequirement.valueOf(it.stringValue()) }
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
                        evidenceRequirements =
                            definition
                                .path("evidenceRequirements")
                                .values()
                                .map { EvidenceRequirement.valueOf(it.stringValue()) }
                                .toSet(),
                        defaultEvidenceRequirement =
                            EvidenceRequirement.valueOf(
                                definition.path("defaultEvidenceRequirement").stringValue(),
                            ),
                    )
                }.associateBy(CatalogRuleDefinition::ruleId)
        check(ruleDefinitions.size == rulesMaxItems)
        check(evidenceRequirements == EvidenceRequirement.entries.toSet())
        check(
            ruleDefinitions.values.all { definition ->
                definition.evidenceRequirements.isNotEmpty() &&
                    definition.defaultEvidenceRequirement in definition.evidenceRequirements
            },
        )
    }

    companion object {
        private const val CATALOG_RESOURCE = "contracts/s2-1-principle-contract.v1.json"
    }
}
