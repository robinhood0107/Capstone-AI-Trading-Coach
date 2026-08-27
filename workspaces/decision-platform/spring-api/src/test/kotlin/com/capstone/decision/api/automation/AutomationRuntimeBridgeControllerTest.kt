package com.capstone.decision.api.automation

import com.capstone.decision.api.brokerage.BrokerageRequestParser
import com.capstone.decision.api.decision.DecisionRequestParser
import com.capstone.decision.application.brokerage.BrokerageService
import com.capstone.decision.application.brokerage.MockBalanceProjection
import com.capstone.decision.application.decision.DecisionService
import com.capstone.decision.infrastructure.security.DemoRole
import com.capstone.decision.infrastructure.security.UserSecurityActorRecord
import com.capstone.decision.infrastructure.security.UserSecurityRepository
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import jakarta.servlet.http.HttpServletRequest
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test
import java.time.Instant

class AutomationRuntimeBridgeControllerTest {
    private val decisionService = mockk<DecisionService>()
    private val brokerageService = mockk<BrokerageService>()
    private val users = mockk<UserSecurityRepository>()
    private val request = mockk<HttpServletRequest>()
    private val controller =
        AutomationRuntimeBridgeController(
            decisionService,
            DecisionRequestParser(),
            brokerageService,
            BrokerageRequestParser(),
            users,
            SECRET,
        )

    @Test
    fun `loopback secret delegates balance to existing Spring brokerage service`() {
        every { request.remoteAddr } returns "127.0.0.1"
        every { users.findByUserId(USER_ID) } returns
            UserSecurityActorRecord(USER_ID, "runtime-user", DemoRole.USER, "ACTIVE", 7)
        every { brokerageService.getOwnedBalance(any(), ACCOUNT_ID) } returns
            MockBalanceProjection(
                accountId = ACCOUNT_ID,
                brokerageMode = "KIS_MOCK",
                cashKrw = 1_000_000,
                portfolioEquityKrw = 1_000_000,
                marginRequirementKrw = 0,
                positions = emptyList(),
                observedAt = Instant.parse("2026-08-27T00:00:00Z"),
                sourceVersion = "fixture",
            )

        val response = controller.command(SECRET, balanceBody(USER_ID), request)

        assertEquals(200, response.statusCode.value())
        verify(exactly = 1) { brokerageService.getOwnedBalance(match { it.userId == USER_ID }, ACCOUNT_ID) }
    }

    @Test
    fun `missing secret is hidden before user or brokerage lookup`() {
        every { request.remoteAddr } returns "127.0.0.1"

        val response = controller.command(null, balanceBody(USER_ID), request)

        assertEquals(404, response.statusCode.value())
        verify(exactly = 0) { users.findByUserId(any()) }
        verify(exactly = 0) { brokerageService.getOwnedBalance(any(), any()) }
    }

    private fun balanceBody(userId: String): String =
        """{"operation":"BALANCE","userId":"$userId","idempotencyKey":null,"payload":{"accountId":"$ACCOUNT_ID"}}"""

    private companion object {
        const val SECRET = "automation-runtime-bridge-test-secret-0001"
        const val USER_ID = "usr_automation_runtime_0001"
        const val ACCOUNT_ID = "acct_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
}
