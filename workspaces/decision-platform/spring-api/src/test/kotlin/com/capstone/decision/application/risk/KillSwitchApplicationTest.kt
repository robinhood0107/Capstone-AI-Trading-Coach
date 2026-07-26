package com.capstone.decision.application.risk

import com.capstone.decision.domain.risk.KillSwitchActorRole
import com.capstone.decision.domain.risk.KillSwitchReasonClass
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import java.time.Instant

class KillSwitchApplicationTest {
    @Test
    fun `USER resume fails before any mutation port call`() {
        val port = RecordingPort()
        val service = KillSwitchService(port, port)

        assertThrows(KillSwitchForbiddenException::class.java) {
            service.change(actor(KillSwitchActorRole.USER), active = false, rawReason = null)
        }
        assertEquals(0, port.mutationCount)
    }

    @Test
    fun `manual free text is discarded before persistence and only enum is passed`() {
        val port = RecordingPort()
        val service = KillSwitchService(port, port)

        val result = service.change(actor(KillSwitchActorRole.USER), active = true, rawReason = "안전 정지")

        assertEquals(KillSwitchReasonClass.USER_MANUAL_STOP, port.lastCommand?.reasonClass)
        assertFalse(result.state.active)
    }

    @Test
    fun `guard reads every call blocks active and fails closed on source error`() {
        var calls = 0
        val port =
            object : KillSwitchGatePort {
                override fun readGate(): KillSwitchGate {
                    calls += 1
                    return KillSwitchGate(active = calls == 2, generation = calls.toLong())
                }
            }
        val guard = KillSwitchGuard(port)

        assertEquals(1, guard.check().generation)
        assertThrows(KillSwitchBlockedException::class.java) { guard.check() }
        assertEquals(2, calls)
        assertThrows(KillSwitchUnavailableException::class.java) {
            KillSwitchGuard(
                object : KillSwitchGatePort {
                    override fun readGate(): KillSwitchGate = error("database unavailable")
                },
            ).check()
        }
    }

    private fun actor(role: KillSwitchActorRole): KillSwitchActor =
        KillSwitchActor(
            userId = "usr_test",
            role = role,
            securityVersion = 1,
            requestId = "req_0123456789abcdef",
        )

    private class RecordingPort :
        KillSwitchQueryPort,
        KillSwitchMutationPort {
        var mutationCount = 0
        var lastCommand: KillSwitchMutationCommand? = null

        override fun readPublicState(): KillSwitchPublicState =
            KillSwitchPublicState(false, KillSwitchReasonClass.INITIAL_STATE, Instant.EPOCH)

        override fun mutate(command: KillSwitchMutationCommand): KillSwitchMutationResult {
            mutationCount += 1
            lastCommand = command
            return KillSwitchMutationResult(
                state = readPublicState(),
                changed = false,
                previousActive = false,
                generation = 1,
                invalidatedDecisionCount = 0,
            )
        }
    }
}
