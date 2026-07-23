package com.capstone.decision.application.risk

import com.capstone.decision.domain.risk.PortfolioSource
import java.math.BigDecimal

enum class CatalogRuleOwnership {
    PUBLIC_PRINCIPLE,
    SYSTEM_MANAGED,
}

enum class CatalogEvidenceCriticality {
    HARD,
    OPTIONAL,
    CONFIGURABLE,
    MIXED,
}

enum class CatalogApplicability {
    PRINCIPLE_RULE_ENABLED,
    ALWAYS,
    SOURCE_APPLICABLE,
    MODEL_REQUESTED,
    ORDER_INSTRUMENT_APPLICABLE,
    NOT_APPLICABLE_V1,
}

data class CatalogEvaluationRule(
    val order: Int,
    val ruleId: String,
    val metric: String,
    val operator: String?,
    val defaultThreshold: BigDecimal?,
    val scale: Int?,
    val defaultSeverity: String?,
    val ownership: CatalogRuleOwnership,
    val evidenceCriticality: CatalogEvidenceCriticality,
    val executionKind: String,
    val applicability: CatalogApplicability,
)

// application은 canonical resource의 typed view만 소비하고 classpath/Jackson은 infrastructure에 맡긴다.
interface SystemRuleContract {
    val catalogVersion: Int
    val readinessPolicyVersion: String
    val rules: List<CatalogEvaluationRule>

    fun storageSource(source: PortfolioSource): String
}
