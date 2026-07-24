package com.capstone.decision.domain.decision

import com.capstone.decision.domain.principle.PrincipleMode
import com.capstone.decision.domain.risk.EvaluationAction

enum class EnforcementAction {
    NONE,
    ACKNOWLEDGE_WARNING,
    RECONFIRM_PRINCIPLE,
    RE_EVALUATE,
    DO_NOT_SUBMIT,
}

data class EnforcedDecisionOutcome(
    val outcome: EvaluationAction,
    val canSubmitOrder: Boolean,
    val enforcementAction: EnforcementAction,
)

// pinned Principle mode는 WARN의 사용자 절차만 바꾸며 HOLD/BLOCK을 제출 가능 상태로 약화하지 않는다.
class DecisionOutcomePolicy {
    fun apply(
        mode: PrincipleMode,
        outcome: EvaluationAction,
    ): EnforcedDecisionOutcome =
        when (outcome) {
            EvaluationAction.ALLOW ->
                EnforcedDecisionOutcome(
                    outcome = outcome,
                    canSubmitOrder = true,
                    enforcementAction = EnforcementAction.NONE,
                )

            EvaluationAction.WARN ->
                EnforcedDecisionOutcome(
                    outcome = outcome,
                    canSubmitOrder = true,
                    enforcementAction =
                        when (mode) {
                            PrincipleMode.GUIDE -> EnforcementAction.ACKNOWLEDGE_WARNING
                            PrincipleMode.STRICT -> EnforcementAction.RECONFIRM_PRINCIPLE
                        },
                )

            EvaluationAction.HOLD ->
                EnforcedDecisionOutcome(
                    outcome = outcome,
                    canSubmitOrder = false,
                    enforcementAction = EnforcementAction.RE_EVALUATE,
                )

            EvaluationAction.BLOCK ->
                EnforcedDecisionOutcome(
                    outcome = outcome,
                    canSubmitOrder = false,
                    enforcementAction = EnforcementAction.DO_NOT_SUBMIT,
                )
        }
}
