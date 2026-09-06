package com.capstone.decision.domain.risk

sealed interface KillSwitchTransition {
    data class Applied(
        val nextActive: Boolean,
        val reasonClass: KillSwitchReasonClass,
    ) : KillSwitchTransition

    data object NoOp : KillSwitchTransition

    data object ResumeRequiresAdmin : KillSwitchTransition
}

// 전역 중지는 정지·해제 모두 ADMIN만 허용한다. 개인 중지는 별도 owner 권위가 소유한다.
class KillSwitchTransitionPolicy {
    fun decide(
        current: KillSwitchState,
        requestedActive: Boolean,
        actorRole: KillSwitchActorRole,
        rawReason: String? = null,
    ): KillSwitchTransition {
        if (actorRole != KillSwitchActorRole.ADMIN) {
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
