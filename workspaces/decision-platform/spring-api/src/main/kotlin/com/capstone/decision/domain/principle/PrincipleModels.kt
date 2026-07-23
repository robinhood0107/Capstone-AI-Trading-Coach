package com.capstone.decision.domain.principle

import java.math.BigDecimal
import java.time.OffsetDateTime
import java.util.UUID

// 외부 wire와 DB가 공유하는 opaque Principle ID이며 임의 문자열이 repository 경계를 넘지 못하게 한다.
@JvmInline
value class PrincipleId(
    val value: String,
) {
    init {
        require(PATTERN.matches(value)) { "Invalid Principle ID." }
    }

    companion object {
        private val PATTERN = Regex("^prc_[0-9a-f]{32}$")

        fun generate(): PrincipleId = PrincipleId("prc_${UUID.randomUUID().toString().replace("-", "")}")

        fun isValid(value: String): Boolean = PATTERN.matches(value)
    }
}

// version row ID는 공개 식별자가 아니지만 application과 JDBC 사이에서 문자열 혼용을 막는다.
@JvmInline
value class PrincipleVersionId(
    val value: String,
) {
    companion object {
        fun generate(): PrincipleVersionId = PrincipleVersionId("pvr_${UUID.randomUUID().toString().replace("-", "")}")
    }
}

@JvmInline
value class PrinciplePresetId(
    val value: String,
)

enum class PrincipleMode {
    GUIDE,
    STRICT,
}

enum class PrincipleStatus {
    ACTIVE,
    ARCHIVED,
}

// rule tuple은 canonical catalog가 검증한 값만 생성되며 BigDecimal로 금융 threshold의 scale을 보존한다.
data class PrincipleRule(
    val ruleId: String,
    val ruleType: String,
    val metric: String,
    val operator: String,
    val threshold: BigDecimal,
    val severity: String,
    val enabled: Boolean,
) {
    fun semanticallyEquals(other: PrincipleRule): Boolean =
        ruleId == other.ruleId &&
            ruleType == other.ruleType &&
            metric == other.metric &&
            operator == other.operator &&
            threshold.compareTo(other.threshold) == 0 &&
            severity == other.severity &&
            enabled == other.enabled
}

data class PrinciplePreset(
    val order: Int,
    val presetId: PrinciplePresetId,
    val nameKo: String,
    val nameEn: String,
    val descriptionKo: String,
    val descriptionEn: String,
    val mode: PrincipleMode,
    val defaultRules: List<PrincipleRule>,
)

data class PrincipleCurrent(
    val principleId: PrincipleId,
    val userId: String,
    val presetId: PrinciplePresetId,
    val title: String,
    val mode: PrincipleMode,
    val status: PrincipleStatus,
    val version: Int,
    val rules: List<PrincipleRule>,
    val createdAt: OffsetDateTime,
    val updatedAt: OffsetDateTime,
)

data class PrincipleSummary(
    val principleId: PrincipleId,
    val presetId: PrinciplePresetId,
    val title: String,
    val mode: PrincipleMode,
    val status: PrincipleStatus,
    val version: Int,
    val createdAt: OffsetDateTime,
    val updatedAt: OffsetDateTime,
)

data class PrincipleVersion(
    val principleId: PrincipleId,
    val version: Int,
    val presetId: PrinciplePresetId,
    val title: String,
    val mode: PrincipleMode,
    val status: PrincipleStatus,
    val rules: List<PrincipleRule>,
    val changedFields: List<String>,
    val createdAt: OffsetDateTime,
)

data class PrinciplePage<T>(
    val items: List<T>,
    val nextCursor: String?,
)

data class PrincipleViolation(
    val field: String,
    val reason: String,
)

class PrincipleValidationException(
    violations: List<PrincipleViolation>,
) : RuntimeException("Principle request validation failed.") {
    val violations: List<PrincipleViolation> =
        violations.sortedWith(compareBy(PrincipleViolation::field, PrincipleViolation::reason))
}

class PrincipleNotFoundException : RuntimeException("Principle was not found.")

class PrincipleConflictException(
    val expectedVersion: Int,
    val currentVersion: Int,
) : RuntimeException("Principle version conflict.")

class PrincipleVersionExhaustedException(
    val currentVersion: Int,
) : RuntimeException("Principle version was exhausted.")
