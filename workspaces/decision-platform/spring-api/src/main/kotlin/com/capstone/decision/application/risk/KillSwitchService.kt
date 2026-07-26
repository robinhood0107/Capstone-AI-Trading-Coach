package com.capstone.decision.application.risk

import com.capstone.decision.domain.risk.KillSwitchActorRole
import com.capstone.decision.domain.risk.KillSwitchReasonClass

// 자유 서술 reason은 domain validation 뒤 즉시 버리고 persistence에는 enum만 전달한다.
class KillSwitchService(
    private val queryPort: KillSwitchQueryPort,
    private val mutationPort: KillSwitchMutationPort,
    private val observationPort: RiskObservationPort = RiskObservationPort.NONE,
) {
    fun getState(): KillSwitchPublicState =
        try {
            queryPort.readPublicState()
        } catch (exception: KillSwitchUnavailableException) {
            throw exception
        } catch (exception: Exception) {
            throw KillSwitchUnavailableException(exception)
        }

    fun change(
        actor: KillSwitchActor,
        active: Boolean,
        rawReason: String?,
    ): KillSwitchMutationResult {
        if (!active && actor.role != KillSwitchActorRole.ADMIN) {
            throw KillSwitchForbiddenException()
        }
        val reasonClass =
            KillSwitchReasonClass.forManualChange(
                active = active,
                actorRole = actor.role,
                rawReason = rawReason,
            )
        val result =
            try {
                mutationPort.mutate(
                    KillSwitchMutationCommand(
                        actor = actor,
                        requestedActive = active,
                        reasonClass = reasonClass,
                    ),
                )
            } catch (exception: KillSwitchForbiddenException) {
                throw exception
            } catch (exception: KillSwitchUnauthorizedException) {
                throw exception
            } catch (exception: KillSwitchConflictException) {
                throw exception
            } catch (exception: Exception) {
                throw KillSwitchUnavailableException(exception)
            }
        // mutation port의 transaction proxy가 반환한 뒤에만 관측하여 rollback 결과를 기록하지 않는다.
        observationPort.recordKillSwitchChanged(
            result = result,
            actorRole = actor.role,
            requestId = actor.requestId,
        )
        return result
    }
}
