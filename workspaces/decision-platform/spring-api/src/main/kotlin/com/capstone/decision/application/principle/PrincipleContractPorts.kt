package com.capstone.decision.application.principle

import com.capstone.decision.domain.principle.EvidenceRequirement
import java.math.BigDecimal
import java.time.OffsetDateTime

// API와 application은 canonical catalog의 값만 소비하고 classpath/Jackson 구현은 infrastructure에 맡긴다.
interface PrincipleContract {
    val presetIds: Set<String>
    val modes: Set<String>
    val statuses: Set<String>
    val disclaimerKo: String
    val disclaimerEn: String
    val titleMinCodePoints: Int
    val titleMaxCodePoints: Int
    val rulesMinItems: Int
    val rulesMaxItems: Int
    val pageDefault: Int
    val pageMin: Int
    val pageMax: Int
    val cursorMaxChars: Int
    val cursorTtlSeconds: Long
    val maxVersion: Int
    val evidenceRequirements: Set<EvidenceRequirement>
    val ruleDefinitions: Map<String, CatalogRuleDefinition>
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
    val evidenceRequirements: Set<EvidenceRequirement>,
    val defaultEvidenceRequirement: EvidenceRequirement,
)

// 커서 port는 route/subject/resource binding을 구현 세부사항과 분리하고 raw userId 노출을 금지한다.
interface PrincipleCursorPort {
    fun encodeOwner(
        userId: String,
        size: Int,
        sort: String,
        updatedAt: OffsetDateTime,
        principleId: String,
    ): String

    fun decodeOwner(
        cursor: String,
        userId: String,
        requestedSize: Int?,
        requestedSort: String?,
    ): OwnerCursor

    fun encodeHistory(
        userId: String,
        principleId: String,
        size: Int,
        sort: String,
        version: Int,
    ): String

    fun decodeHistory(
        cursor: String,
        userId: String,
        principleId: String,
        requestedSize: Int?,
        requestedSort: String?,
    ): HistoryCursor
}

data class OwnerCursor(
    val size: Int,
    val sort: String,
    val updatedAt: OffsetDateTime,
    val principleId: String,
)

data class HistoryCursor(
    val size: Int,
    val sort: String,
    val version: Int,
)

class InvalidPrincipleCursorException : RuntimeException("Invalid Principle cursor.")
