package com.capstone.decision.application.risk

import com.capstone.decision.domain.risk.KillSwitchActorRole
import java.time.Duration

/**
 * application은 관측 backend를 모르고, commit 이후의 bounded 결과만 low-cardinality 신호로 전달한다.
 */
interface RiskObservationPort {
    fun recordKillSwitchChanged(
        result: KillSwitchMutationResult,
        actorRole: KillSwitchActorRole,
        requestId: String,
    )

    fun recordPortfolioQuery(duration: Duration)

    companion object {
        val NONE: RiskObservationPort =
            object : RiskObservationPort {
                override fun recordKillSwitchChanged(
                    result: KillSwitchMutationResult,
                    actorRole: KillSwitchActorRole,
                    requestId: String,
                ) = Unit

                override fun recordPortfolioQuery(duration: Duration) = Unit
            }
    }
}
