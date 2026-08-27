package com.capstone.decision.application.journal

import java.time.OffsetDateTime

data class JournalLinks(
    val decisionId: String?,
    val backtestRunId: String?,
    val ragAnswerId: String?,
    val orderId: String?,
    val automationRunId: String?,
)

data class JournalProjection(
    val journalId: String,
    val ownerScope: String,
    val title: String,
    val content: String,
    val tags: List<String>,
    val links: JournalLinks,
    val version: Int,
    val createdAt: OffsetDateTime,
    val updatedAt: OffsetDateTime,
    val deletedAt: OffsetDateTime?,
)

data class CreateJournalCommand(
    val title: String,
    val content: String,
    val tags: List<String>,
    val links: JournalLinks,
)

data class ReplaceJournalCommand(
    val expectedVersion: Int,
    val title: String,
    val content: String,
    val tags: List<String>,
    val links: JournalLinks,
)

data class DeleteJournalCommand(
    val expectedVersion: Int,
)

data class JournalCursor(
    val updatedAt: OffsetDateTime,
    val journalId: String,
)

data class JournalPage(
    val items: List<JournalProjection>,
    val nextCursor: String?,
)

interface JournalRepository {
    fun create(
        ownerUserId: String,
        journalId: String,
        command: CreateJournalCommand,
        scopeHash: String,
        requestHash: String,
    ): JournalProjection

    fun replace(
        ownerUserId: String,
        journalId: String,
        command: ReplaceJournalCommand,
        scopeHash: String,
        requestHash: String,
    ): JournalProjection

    fun delete(
        ownerUserId: String,
        journalId: String,
        command: DeleteJournalCommand,
        scopeHash: String,
        requestHash: String,
    ): JournalProjection

    fun list(
        ownerUserId: String,
        limit: Int,
        after: JournalCursor?,
    ): List<JournalProjection>
}

class JournalConflictException : RuntimeException("Journal version conflict.")

class JournalIdempotencyConflictException : RuntimeException("Journal idempotency conflict.")

class JournalNotFoundException : RuntimeException("Journal was not found.")

class JournalAccessDeniedException(
    cause: Throwable? = null,
) : RuntimeException("Journal access was denied.", cause)

class JournalStorageException(
    cause: Throwable,
) : RuntimeException("Journal storage is unavailable.", cause)
