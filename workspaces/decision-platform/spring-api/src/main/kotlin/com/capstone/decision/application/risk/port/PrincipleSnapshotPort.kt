package com.capstone.decision.application.risk.port

import com.capstone.decision.domain.principle.PrincipleId
import com.capstone.decision.domain.principle.PrincipleMode
import com.capstone.decision.domain.principle.PrincipleRule
import com.capstone.decision.domain.principle.PrincipleVersionId

data class ActivePrincipleSnapshot(
    val principleId: PrincipleId,
    val principleVersionId: PrincipleVersionId,
    val version: Int,
    val mode: PrincipleMode,
    val rules: List<PrincipleRule>,
) {
    init {
        require(version > 0)
        require(rules.isNotEmpty())
    }
}

// 조회 statement 자체가 JWT actor의 owner predicate와 ACTIVE 상태를 강제해야 한다.
interface PrincipleSnapshotPort {
    /** 내부 자동운용 claim과 owner에 결속된 immutable 원칙만 읽는다. */
    fun findAutomationOwned(
        actorUserId: String,
        principleId: PrincipleId,
        runId: String,
        claimHash: String,
    ): ActivePrincipleSnapshot? = null

    fun findActiveOwned(
        actorUserId: String,
        principleId: PrincipleId,
    ): ActivePrincipleSnapshot?
}
