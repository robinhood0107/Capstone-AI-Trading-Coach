package com.capstone.decision.api.automation

import com.capstone.decision.api.brokerage.BrokerageRequestParser
import com.capstone.decision.api.decision.DecisionRequestParser
import com.capstone.decision.application.automation.AutomationEvidenceService
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
    private val evidenceService = mockk<AutomationEvidenceService>()
    private val request = mockk<HttpServletRequest>()
    private val controller =
        AutomationRuntimeBridgeController(
            decisionService,
            DecisionRequestParser(),
            brokerageService,
            BrokerageRequestParser(),
            evidenceService,
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
    fun `evaluation binds owner run and claim without accepting a client principle version`() {
        every { request.remoteAddr } returns "127.0.0.1"
        every { users.findByUserId(USER_ID) } returns
            UserSecurityActorRecord(USER_ID, "runtime-user", DemoRole.USER, "ACTIVE", 7)
        every { decisionService.evaluate(any(), any(), any(), any(), any()) } returns mockk(relaxed = true)
        val runId = "auto_run_" + "a".repeat(32)
        val claim = "sha256:" + "b".repeat(64)
        val body =
            """
            {"operation":"EVALUATE","userId":"$USER_ID","idempotencyKey":"automation-fixture-key-0001",
             "payload":{"runId":"$runId","claimTokenHash":"$claim","evaluation":{
               "principleId":"prc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","portfolioSource":"KIS_MOCK","orderIntent":{
                 "symbol":"005930","side":"BUY","orderType":"LIMIT","quantity":1,
                 "estimatedPrice":75000,"estimatedAmount":75000,"timeframe":"1d","strategyId":"strategy_fixture_0001"}}}}
            """.trimIndent()
        assertEquals(200, controller.command(SECRET, body, request).statusCode.value())
        verify(exactly = 1) {
            decisionService.evaluate(match { it.userId == USER_ID }, any(), any(), runId, claim)
        }
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
