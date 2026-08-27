package com.capstone.decision.api.journal

import com.capstone.decision.application.journal.JournalLinks
import com.capstone.decision.application.journal.JournalProjection
import io.swagger.v3.oas.annotations.media.ArraySchema
import io.swagger.v3.oas.annotations.media.Schema
import java.time.OffsetDateTime

@Schema(name = "JournalLinks", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class JournalLinksResponse(
    @field:Schema(nullable = true, pattern = "^dec_[A-Za-z0-9_-]{8,96}$")
    val decisionId: String?,
    @field:Schema(nullable = true, pattern = "^run_[A-Za-z0-9_-]{8,96}$")
    val backtestRunId: String?,
    @field:Schema(nullable = true, pattern = "^rag_[A-Za-z0-9_-]{8,96}$")
    val ragAnswerId: String?,
    @field:Schema(nullable = true, pattern = "^ord_[A-Za-z0-9_-]{8,96}$")
    val orderId: String?,
    @field:Schema(nullable = true, pattern = "^auto_run_[A-Za-z0-9_-]{8,96}$")
    val automationRunId: String?,
)

@Schema(name = "Journal", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class JournalResponse(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = ["journal.v1"])
    val contractId: String = "journal.v1",
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, pattern = "^jnl_[A-Za-z0-9_-]{8,96}$")
    val journalId: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, pattern = "^(?!0{64}$)[0-9a-f]{64}$")
    val ownerScope: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minLength = 1, maxLength = 120)
    val title: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minLength = 1, maxLength = 8192)
    val content: String,
    @field:ArraySchema(
        maxItems = 20,
        uniqueItems = true,
        arraySchema = Schema(requiredMode = Schema.RequiredMode.REQUIRED),
        schema = Schema(minLength = 1, maxLength = 32),
    )
    val tags: List<String>,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val links: JournalLinksResponse,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1")
    val version: Int,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time")
    val createdAt: OffsetDateTime,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time")
    val updatedAt: OffsetDateTime,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, nullable = true, format = "date-time")
    val deletedAt: OffsetDateTime?,
)

@Schema(name = "JournalPage", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
data class JournalPageResponse(
    @field:ArraySchema(arraySchema = Schema(requiredMode = Schema.RequiredMode.REQUIRED))
    val items: List<JournalResponse>,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, nullable = true, maxLength = 512)
    val nextCursor: String?,
)

@Schema(name = "JournalWriteLinks", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
class JournalWriteLinksSchema(
    @field:Schema(nullable = true, pattern = "^dec_[A-Za-z0-9_-]{8,96}$")
    val decisionId: String?,
    @field:Schema(nullable = true, pattern = "^run_[A-Za-z0-9_-]{8,96}$")
    val backtestRunId: String?,
    @field:Schema(nullable = true, pattern = "^rag_[A-Za-z0-9_-]{8,96}$")
    val ragAnswerId: String?,
    @field:Schema(nullable = true, pattern = "^ord_[A-Za-z0-9_-]{8,96}$")
    val orderId: String?,
    @field:Schema(nullable = true, pattern = "^auto_run_[A-Za-z0-9_-]{8,96}$")
    val automationRunId: String?,
)

@Schema(name = "CreateJournalRequest", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
class CreateJournalRequestSchema(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minLength = 1, maxLength = 120)
    val title: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minLength = 1, maxLength = 8192)
    val content: String,
    @field:ArraySchema(maxItems = 20, uniqueItems = true, arraySchema = Schema(requiredMode = Schema.RequiredMode.REQUIRED))
    val tags: List<String>,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val links: JournalWriteLinksSchema,
)

@Schema(name = "ReplaceJournalRequest", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
class ReplaceJournalRequestSchema(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1")
    val expectedVersion: Int,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minLength = 1, maxLength = 120)
    val title: String,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minLength = 1, maxLength = 8192)
    val content: String,
    @field:ArraySchema(maxItems = 20, uniqueItems = true, arraySchema = Schema(requiredMode = Schema.RequiredMode.REQUIRED))
    val tags: List<String>,
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED)
    val links: JournalWriteLinksSchema,
)

@Schema(name = "DeleteJournalRequest", additionalProperties = Schema.AdditionalPropertiesValue.FALSE)
class DeleteJournalRequestSchema(
    @field:Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1")
    val expectedVersion: Int,
)

@Schema(name = "P1JournalErrorResponse")
class P1JournalErrorResponseSchema

fun JournalProjection.toResponse(): JournalResponse =
    JournalResponse(
        journalId = journalId,
        ownerScope = ownerScope,
        title = title,
        content = content,
        tags = tags,
        links = links.toResponse(),
        version = version,
        createdAt = createdAt,
        updatedAt = updatedAt,
        deletedAt = deletedAt,
    )

private fun JournalLinks.toResponse(): JournalLinksResponse =
    JournalLinksResponse(decisionId, backtestRunId, ragAnswerId, orderId, automationRunId)
