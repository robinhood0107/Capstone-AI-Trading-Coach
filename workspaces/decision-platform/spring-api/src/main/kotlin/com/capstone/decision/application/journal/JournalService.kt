package com.capstone.decision.application.journal

import com.capstone.decision.application.security.OwnerWriteHashes
import org.springframework.stereotype.Service
import java.nio.charset.StandardCharsets
import java.time.OffsetDateTime
import java.util.Base64
import java.util.UUID

/** Journal write는 canonical command hash만 persistence에 넘기고 owner subject를 body에서 받지 않는다. */
@Service
class JournalService(
    private val repository: JournalRepository,
) {
    fun create(
        ownerUserId: String,
        rawIdempotencyKey: String,
        command: CreateJournalCommand,
    ): JournalProjection =
        repository.create(
            ownerUserId = ownerUserId,
            journalId = "jnl_" + compactUuid(),
            command = command,
            scopeHash = OwnerWriteHashes.scope(ownerUserId, rawIdempotencyKey),
            requestHash = requestHash("CREATE_JOURNAL", ownerUserId, null, command.expectedFields()),
        )

    fun replace(
        ownerUserId: String,
        journalId: String,
        rawIdempotencyKey: String,
        command: ReplaceJournalCommand,
    ): JournalProjection =
        repository.replace(
            ownerUserId = ownerUserId,
            journalId = journalId,
            command = command,
            scopeHash = OwnerWriteHashes.scope(ownerUserId, rawIdempotencyKey),
            requestHash = requestHash("PATCH_JOURNAL", ownerUserId, journalId, command.expectedFields()),
        )

    fun delete(
        ownerUserId: String,
        journalId: String,
        rawIdempotencyKey: String,
        command: DeleteJournalCommand,
    ): JournalProjection =
        repository.delete(
            ownerUserId = ownerUserId,
            journalId = journalId,
            command = command,
            scopeHash = OwnerWriteHashes.scope(ownerUserId, rawIdempotencyKey),
            requestHash =
                OwnerWriteHashes.request(
                    "DELETE_JOURNAL",
                    ownerUserId,
                    journalId,
                    command.expectedVersion.toString(),
                ),
        )

    fun list(
        ownerUserId: String,
        size: Int,
        cursor: String?,
    ): JournalPage {
        val after = cursor?.let { decodeCursor(ownerUserId, it) }
        val fetched = repository.list(ownerUserId, size + 1, after)
        val items = fetched.take(size)
        return JournalPage(
            items = items,
            nextCursor =
                if (fetched.size > size) {
                    items.last().let { encodeCursor(ownerUserId, it.updatedAt, it.journalId) }
                } else {
                    null
                },
        )
    }

    private fun CreateJournalCommand.expectedFields(): List<String?> =
        listOf(title, content, tags.joinToString("\u0000"), *links.fields().toTypedArray())

    private fun ReplaceJournalCommand.expectedFields(): List<String?> =
        listOf(expectedVersion.toString(), title, content, tags.joinToString("\u0000"), *links.fields().toTypedArray())

    private fun JournalLinks.fields(): List<String?> = listOf(decisionId, backtestRunId, ragAnswerId, orderId, automationRunId)

    private fun requestHash(
        operation: String,
        ownerUserId: String,
        journalId: String?,
        fields: List<String?>,
    ): String = OwnerWriteHashes.request(operation, ownerUserId, journalId, *fields.toTypedArray())

    private fun encodeCursor(
        ownerUserId: String,
        updatedAt: OffsetDateTime,
        journalId: String,
    ): String {
        val payload = "${OwnerWriteHashes.ownerScope(ownerUserId)}\n$updatedAt\n$journalId"
        return Base64.getUrlEncoder().withoutPadding().encodeToString(payload.toByteArray(StandardCharsets.UTF_8))
    }

    private fun decodeCursor(
        ownerUserId: String,
        cursor: String,
    ): JournalCursor {
        val values =
            runCatching { String(Base64.getUrlDecoder().decode(cursor), StandardCharsets.UTF_8).split('\n') }
                .getOrElse { throw IllegalArgumentException("Invalid Journal cursor.") }
        if (
            values.size != 3 ||
            values[0] != OwnerWriteHashes.ownerScope(ownerUserId) ||
            !values[2].matches(JOURNAL_ID)
        ) {
            throw IllegalArgumentException("Invalid Journal cursor.")
        }
        val updatedAt =
            runCatching { OffsetDateTime.parse(values[1]) }.getOrNull()
                ?: throw IllegalArgumentException("Invalid Journal cursor.")
        return JournalCursor(updatedAt, values[2])
    }

    private fun compactUuid(): String = UUID.randomUUID().toString().replace("-", "")

    private companion object {
        val JOURNAL_ID = Regex("^jnl_[A-Za-z0-9_-]{8,96}$")
    }
}
