package com.capstone.decision.domain.risk

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertSame
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import java.time.Instant

class KillSwitchDomainTest {
    private val policy = KillSwitchTransitionPolicy()
    private val changedAt = Instant.parse("2026-07-26T01:00:00Z")

    @Test
    fun `transition policy exhaustively enforces asymmetric stop and resume authority`() {
        val off = KillSwitchState(false, KillSwitchReasonClass.INITIAL_STATE, 1, changedAt)
        val on = KillSwitchState(true, KillSwitchReasonClass.USER_MANUAL_STOP, 2, changedAt)

        assertApplied(policy.decide(off, true, KillSwitchActorRole.USER), KillSwitchReasonClass.USER_MANUAL_STOP)
        assertApplied(policy.decide(off, true, KillSwitchActorRole.ADMIN), KillSwitchReasonClass.OPERATOR_MANUAL_STOP)
        assertSame(KillSwitchTransition.NoOp, policy.decide(on, true, KillSwitchActorRole.USER))
        assertSame(KillSwitchTransition.NoOp, policy.decide(on, true, KillSwitchActorRole.ADMIN))
        assertSame(KillSwitchTransition.ResumeRequiresAdmin, policy.decide(on, false, KillSwitchActorRole.USER))
        assertApplied(policy.decide(on, false, KillSwitchActorRole.ADMIN), KillSwitchReasonClass.ADMIN_RESUME)
        assertSame(KillSwitchTransition.ResumeRequiresAdmin, policy.decide(off, false, KillSwitchActorRole.USER))
        assertSame(KillSwitchTransition.NoOp, policy.decide(off, false, KillSwitchActorRole.ADMIN))
    }

    @Test
    fun `reason class parser rejects every value outside the persisted allowlist`() {
        KillSwitchReasonClass.entries.forEach { reasonClass ->
            assertEquals(reasonClass, KillSwitchReasonClass.fromStored(reasonClass.name))
        }

        listOf(null, "", " ", "UNKNOWN", "USER_MANUAL_STOP\n", "a".repeat(201)).forEach { invalid ->
            assertThrows(IllegalArgumentException::class.java) {
                KillSwitchReasonClass.fromStored(invalid)
            }
        }
    }

    @Test
    fun `optional manual reason is bounded and never determines the stored class`() {
        assertEquals(
            KillSwitchReasonClass.USER_MANUAL_STOP,
            KillSwitchReasonClass.forManualChange(true, KillSwitchActorRole.USER, null),
        )
        assertEquals(
            KillSwitchReasonClass.OPERATOR_MANUAL_STOP,
            KillSwitchReasonClass.forManualChange(true, KillSwitchActorRole.ADMIN, "시연 중 안전 정지"),
        )
        listOf("", " ", "reason\u0000", "가".repeat(201)).forEach { invalid ->
            assertThrows(IllegalArgumentException::class.java) {
                KillSwitchReasonClass.forManualChange(true, KillSwitchActorRole.USER, invalid)
            }
        }
    }

    @Test
    fun `state transition increases generation exactly once and never moves time backward`() {
        val current = KillSwitchState(false, KillSwitchReasonClass.INITIAL_STATE, 41, changedAt)
        val next =
            current.next(
                active = true,
                reasonClass = KillSwitchReasonClass.USER_MANUAL_STOP,
                changedAt = changedAt.plusSeconds(1),
            )

        assertEquals(42, next.generation)
        assertThrows(IllegalArgumentException::class.java) {
            current.next(
                active = true,
                reasonClass = KillSwitchReasonClass.USER_MANUAL_STOP,
                changedAt = changedAt.minusNanos(1),
            )
        }
    }

    private fun assertApplied(
        decision: KillSwitchTransition,
        expectedReason: KillSwitchReasonClass,
    ) {
        val applied = decision as KillSwitchTransition.Applied
        assertEquals(expectedReason, applied.reasonClass)
    }
}
