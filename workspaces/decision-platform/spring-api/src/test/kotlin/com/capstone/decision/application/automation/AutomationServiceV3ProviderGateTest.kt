package com.capstone.decision.application.automation

import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.assertj.core.api.Assertions.assertThat
import org.assertj.core.api.Assertions.assertThatThrownBy
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.support.StaticListableBeanFactory

class AutomationServiceV3ProviderGateTest {
    private val repository = mockk<AutomationRepository>(relaxed = true)
    private val providers =
        StaticListableBeanFactory().getBeanProvider(AutomationEvidenceProvider::class.java)
    private val service = AutomationService(repository, providers)

    @Test
    fun `AI enabled status and arm stay blocked when no provider bean exists`() {
        every { repository.statusV3(OWNER) } returns status(aiEnabled = true)

        val projected = service.statusV3(OWNER)

        assertThat(projected.canArm).isFalse()
        assertThat(projected.blockers).contains("AI_PROVIDER_NOT_READY")
        assertThatThrownBy {
            service.armV3(
                OWNER,
                "idempotency-key-0001",
                ArmAutomationV3Command(
                    accountId = "acct_" + "a".repeat(32),
                    policyId = "auto_pol_" + "b".repeat(32),
                    expectedPolicyVersion = 1,
                    expectedControlVersion = 1,
                ),
            )
        }.isInstanceOf(AutomationBlockedException::class.java)
            .hasMessageContaining("AI_PROVIDER_NOT_READY")
        verify(exactly = 0) { repository.armV3(any(), any(), any(), any(), any()) }
    }

    @Test
    fun `AI off status does not invent a provider blocker`() {
        every { repository.statusV3(OWNER) } returns status(aiEnabled = false)

        val projected = service.statusV3(OWNER)

        assertThat(projected.canArm).isTrue()
        assertThat(projected.blockers).doesNotContain("AI_PROVIDER_NOT_READY")
    }

    private fun status(aiEnabled: Boolean) =
        AutomationStatusV3Projection(
            controlState = "DISARMED",
            projectionState = "DISARMED",
            controlVersion = 1,
            accountId = "acct_" + "a".repeat(32),
            policy = null,
            aiJudgementEnabled = aiEnabled,
            thinkingLevel = "low",
            marketHistoryStatus = "READY",
            killSwitchActive = false,
            certificationStatus = "VALID",
            openPositionCount = 0,
            legacyOpenPositionCount = 0,
            unresolvedReconciliation = false,
            canArm = true,
            blockers = emptyList(),
        )

    private companion object {
        const val OWNER = "usr_demo_user"
    }
}
