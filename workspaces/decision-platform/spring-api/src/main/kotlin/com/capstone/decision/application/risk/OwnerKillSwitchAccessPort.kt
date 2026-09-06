package com.capstone.decision.application.risk

import com.capstone.decision.application.security.AppPrincipal
import java.time.Instant

/** 인증된 owner의 상태와 변경만 제공한다. 저장소는 capability와 트랜잭션을 소유한다. */
interface OwnerKillSwitchAccessPort {
    fun access(
        actor: AppPrincipal,
        active: Boolean?,
        key: String?,
        requestId: String,
    ): OwnerStopSnapshot
}

data class OwnerStopSnapshot(
    val active: Boolean,
    val globalActive: Boolean,
    val effectiveActive: Boolean,
    val reasonClass: String,
    val changedAt: Instant,
    val globalGeneration: Long,
)
