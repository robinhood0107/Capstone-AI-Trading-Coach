package com.capstone.decision.application.automation

import com.capstone.decision.application.security.OwnerWriteHashes
import org.springframework.stereotype.Service
import java.nio.charset.StandardCharsets
import java.time.OffsetDateTime
import java.util.Base64

/** Owner subject만 repository로 전달하고 raw idempotency key는 hash 이후 즉시 폐기한다. */
@Service
class AutomationService(
    private val repository: AutomationRepository,
) {
    fun status(ownerUserId: String): AutomationControlProjection = repository.status(ownerUserId)

    fun statusV2(ownerUserId: String): AutomationStatusV2Projection = repository.statusV2(ownerUserId)

    fun putPolicyV2(
        ownerUserId: String,
        rawIdempotencyKey: String,
        command: PutAutomationPolicyV2Command,
    ): AutomationPolicyV2Projection =
        repository.putPolicyV2(
            ownerUserId = ownerUserId,
            command = command,
            scopeHash = OwnerWriteHashes.scope(ownerUserId, rawIdempotencyKey),
            requestHash =
                OwnerWriteHashes.request(
                    "PUT_AUTOMATION_POLICY",
                    ownerUserId,
                    command.capitalLimitKrw.toString(),
                    command.stopLossBps.toString(),
                    command.takeProfitBps.toString(),
                    command.expectedVersion.toString(),
                ),
        )

    fun armV2(
        ownerUserId: String,
        rawIdempotencyKey: String,
        command: ArmAutomationV2Command,
    ): AutomationStatusV2Projection =
        repository.armV2(
            ownerUserId = ownerUserId,
            command = command,
            scopeHash = OwnerWriteHashes.scope(ownerUserId, rawIdempotencyKey),
            requestHash =
                OwnerWriteHashes.request(
                    "ARM_AUTOMATION",
                    ownerUserId,
                    command.accountId,
                    command.policyId,
                    command.expectedPolicyVersion.toString(),
                    command.expectedControlVersion.toString(),
                ),
        )

    fun arm(
        ownerUserId: String,
        rawIdempotencyKey: String,
        command: ArmAutomationCommand,
    ): AutomationControlProjection =
        repository.arm(
            ownerUserId = ownerUserId,
            command = command,
            scopeHash = OwnerWriteHashes.scope(ownerUserId, rawIdempotencyKey),
            requestHash =
                OwnerWriteHashes.request(
                    "ARM_AUTOMATION",
                    ownerUserId,
                    command.brokerageMode,
                    command.accountId,
                    command.principleId,
                    command.strategyId,
                    command.expectedVersion.toString(),
                ),
        )

    fun disarm(
        ownerUserId: String,
        rawIdempotencyKey: String,
        command: DisarmAutomationCommand,
    ): AutomationControlProjection =
        repository.disarm(
            ownerUserId = ownerUserId,
            command = command,
            scopeHash = OwnerWriteHashes.scope(ownerUserId, rawIdempotencyKey),
            requestHash =
                OwnerWriteHashes.request(
                    "DISARM_AUTOMATION",
                    ownerUserId,
                    command.expectedVersion.toString(),
                ),
        )

    fun listRuns(
        ownerUserId: String,
        size: Int,
        cursor: String?,
    ): AutomationRunPage {
        val after = cursor?.let { decodeCursor(ownerUserId, it) }
        val fetched = repository.listRuns(ownerUserId, size + 1, after)
        val items = fetched.take(size)
        return AutomationRunPage(
            items = items,
            nextCursor =
                if (fetched.size > size) {
                    items.last().let { encodeCursor(ownerUserId, it.updatedAt, it.runId) }
                } else {
                    null
                },
        )
    }

    fun listRunsV2(
        ownerUserId: String,
        size: Int,
        cursor: String?,
    ): AutomationRunV2Page {
        val after = cursor?.let { decodeCursor(ownerUserId, it) }
        val fetched = repository.listRunsV2(ownerUserId, size + 1, after)
        val items = fetched.take(size)
        return AutomationRunV2Page(
            items = items,
            nextCursor =
                if (fetched.size > size) {
                    items.last().let { encodeCursor(ownerUserId, it.updatedAt, it.runId) }
                } else {
                    null
                },
        )
    }

    fun listPositionsV2(ownerUserId: String): AutomationPositionV2Page =
        AutomationPositionV2Page(repository.listPositionsV2(ownerUserId), null)

    private fun encodeCursor(
        ownerUserId: String,
        updatedAt: OffsetDateTime,
        runId: String,
    ): String {
        val payload = "${OwnerWriteHashes.ownerScope(ownerUserId)}\n$updatedAt\n$runId"
        return Base64.getUrlEncoder().withoutPadding().encodeToString(payload.toByteArray(StandardCharsets.UTF_8))
    }

    private fun decodeCursor(
        ownerUserId: String,
        cursor: String,
    ): AutomationRunCursor {
        val values =
            runCatching {
                String(Base64.getUrlDecoder().decode(cursor), StandardCharsets.UTF_8).split('\n')
            }.getOrElse { throw IllegalArgumentException("Invalid automation cursor.") }
        if (
            values.size != 3 ||
            values[0] != OwnerWriteHashes.ownerScope(ownerUserId) ||
            !values[2].matches(RUN_ID)
        ) {
            throw IllegalArgumentException("Invalid automation cursor.")
        }
        val updatedAt =
            runCatching { OffsetDateTime.parse(values[1]) }.getOrNull()
                ?: throw IllegalArgumentException("Invalid automation cursor.")
        return AutomationRunCursor(updatedAt, values[2])
    }

    private companion object {
        val RUN_ID = Regex("^auto_run_[A-Za-z0-9_-]{8,96}$")
    }
}
