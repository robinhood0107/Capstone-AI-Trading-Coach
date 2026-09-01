package com.capstone.decision.application.brokerage

import com.capstone.decision.application.risk.KillSwitchGate
import com.capstone.decision.application.risk.KillSwitchGuard
import com.capstone.decision.domain.risk.OrderIntentSnapshot
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.runs
import io.mockk.verify
import io.mockk.verifyOrder
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.ObjectProvider
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset

class BrokerageOnlineServiceTest {
    private val persistence = mockk<BrokerageOrderPersistencePort>()
    private val hasher = mockk<BrokerageIdempotencyIdentityPort>()
    private val projectionFactory = mockk<BrokerageProjectionFactory>()
    private val killSwitch = mockk<KillSwitchGuard>()
    private val gateway = mockk<BrokerageGatewayPort>()
    private val gatewayProvider = mockk<ObjectProvider<BrokerageGatewayPort>>()
    private val clock = Clock.fixed(NOW, ZoneOffset.UTC)
    private val service =
        BrokerageService(
            persistence,
            hasher,
            projectionFactory,
            killSwitch,
            clock,
            gatewayProvider,
        )

    @Test
    fun `provider submit runs after durable reservation and records accepted outcome`() {
        arrangeSubmit()
        every { gateway.submitMockOrder(any()) } returns
            BrokerageGatewaySubmitResult(ORDER_ID, "a".repeat(64), "VTTC0012U", NOW.plusSeconds(1))
        every { persistence.recordProviderOutcome(any()) } returns detail("ACCEPTED")
        every { projectionFactory.fromDetail(any()) } returns projection("ACCEPTED")

        val result = service.submitMockOrder(ACTOR, "online-idempotency-0001", command())

        assertEquals("ACCEPTED", result.status)
        verifyOrder {
            persistence.persist(any())
            gateway.submitMockOrder(any())
            persistence.recordProviderOutcome(match { it.status == "ACCEPTED" })
        }
        verify(exactly = 1) { gateway.submitMockOrder(any()) }
    }

    @Test
    fun `ambiguous submit records pending exactly once and never retries provider`() {
        arrangeSubmit()
        every { gateway.submitMockOrder(any()) } throws RuntimeException("synthetic")
        every { persistence.recordProviderOutcome(match { it.status == "PENDING_RECONCILIATION" }) } returns
            detail("PENDING_RECONCILIATION")

        assertThrows(BrokerageUnavailableException::class.java) {
            service.submitMockOrder(ACTOR, "online-idempotency-0002", command())
        }

        verify(exactly = 1) { gateway.submitMockOrder(any()) }
        verify(exactly = 1) {
            persistence.recordProviderOutcome(match { it.status == "PENDING_RECONCILIATION" })
        }
    }

    @Test
    fun `accepted provider result becomes pending when durable outcome write fails`() {
        arrangeSubmit()
        every { gateway.submitMockOrder(any()) } returns
            BrokerageGatewaySubmitResult(ORDER_ID, "a".repeat(64), "VTTC0012U", NOW.plusSeconds(1))
        every {
            persistence.recordProviderOutcome(match { it.status == "ACCEPTED" })
        } throws RuntimeException("synthetic database failure")
        every { persistence.recordProviderOutcome(match { it.status == "PENDING_RECONCILIATION" }) } returns
            detail("PENDING_RECONCILIATION")

        assertThrows(BrokerageUnavailableException::class.java) {
            service.submitMockOrder(ACTOR, "online-idempotency-0003", command())
        }

        verify(exactly = 1) { gateway.submitMockOrder(any()) }
        verify(exactly = 1) {
            persistence.recordProviderOutcome(match { it.status == "ACCEPTED" })
        }
        verify(exactly = 1) {
            persistence.recordProviderOutcome(match { it.status == "PENDING_RECONCILIATION" })
        }
    }

    @Test
    fun `provider cancel runs after cancel requested event and confirms cancelled`() {
        every { gatewayProvider.getIfAvailable() } returns gateway
        every { persistence.cancelOwnedOrder(any(), ORDER_ID, NOW) } returns detail("CANCEL_REQUESTED")
        every { gateway.cancelMockOrder(any()) } returns
            BrokerageGatewayCancelResult(ORDER_ID, "CANCELLED", NOW.plusSeconds(1))
        every { persistence.recordProviderOutcome(any()) } returns detail("CANCELLED")

        val result = service.cancelOwnedOrder(ACTOR, ORDER_ID)

        assertEquals("CANCELLED", result.status)
        verifyOrder {
            persistence.cancelOwnedOrder(any(), ORDER_ID, NOW)
            gateway.cancelMockOrder(any())
            persistence.recordProviderOutcome(match { it.status == "CANCELLED" })
        }
    }

    @Test
    fun `online balance runs only after stored owner anchor is found`() {
        every { persistence.findOwnedBalance(ACTOR.userId, ACCOUNT_ID) } returns storedBalance()
        every { gatewayProvider.getIfAvailable() } returns gateway
        every { gateway.getMockBalance(any()) } returns
            BrokerageGatewayBalanceResult(
                accountId = ACCOUNT_ID,
                cashKrw = 1_000_000,
                portfolioEquityKrw = 1_140_000,
                marginRequirementKrw = 0,
                positions =
                    listOf(
                        MockBalancePositionProjection("005930", 2, 140_000, false),
                    ),
                observedAt = NOW.plusSeconds(1),
                sourceVersion = "kis-mock-balance-v1",
            )

        val result = service.getOwnedBalance(ACTOR, ACCOUNT_ID)

        assertEquals(1_000_000, result.cashKrw)
        verifyOrder {
            persistence.findOwnedBalance(ACTOR.userId, ACCOUNT_ID)
            gateway.getMockBalance(match { it.requestId == ACTOR.requestId })
        }
    }

    @Test
    fun `online balance uses the complete stored observation when provider risk fields are unavailable`() {
        every { persistence.findOwnedBalance(ACTOR.userId, ACCOUNT_ID) } returns storedBalance()
        every { gatewayProvider.getIfAvailable() } returns gateway
        every { gateway.getMockBalance(any()) } throws BrokerageUnavailableException("risk fields unavailable")

        val result = service.getOwnedBalance(ACTOR, ACCOUNT_ID)

        assertEquals(1_000_000, result.cashKrw)
        assertEquals("stored-kis-mock-v1", result.sourceVersion)
        verifyOrder {
            persistence.findOwnedBalance(ACTOR.userId, ACCOUNT_ID)
            gateway.getMockBalance(match { it.accountId == ACCOUNT_ID })
        }
    }

    @Test
    fun `online buyable uses exact query after stored owner anchor is found`() {
        every { persistence.findOwnedBalance(ACTOR.userId, ACCOUNT_ID) } returns storedBalance()
        every { gatewayProvider.getIfAvailable() } returns gateway
        every { gateway.getMockBuyable(any()) } returns
            BrokerageGatewayBuyableResult(
                accountId = ACCOUNT_ID,
                symbol = "005930",
                estimatedPriceKrw = 70_000,
                buyableQuantity = 14,
                buyableAmountKrw = 980_000,
                cashKrw = 1_000_000,
                observedAt = NOW.plusSeconds(1),
                sourceVersion = "kis-mock-buyable-v1",
            )

        val result = service.getOwnedBuyable(ACTOR, ACCOUNT_ID, "005930", 70_000)

        assertEquals(14, result.buyableQuantity)
        verifyOrder {
            persistence.findOwnedBalance(ACTOR.userId, ACCOUNT_ID)
            gateway.getMockBuyable(
                match {
                    it.requestId == ACTOR.requestId &&
                        it.symbol == "005930" &&
                        it.estimatedPriceKrw == 70_000L
                },
            )
        }
    }

    private fun arrangeSubmit() {
        every { killSwitch.check() } returns KillSwitchGate(active = false, generation = 1)
        every { gatewayProvider.getIfAvailable() } returns gateway
        every { hasher.identity(ACTOR.userId, any(), any()) } returns
            BrokerageIdempotencyIdentity("a".repeat(64), "b".repeat(64), "c".repeat(64))
        every { persistence.findIdempotencyResult(ACTOR.userId, any(), any(), NOW) } returns null
        every { persistence.findOrderableDecisionAccountId(ACTOR.userId, DECISION_ID) } returns ACCOUNT_ID
        every { projectionFactory.createSubmitted(any(), ACCOUNT_ID, NOW) } returns projection("SUBMITTED")
        every { projectionFactory.canonicalJson(any()) } returns
            """{"orderId":"$ORDER_ID","status":"SUBMITTED"}"""
        every { persistence.persist(any()) } just runs
    }

    private fun storedBalance() =
        StoredMockBalance(
            accountId = ACCOUNT_ID,
            accountScopeHash = "a".repeat(64),
            cashKrw = 1_000_000,
            portfolioEquityKrw = 1_140_000,
            marginRequirementKrw = 0,
            completeness = "COMPLETE",
            positionCount = 1,
            positions = listOf(MockBalancePositionProjection("005930", 2, 140_000, false)),
            observedAt = NOW,
            sourceVersion = "stored-kis-mock-v1",
        )

    private fun command() =
        SubmitMockOrderCommand(
            decisionId = DECISION_ID,
            orderIntent =
                OrderIntentSnapshot(
                    symbol = "005930",
                    side = "BUY",
                    orderType = "MARKET",
                    quantity = 1,
                    estimatedPrice = 70_000,
                    estimatedAmount = 70_000,
                    timeframe = "1d",
                    strategyId = "strategy",
                ),
            userAcknowledgement = UserAcknowledgement(false),
        )

    private fun projection(status: String) = MockOrderProjection(ORDER_ID, ACCOUNT_ID, "KIS_MOCK", status, NOW)

    private fun detail(status: String) = OrderDetailProjection(ORDER_ID, ACCOUNT_ID, "KIS_MOCK", status, NOW, DECISION_ID)

    private companion object {
        val NOW: Instant = Instant.parse("2030-01-02T03:04:05Z")
        val ACTOR = BrokerageActor("usr_test", "USER", 1, "req-online")
        const val ORDER_ID = "ord_mock_11111111111111111111111111111111"
        const val ACCOUNT_ID = "acct_22222222222222222222222222222222"
        const val DECISION_ID = "dec_online"
    }
}
