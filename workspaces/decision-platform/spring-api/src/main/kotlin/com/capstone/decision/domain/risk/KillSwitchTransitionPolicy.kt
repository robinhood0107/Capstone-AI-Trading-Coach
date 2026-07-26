package com.capstone.decision.domain.risk

sealed interface KillSwitchTransition {
    data class Applied(
        val nextActive: Boolean,
        val reasonClass: KillSwitchReasonClass,
    ) : KillSwitchTransition

    data object NoOp : KillSwitchTransition

    data object ResumeRequiresAdmin : KillSwitchTransition
}

// 안전 정지는 USER에게도 열되 재가동 요청은 현재 값과 무관하게 ADMIN만 허용한다.
class KillSwitchTransitionPolicy {
    fun decide(
        current: KillSwitchState,
        requestedActive: Boolean,
        actorRole: KillSwitchActorRole,
        rawReason: String? = null,
    ): KillSwitchTransition {
        if (!requestedActive && actorRole != KillSwitchActorRole.ADMIN) {
            return KillSwitchTransition.ResumeRequiresAdmin
        }
        if (current.active == requestedActive) {
            return KillSwitchTransition.NoOp
        }
        return KillSwitchTransition.Applied(
            nextActive = requestedActive,
            reasonClass =
                KillSwitchReasonClass.forManualChange(
                    active = requestedActive,
                    actorRole = actorRole,
                    rawReason = rawReason,
                ),
        )
    }
}
