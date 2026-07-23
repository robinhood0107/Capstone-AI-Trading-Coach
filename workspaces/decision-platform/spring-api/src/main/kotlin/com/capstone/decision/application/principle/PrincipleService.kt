package com.capstone.decision.application.principle

import com.capstone.decision.domain.principle.PrincipleConflictException
import com.capstone.decision.domain.principle.PrincipleCurrent
import com.capstone.decision.domain.principle.PrincipleId
import com.capstone.decision.domain.principle.PrincipleMode
import com.capstone.decision.domain.principle.PrincipleNotFoundException
import com.capstone.decision.domain.principle.PrinciplePage
import com.capstone.decision.domain.principle.PrinciplePreset
import com.capstone.decision.domain.principle.PrinciplePresetId
import com.capstone.decision.domain.principle.PrincipleRule
import com.capstone.decision.domain.principle.PrincipleStatus
import com.capstone.decision.domain.principle.PrincipleSummary
import com.capstone.decision.domain.principle.PrincipleValidationException
import com.capstone.decision.domain.principle.PrincipleVersion
import com.capstone.decision.domain.principle.PrincipleVersionExhaustedException
import com.capstone.decision.domain.principle.PrincipleVersionId
import com.capstone.decision.domain.principle.PrincipleViolation
import com.capstone.decision.infrastructure.principle.HistoryCursor
import com.capstone.decision.infrastructure.principle.InvalidPrincipleCursorException
import com.capstone.decision.infrastructure.principle.OwnerCursor
import com.capstone.decision.infrastructure.principle.PrincipleCatalog
import com.capstone.decision.infrastructure.principle.PrincipleCursorCodec
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional
import java.time.Clock
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.time.temporal.ChronoUnit

data class PrincipleActor(
    val userId: String,
    val role: String,
    val requestId: String,
)

data class CreatePrincipleCommand(
    val presetId: PrinciplePresetId,
    val title: String,
    val mode: PrincipleMode?,
    val rules: List<PrincipleRule>?,
)

data class UpdatePrincipleCommand(
    val expectedVersion: Int,
    val title: String,
    val mode: PrincipleMode,
    val status: PrincipleStatus,
    val rules: List<PrincipleRule>,
)

data class OwnerPageQuery(
    val cursor: String?,
    val size: Int?,
    val sort: OwnerSort?,
)

data class HistoryPageQuery(
    val cursor: String?,
    val size: Int?,
    val sort: HistorySort?,
)

enum class OwnerSort {
    UPDATED_AT_DESC,
    UPDATED_AT_ASC,
}

enum class HistorySort {
    VERSION_DESC,
    VERSION_ASC,
}

// application 계층은 owner predicate가 없는 조회를 노출하지 않아 IDOR 방어를 repository 계약에 고정한다.
interface PrincipleRepository {
    fun listActivePresets(): List<PrinciplePreset>

    fun findActivePreset(presetId: PrinciplePresetId): PrinciplePreset?

    fun insertPrinciple(current: PrincipleCurrent)

    fun insertVersion(
        versionId: PrincipleVersionId,
        version: PrincipleVersion,
        createdBy: String,
    )

    fun insertAudit(
        actor: PrincipleActor,
        action: String,
        principleId: PrincipleId,
        newVersion: Int,
        changedFields: List<String>,
        createdAt: OffsetDateTime,
    )

    fun findOwnedCurrent(
        userId: String,
        principleId: PrincipleId,
    ): PrincipleCurrent?

    fun listOwned(
        userId: String,
        size: Int,
        sort: OwnerSort,
        after: OwnerCursor?,
    ): List<PrincipleSummary>

    fun updateOwnedCas(
        userId: String,
        principleId: PrincipleId,
        expectedVersion: Int,
        title: String,
        mode: PrincipleMode,
        status: PrincipleStatus,
        updatedAt: OffsetDateTime,
    ): Int?

    fun listOwnedVersions(
        userId: String,
        principleId: PrincipleId,
        size: Int,
        sort: HistorySort,
        after: HistoryCursor?,
    ): List<PrincipleVersion>
}

// create/update의 row, immutable snapshot, audit는 같은 transaction에서만 함께 확정된다.
@Service
class PrincipleService(
    private val repository: PrincipleRepository,
    private val catalog: PrincipleCatalog,
    private val cursorCodec: PrincipleCursorCodec,
    private val principleClock: Clock,
) {
    @Transactional(readOnly = true)
    fun listPresets(): List<PrinciplePreset> = repository.listActivePresets()

    @Transactional
    fun create(
        actor: PrincipleActor,
        command: CreatePrincipleCommand,
    ): PrincipleCurrent {
        val preset =
            repository.findActivePreset(command.presetId)
                ?: throw PrincipleValidationException(
                    listOf(PrincipleViolation("/presetId", "UNAVAILABLE")),
                )
        val now = now()
        val rules = (command.rules ?: preset.defaultRules).map(PrincipleRule::copy)
        val current =
            PrincipleCurrent(
                principleId = PrincipleId.generate(),
                userId = actor.userId,
                presetId = preset.presetId,
                title = command.title,
                mode = command.mode ?: preset.mode,
                status = PrincipleStatus.ACTIVE,
                version = 1,
                rules = rules,
                createdAt = now,
                updatedAt = now,
            )
        val changedFields = INITIAL_CHANGED_FIELDS
        repository.insertPrinciple(current)
        repository.insertVersion(
            versionId = PrincipleVersionId.generate(),
            version = current.toVersion(changedFields, now),
            createdBy = actor.userId,
        )
        repository.insertAudit(
            actor = actor,
            action = "PRINCIPLE_CREATED",
            principleId = current.principleId,
            newVersion = current.version,
            changedFields = changedFields,
            createdAt = now,
        )
        return current
    }

    @Transactional(readOnly = true)
    fun get(
        userId: String,
        principleId: PrincipleId,
    ): PrincipleCurrent =
        repository.findOwnedCurrent(userId, principleId)
            ?: throw PrincipleNotFoundException()

    @Transactional(readOnly = true)
    fun list(
        userId: String,
        query: OwnerPageQuery,
    ): PrinciplePage<PrincipleSummary> {
        val decoded = decodeOwnerCursor(query, userId)
        val size = decoded?.size ?: query.size ?: catalog.pageDefault
        val sort = decoded?.sort?.let(OwnerSort::valueOf) ?: query.sort ?: OwnerSort.UPDATED_AT_DESC
        val fetched =
            repository.listOwned(
                userId = userId,
                size = size + 1,
                sort = sort,
                after = decoded,
            )
        val items = fetched.take(size)
        val nextCursor =
            if (fetched.size > size) {
                items.last().let { last ->
                    cursorCodec.encodeOwner(
                        userId = userId,
                        size = size,
                        sort = sort.name,
                        updatedAt = last.updatedAt,
                        principleId = last.principleId.value,
                    )
                }
            } else {
                null
            }
        return PrinciplePage(items, nextCursor)
    }

    @Transactional
    fun update(
        actor: PrincipleActor,
        principleId: PrincipleId,
        command: UpdatePrincipleCommand,
    ): PrincipleCurrent {
        val current =
            repository.findOwnedCurrent(actor.userId, principleId)
                ?: throw PrincipleNotFoundException()
        if (current.version != command.expectedVersion) {
            throw PrincipleConflictException(command.expectedVersion, current.version)
        }

        val changedFields = changedFields(current, command)
        if (changedFields.isEmpty()) {
            return current
        }

        val now = now()
        val newVersion =
            repository.updateOwnedCas(
                userId = actor.userId,
                principleId = principleId,
                expectedVersion = command.expectedVersion,
                title = command.title,
                mode = command.mode,
                status = command.status,
                updatedAt = now,
            ) ?: diagnoseFailedUpdate(actor.userId, principleId, command.expectedVersion)
        val updated =
            current.copy(
                title = command.title,
                mode = command.mode,
                status = command.status,
                version = newVersion,
                rules = command.rules,
                updatedAt = now,
            )
        repository.insertVersion(
            versionId = PrincipleVersionId.generate(),
            version = updated.toVersion(changedFields, now),
            createdBy = actor.userId,
        )
        repository.insertAudit(
            actor = actor,
            action = auditAction(current.status, updated.status),
            principleId = principleId,
            newVersion = newVersion,
            changedFields = changedFields,
            createdAt = now,
        )
        return updated
    }

    @Transactional(readOnly = true)
    fun listVersions(
        userId: String,
        principleId: PrincipleId,
        query: HistoryPageQuery,
    ): PrinciplePage<PrincipleVersion> {
        // 빈 page에서도 cross-owner와 owned cursor exhaustion을 구분하기 위해 owner-scoped 현재 row만 확인한다.
        repository.findOwnedCurrent(userId, principleId)
            ?: throw PrincipleNotFoundException()
        val decoded = decodeHistoryCursor(query, userId, principleId)
        val size = decoded?.size ?: query.size ?: catalog.pageDefault
        val sort = decoded?.sort?.let(HistorySort::valueOf) ?: query.sort ?: HistorySort.VERSION_DESC
        val fetched =
            repository.listOwnedVersions(
                userId = userId,
                principleId = principleId,
                size = size + 1,
                sort = sort,
                after = decoded,
            )
        val items = fetched.take(size)
        val nextCursor =
            if (fetched.size > size) {
                cursorCodec.encodeHistory(
                    userId = userId,
                    principleId = principleId.value,
                    size = size,
                    sort = sort.name,
                    version = items.last().version,
                )
            } else {
                null
            }
        return PrinciplePage(items, nextCursor)
    }

    private fun decodeOwnerCursor(
        query: OwnerPageQuery,
        userId: String,
    ): OwnerCursor? =
        query.cursor?.let { cursor ->
            try {
                cursorCodec.decodeOwner(cursor, userId, query.size, query.sort?.name)
            } catch (_: InvalidPrincipleCursorException) {
                throw invalidCursor()
            }
        }

    private fun decodeHistoryCursor(
        query: HistoryPageQuery,
        userId: String,
        principleId: PrincipleId,
    ): HistoryCursor? =
        query.cursor?.let { cursor ->
            try {
                cursorCodec.decodeHistory(cursor, userId, principleId.value, query.size, query.sort?.name)
            } catch (_: InvalidPrincipleCursorException) {
                throw invalidCursor()
            }
        }

    private fun diagnoseFailedUpdate(
        userId: String,
        principleId: PrincipleId,
        expectedVersion: Int,
    ): Nothing {
        val latest =
            repository.findOwnedCurrent(userId, principleId)
                ?: throw PrincipleNotFoundException()
        if (latest.version == catalog.maxVersion && expectedVersion == catalog.maxVersion) {
            throw PrincipleVersionExhaustedException(latest.version)
        }
        throw PrincipleConflictException(expectedVersion, latest.version)
    }

    private fun changedFields(
        current: PrincipleCurrent,
        command: UpdatePrincipleCommand,
    ): List<String> =
        buildList {
            if (current.title != command.title) add("title")
            if (current.mode != command.mode) add("mode")
            if (current.status != command.status) add("status")
            if (!rulesEqual(current.rules, command.rules)) add("rules")
        }

    private fun rulesEqual(
        current: List<PrincipleRule>,
        replacement: List<PrincipleRule>,
    ): Boolean =
        current.size == replacement.size &&
            current.zip(replacement).all { (left, right) -> left.semanticallyEquals(right) }

    private fun PrincipleCurrent.toVersion(
        changedFields: List<String>,
        createdAt: OffsetDateTime,
    ): PrincipleVersion =
        PrincipleVersion(
            principleId = principleId,
            version = version,
            presetId = presetId,
            title = title,
            mode = mode,
            status = status,
            rules = rules,
            changedFields = changedFields,
            createdAt = createdAt,
        )

    private fun auditAction(
        previous: PrincipleStatus,
        current: PrincipleStatus,
    ): String =
        when {
            previous == PrincipleStatus.ACTIVE && current == PrincipleStatus.ARCHIVED -> "PRINCIPLE_ARCHIVED"
            previous == PrincipleStatus.ARCHIVED && current == PrincipleStatus.ACTIVE -> "PRINCIPLE_REACTIVATED"
            else -> "PRINCIPLE_UPDATED"
        }

    private fun now(): OffsetDateTime =
        OffsetDateTime
            .ofInstant(principleClock.instant(), KST)
            .truncatedTo(ChronoUnit.MICROS)

    private fun invalidCursor(): PrincipleValidationException =
        PrincipleValidationException(listOf(PrincipleViolation("/query/cursor", "INVALID_CURSOR")))

    companion object {
        private val KST = ZoneOffset.ofHours(9)
        private val INITIAL_CHANGED_FIELDS = listOf("presetId", "title", "mode", "status", "rules")
    }
}
