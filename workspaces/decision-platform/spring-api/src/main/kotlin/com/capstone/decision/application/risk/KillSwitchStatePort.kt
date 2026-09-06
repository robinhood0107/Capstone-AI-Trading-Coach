package com.capstone.decision.application.risk

interface KillSwitchQueryPort {
    fun readPublicState(): KillSwitchPublicState

    fun readEffectiveActive(actorUserId: String): Boolean = readPublicState().active
}

interface KillSwitchGatePort {
    fun readGate(): KillSwitchGate
}

interface KillSwitchMutationPort {
    /**
     * singleton lock, actor 재검증, CAS, invalidation, audit와 outbox를 한 DB transaction으로 수행한다.
     */
    fun mutate(command: KillSwitchMutationCommand): KillSwitchMutationResult
}
