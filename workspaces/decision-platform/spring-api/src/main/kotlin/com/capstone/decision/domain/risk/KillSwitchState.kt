package com.capstone.decision.domain.risk

import java.time.Instant

/**
 * DB singleton의 판단 계약이며 actor/request 식별자는 사용자 projection에 포함하지 않는다.
 */
data class KillSwitchState(
    val active: Boolean,
    val reasonClass: KillSwitchReasonClass,
    val generation: Long,
    val changedAt: Instant,
) {
    init {
        require(generation > 0)
        require(
            if (active) {
                reasonClass !in setOf(KillSwitchReasonClass.ADMIN_RESUME, KillSwitchReasonClass.INITIAL_STATE)
            } else {
                reasonClass in setOf(KillSwitchReasonClass.ADMIN_RESUME, KillSwitchReasonClass.INITIAL_STATE)
            },
        )
    }

    fun next(
        active: Boolean,
        reasonClass: KillSwitchReasonClass,
        changedAt: Instant,
    ): KillSwitchState {
        require(active != this.active)
        require(!changedAt.isBefore(this.changedAt))
        return KillSwitchState(
            active = active,
            reasonClass = reasonClass,
            generation = Math.addExact(generation, 1L),
            changedAt = changedAt,
        )
    }
}
