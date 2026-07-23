package com.capstone.decision.api.principle

import com.capstone.decision.domain.principle.PrincipleCurrent
import com.capstone.decision.domain.principle.PrinciplePreset
import com.capstone.decision.domain.principle.PrincipleRule
import com.capstone.decision.domain.principle.PrincipleSummary
import com.capstone.decision.domain.principle.PrincipleVersion
import io.swagger.v3.oas.annotations.media.ArraySchema
import io.swagger.v3.oas.annotations.media.Schema
import java.math.BigDecimal
import java.time.OffsetDateTime

@Schema(name = "PrincipleCreateRequest", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class PrincipleCreateRequestSchema(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["conservative", "balanced", "aggressive"])
    val presetId: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minLength = 1, maxLength = 120)
    val title: String,
    @field:Schema(allowableValues = ["GUIDE", "STRICT"])
    val mode: String? = null,
    @field:ArraySchema(minItems = 1, maxItems = 8)
    val rules: List<PrincipleRuleResponse>? = null,
)

@Schema(name = "PrincipleUpdateRequest", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class PrincipleUpdateRequestSchema(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1", maximum = "2147483647")
    val expectedVersion: Int,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minLength = 1, maxLength = 120)
    val title: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["GUIDE", "STRICT"])
    val mode: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["ACTIVE", "ARCHIVED"])
    val status: String,
    @field:ArraySchema(
        minItems = 1,
        maxItems = 8,
        arraySchema = Schema(requiredMode = Schema.RequiredMode.REQUIRED),
    )
    val rules: List<PrincipleRuleResponse>,
)

@Schema(name = "PrincipleRule", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class PrincipleRuleResponse(
    val ruleId: String,
    val ruleType: String,
    val metric: String,
    val operator: String,
    val threshold: BigDecimal,
    val severity: String,
    val enabled: Boolean,
)

@Schema(name = "PrinciplePreset")
data class PrinciplePresetResponse(
    val order: Int,
    val presetId: String,
    val nameKo: String,
    val nameEn: String,
    val descriptionKo: String,
    val descriptionEn: String,
    val mode: String,
    val defaultRules: List<PrincipleRuleResponse>,
)

data class PrincipleDisclaimerResponse(
    val ko: String,
    val en: String,
)

@Schema(name = "PrinciplePresetListData")
data class PrinciplePresetListData(
    val disclaimer: PrincipleDisclaimerResponse,
    val items: List<PrinciplePresetResponse>,
)

@Schema(name = "PrincipleCurrent")
data class PrincipleCurrentResponse(
    val principleId: String,
    val presetId: String,
    val title: String,
    val mode: String,
    val status: String,
    val version: Int,
    val rules: List<PrincipleRuleResponse>,
    val createdAt: OffsetDateTime,
    val updatedAt: OffsetDateTime,
)

@Schema(name = "PrincipleSummary")
data class PrincipleSummaryResponse(
    val principleId: String,
    val presetId: String,
    val title: String,
    val mode: String,
    val status: String,
    val version: Int,
    val createdAt: OffsetDateTime,
    val updatedAt: OffsetDateTime,
)

@Schema(name = "PrincipleOwnerListData")
data class PrincipleOwnerListData(
    val items: List<PrincipleSummaryResponse>,
    val nextCursor: String?,
)

@Schema(name = "PrincipleVersion")
data class PrincipleVersionResponse(
    val principleId: String,
    val version: Int,
    val presetId: String,
    val title: String,
    val mode: String,
    val status: String,
    val rules: List<PrincipleRuleResponse>,
    val changedFields: List<String>,
    val createdAt: OffsetDateTime,
)

@Schema(name = "PrincipleHistoryData")
data class PrincipleHistoryData(
    val items: List<PrincipleVersionResponse>,
    val nextCursor: String?,
)

fun PrincipleRule.toResponse(): PrincipleRuleResponse =
    PrincipleRuleResponse(
        ruleId = ruleId,
        ruleType = ruleType,
        metric = metric,
        operator = operator,
        threshold = threshold,
        severity = severity,
        enabled = enabled,
    )

fun PrinciplePreset.toResponse(): PrinciplePresetResponse =
    PrinciplePresetResponse(
        order = order,
        presetId = presetId.value,
        nameKo = nameKo,
        nameEn = nameEn,
        descriptionKo = descriptionKo,
        descriptionEn = descriptionEn,
        mode = mode.name,
        defaultRules = defaultRules.map(PrincipleRule::toResponse),
    )

fun PrincipleCurrent.toResponse(): PrincipleCurrentResponse =
    PrincipleCurrentResponse(
        principleId = principleId.value,
        presetId = presetId.value,
        title = title,
        mode = mode.name,
        status = status.name,
        version = version,
        rules = rules.map(PrincipleRule::toResponse),
        createdAt = createdAt,
        updatedAt = updatedAt,
    )

fun PrincipleSummary.toResponse(): PrincipleSummaryResponse =
    PrincipleSummaryResponse(
        principleId = principleId.value,
        presetId = presetId.value,
        title = title,
        mode = mode.name,
        status = status.name,
        version = version,
        createdAt = createdAt,
        updatedAt = updatedAt,
    )

fun PrincipleVersion.toResponse(): PrincipleVersionResponse =
    PrincipleVersionResponse(
        principleId = principleId.value,
        version = version,
        presetId = presetId.value,
        title = title,
        mode = mode.name,
        status = status.name,
        rules = rules.map(PrincipleRule::toResponse),
        changedFields = changedFields,
        createdAt = createdAt,
    )
