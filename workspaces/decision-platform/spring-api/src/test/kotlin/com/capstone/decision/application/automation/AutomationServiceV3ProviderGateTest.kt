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
        verify(exactly = 0) { repository.armV3(any(), any(), any(), any(), any(), any(), any()) }
    }

    @Test
    fun `AI off status does not invent a provider blocker`() {
        every { repository.statusV3(OWNER) } returns status(aiEnabled = false)

        val projected = service.statusV3(OWNER)

        assertThat(projected.canArm).isTrue()
        assertThat(projected.blockers).doesNotContain("AI_PROVIDER_NOT_READY")
    }

    @Test
    fun `FULL operator Vertex path reaches status and arm with the same readiness value`() {
        val fullRepository = mockk<AutomationRepository>(relaxed = true)
        val withTransport = StaticListableBeanFactory()
        withTransport.addBean("evidence", FixtureAutomationEvidenceProvider())
        val full =
            AutomationService(
                fullRepository,
                withTransport.getBeanProvider(AutomationEvidenceProvider::class.java),
                AutomationAiProviderPolicy(operatorVertexFallbackEnabled = true),
                connectedKisAccountEnabled = true,
            )
        every { fullRepository.statusV3(OWNER, true) } returns status(aiEnabled = true)
        every { fullRepository.armV3(OWNER, any(), any(), any(), true, true, true) } returns status(aiEnabled = true)

        assertThat(full.statusV3(OWNER).blockers).doesNotContain("AI_PROVIDER_NOT_READY")
        full.armV3(OWNER, "idempotency-key-0002", ARM)

        verify { fullRepository.statusV3(OWNER, true) }
        verify(exactly = 1) { fullRepository.armV3(OWNER, any(), any(), any(), true, true, true) }
    }

    @Test
    fun `LOCAL keeps the owner credential rule because the operator path is off`() {
        val localRepository = mockk<AutomationRepository>(relaxed = true)
        val withTransport = StaticListableBeanFactory()
        withTransport.addBean("evidence", FixtureAutomationEvidenceProvider())
        val local =
            AutomationService(
                localRepository,
                withTransport.getBeanProvider(AutomationEvidenceProvider::class.java),
                AutomationAiProviderPolicy(operatorVertexFallbackEnabled = false),
            )
        every { localRepository.statusV3(OWNER, false) } returns status(aiEnabled = true)
        every { localRepository.armV3(OWNER, any(), any(), any(), true, false, false) } returns status(aiEnabled = true)

        local.statusV3(OWNER)
        local.armV3(OWNER, "idempotency-key-0003", ARM)

        verify { localRepository.statusV3(OWNER, false) }
        verify(exactly = 1) { localRepository.armV3(OWNER, any(), any(), any(), true, false, false) }
        verify(exactly = 0) { localRepository.statusV3(OWNER, true) }
    }

    @Test
    fun `the operator switch never counts without a live evidence transport`() {
        val policy = AutomationAiProviderPolicy(operatorVertexFallbackEnabled = true)
        assertThat(policy.operatorProviderReady(transportReady = false)).isFalse()
        assertThat(policy.operatorProviderReady(transportReady = true)).isTrue()
        assertThat(AutomationAiProviderPolicy(false).operatorProviderReady(transportReady = true)).isFalse()
    }

    @Test
    fun `own key always wins and the operator is only a permitted fallback`() {
        val on = AutomationAiProviderPolicy(operatorVertexFallbackEnabled = true)
        val off = AutomationAiProviderPolicy(operatorVertexFallbackEnabled = false)
        assertThat(on.select(ownerCredentialUsable = true)).isEqualTo(AutomationAiCredentialSource.OWNER)
        assertThat(on.select(ownerCredentialUsable = false)).isEqualTo(AutomationAiCredentialSource.OPERATOR)
        assertThat(off.select(ownerCredentialUsable = true)).isEqualTo(AutomationAiCredentialSource.OWNER)
        assertThat(off.select(ownerCredentialUsable = false)).isNull()
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
        val ARM =
            ArmAutomationV3Command(
                accountId = "acct_" + "a".repeat(32),
                policyId = "auto_pol_" + "b".repeat(32),
                expectedPolicyVersion = 1,
                expectedControlVersion = 1,
            )
    }
}
