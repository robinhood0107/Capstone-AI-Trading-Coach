package com.capstone.decision.domain.decision

import com.capstone.decision.domain.principle.PrincipleMode
import com.capstone.decision.domain.risk.EvaluationAction
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test

class DecisionOutcomePolicyTest {
    @Test
    fun `Guide Strict full matrix never weakens HOLD or BLOCK`() {
        val expected =
            mapOf(
                (PrincipleMode.GUIDE to EvaluationAction.ALLOW) to
                    (true to EnforcementAction.NONE),
                (PrincipleMode.STRICT to EvaluationAction.ALLOW) to
                    (true to EnforcementAction.NONE),
                (PrincipleMode.GUIDE to EvaluationAction.WARN) to
                    (true to EnforcementAction.ACKNOWLEDGE_WARNING),
                (PrincipleMode.STRICT to EvaluationAction.WARN) to
                    (true to EnforcementAction.RECONFIRM_PRINCIPLE),
                (PrincipleMode.GUIDE to EvaluationAction.HOLD) to
                    (false to EnforcementAction.RE_EVALUATE),
                (PrincipleMode.STRICT to EvaluationAction.HOLD) to
                    (false to EnforcementAction.RE_EVALUATE),
                (PrincipleMode.GUIDE to EvaluationAction.BLOCK) to
                    (false to EnforcementAction.DO_NOT_SUBMIT),
                (PrincipleMode.STRICT to EvaluationAction.BLOCK) to
                    (false to EnforcementAction.DO_NOT_SUBMIT),
            )

        expected.forEach { (input, output) ->
            val applied = DecisionOutcomePolicy().apply(input.first, input.second)

            assertThat(applied.canSubmitOrder).isEqualTo(output.first)
            assertThat(applied.enforcementAction).isEqualTo(output.second)
            assertThat(applied.outcome).isEqualTo(input.second)
        }
    }
}
