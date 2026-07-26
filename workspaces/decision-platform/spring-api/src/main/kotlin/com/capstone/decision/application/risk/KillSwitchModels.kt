package com.capstone.decision.application.risk

import com.capstone.decision.domain.risk.KillSwitchActorRole
import com.capstone.decision.domain.risk.KillSwitchReasonClass
import java.time.Instant

data class KillSwitchPublicState(
    val active: Boolean,
    val reasonClass: KillSwitchReasonClass,
    val changedAt: Instant,
)

data class KillSwitchGate(
    val active: Boolean,
    val generation: Long,
) {
    init {
        require(generation > 0)
    }
}

data class KillSwitchActor(
    val userId: String,
    val role: KillSwitchActorRole,
    val securityVersion: Long,
    val requestId: String,
) {
    init {
        require(userId.isNotBlank())
        require(securityVersion > 0)
        require(requestId.isNotBlank())
    }
}

data class KillSwitchMutationCommand(
    val actor: KillSwitchActor,
    val requestedActive: Boolean,
    val reasonClass: KillSwitchReasonClass,
    val changedAt: Instant,
)

data class KillSwitchMutationResult(
    val state: KillSwitchPublicState,
    val changed: Boolean,
    val previousActive: Boolean,
    val generation: Long,
    val invalidatedDecisionCount: Int,
)

class KillSwitchForbiddenException : RuntimeException("Kill Switch resume requires a current ADMIN.")

class KillSwitchUnauthorizedException : RuntimeException("Kill Switch actor is no longer authenticated.")

class KillSwitchConflictException : RuntimeException("Kill Switch generation changed concurrently.")

class KillSwitchBlockedException : RuntimeException("Kill Switch is active.")

class KillSwitchUnavailableException(
    cause: Throwable? = null,
) : RuntimeException("Kill Switch authority is unavailable.", cause)
