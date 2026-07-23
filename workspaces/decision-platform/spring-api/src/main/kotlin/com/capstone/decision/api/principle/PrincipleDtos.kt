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
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val ruleId: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val ruleType: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val metric: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val operator: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val threshold: BigDecimal,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val severity: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val enabled: Boolean,
)

@Schema(name = "PrinciplePreset", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class PrinciplePresetResponse(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1", maximum = "3")
    val order: Int,
    @field:Schema(
        requiredMode = Schema.RequiredMode.REQUIRED,
        allowableValues = ["conservative", "balanced", "aggressive"],
    )
    val presetId: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minLength = 1)
    val nameKo: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minLength = 1)
    val nameEn: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minLength = 1)
    val descriptionKo: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minLength = 1)
    val descriptionEn: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["GUIDE", "STRICT"])
    val mode: String,
    @field:ArraySchema(
        minItems = 8,
        maxItems = 8,
        arraySchema = Schema(requiredMode = Schema.RequiredMode.REQUIRED),
    )
    val defaultRules: List<PrincipleRuleResponse>,
)

@Schema(name = "PrincipleDisclaimer", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class PrincipleDisclaimerResponse(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minLength = 1)
    val ko: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minLength = 1)
    val en: String,
)

@Schema(name = "PrinciplePresetListData", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class PrinciplePresetListData(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val disclaimer: PrincipleDisclaimerResponse,
    @field:ArraySchema(
        minItems = 3,
        maxItems = 3,
        arraySchema = Schema(requiredMode = Schema.RequiredMode.REQUIRED),
    )
    val items: List<PrinciplePresetResponse>,
)

@Schema(name = "PrincipleCurrent", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class PrincipleCurrentResponse(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, pattern = "^prc_[0-9a-f]{32}$")
    val principleId: String,
    @field:Schema(
        requiredMode = Schema.RequiredMode.REQUIRED,
        allowableValues = ["conservative", "balanced", "aggressive"],
    )
    val presetId: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minLength = 1, maxLength = 120)
    val title: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["GUIDE", "STRICT"])
    val mode: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["ACTIVE", "ARCHIVED"])
    val status: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1", maximum = "2147483647")
    val version: Int,
    @field:ArraySchema(
        minItems = 1,
        maxItems = 8,
        arraySchema = Schema(requiredMode = Schema.RequiredMode.REQUIRED),
    )
    val rules: List<PrincipleRuleResponse>,
    @field:Schema(
        requiredMode = Schema.RequiredMode.REQUIRED,
        pattern = "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,9})?\\+09:00$",
    )
    val createdAt: OffsetDateTime,
    @field:Schema(
        requiredMode = Schema.RequiredMode.REQUIRED,
        pattern = "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,9})?\\+09:00$",
    )
    val updatedAt: OffsetDateTime,
)

@Schema(name = "PrincipleSummary", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class PrincipleSummaryResponse(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, pattern = "^prc_[0-9a-f]{32}$")
    val principleId: String,
    @field:Schema(
        requiredMode = Schema.RequiredMode.REQUIRED,
        allowableValues = ["conservative", "balanced", "aggressive"],
    )
    val presetId: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minLength = 1, maxLength = 120)
    val title: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["GUIDE", "STRICT"])
    val mode: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["ACTIVE", "ARCHIVED"])
    val status: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1", maximum = "2147483647")
    val version: Int,
    @field:Schema(
        requiredMode = Schema.RequiredMode.REQUIRED,
        pattern = "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,9})?\\+09:00$",
    )
    val createdAt: OffsetDateTime,
    @field:Schema(
        requiredMode = Schema.RequiredMode.REQUIRED,
        pattern = "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,9})?\\+09:00$",
    )
    val updatedAt: OffsetDateTime,
)

@Schema(name = "PrincipleOwnerListData", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class PrincipleOwnerListData(
    @field:ArraySchema(arraySchema = Schema(requiredMode = Schema.RequiredMode.REQUIRED))
    val items: List<PrincipleSummaryResponse>,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, nullable = true, maxLength = 2048)
    val nextCursor: String?,
)

@Schema(name = "PrincipleVersion", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class PrincipleVersionResponse(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, pattern = "^prc_[0-9a-f]{32}$")
    val principleId: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1", maximum = "2147483647")
    val version: Int,
    @field:Schema(
        requiredMode = Schema.RequiredMode.REQUIRED,
        allowableValues = ["conservative", "balanced", "aggressive"],
    )
    val presetId: String,
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
    @field:ArraySchema(
        minItems = 1,
        maxItems = 5,
        arraySchema = Schema(requiredMode = Schema.RequiredMode.REQUIRED),
    )
    val changedFields: List<String>,
    @field:Schema(
        requiredMode = Schema.RequiredMode.REQUIRED,
        pattern = "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,9})?\\+09:00$",
    )
    val createdAt: OffsetDateTime,
)

@Schema(name = "PrincipleHistoryData", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class PrincipleHistoryData(
    @field:ArraySchema(arraySchema = Schema(requiredMode = Schema.RequiredMode.REQUIRED))
    val items: List<PrincipleVersionResponse>,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, nullable = true, maxLength = 2048)
    val nextCursor: String?,
)

@Schema(name = "PrincipleValidationErrorResponse")
class PrincipleValidationErrorResponseSchema

@Schema(name = "PrincipleUnauthorizedErrorResponse")
class PrincipleUnauthorizedErrorResponseSchema

@Schema(name = "PrincipleForbiddenErrorResponse")
class PrincipleForbiddenErrorResponseSchema

@Schema(name = "PrincipleNotFoundErrorResponse")
class PrincipleNotFoundErrorResponseSchema

@Schema(name = "PrincipleConflictErrorResponse")
class PrincipleConflictErrorResponseSchema

@Schema(name = "PrincipleVersionExhaustedErrorResponse")
class PrincipleVersionExhaustedErrorResponseSchema

@Schema(name = "PrinciplePayloadTooLargeErrorResponse")
class PrinciplePayloadTooLargeErrorResponseSchema

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
