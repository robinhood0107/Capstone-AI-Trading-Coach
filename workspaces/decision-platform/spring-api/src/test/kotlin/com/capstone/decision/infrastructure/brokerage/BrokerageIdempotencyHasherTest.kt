package com.capstone.decision.infrastructure.brokerage

import com.capstone.decision.application.brokerage.SubmitMockOrderCommand
import com.capstone.decision.application.brokerage.UserAcknowledgement
import com.capstone.decision.domain.risk.OrderIntentSnapshot
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class BrokerageIdempotencyHasherTest {
    private val hasher =
        BrokerageIdempotencyHasher(
            BrokerageProperties(
                idempotencyScopeHmacKey = "b".repeat(64),
            ),
        )

    @Test
    fun `같은 raw key도 submit cancel paper fill 네 purpose hash가 모두 분리된다`() {
        val mock = hasher.identity("usr_demo_user", "same-order-key-0001", command())
        val paper = hasher.paperIdentity("usr_demo_user", "same-order-key-0001", command())
        val cancel =
            hasher.replayIdentity(
                "usr_demo_user",
                "USER",
                1,
                "same-order-key-0001",
                BrokerageWriteReplayPurpose.ORDER_CANCEL,
            )
        val fill =
            hasher.replayIdentity(
                "usr_demo_user",
                "USER",
                1,
                "same-order-key-0001",
                BrokerageWriteReplayPurpose.FILL_APPLY,
            )

        assertEquals(
            4,
            setOf(mock.scopeHash, paper.scopeHash, cancel.scopeHash, fill.scopeHash).size,
        )
        assertEquals(
            4,
            setOf(mock.ownerScopeHash, paper.ownerScopeHash, cancel.ownerScopeHash, fill.ownerScopeHash).size,
        )
        assertEquals(mock.requestHash, paper.requestHash)
        assertTrue(
            listOf(mock.scopeHash, paper.scopeHash, cancel.scopeHash, fill.scopeHash)
                .all { it.matches(Regex("^[0-9a-f]{64}$")) },
        )
        assertTrue(
            listOf(mock.scopeHash, paper.scopeHash, cancel.scopeHash, fill.scopeHash)
                .none { it.contains("same-order-key-0001") || it.contains("usr_demo_user") },
        )
        assertNotEquals(
            paper.scopeHash,
            hasher.paperIdentity("usr_demo_admin", "same-order-key-0001", command()).scopeHash,
        )
    }

    @Test
    fun `replay scope는 현재 role과 security version을 묶고 owner quota scope는 유지한다`() {
        val userV1 =
            hasher.replayIdentity(
                "usr_demo_admin",
                "USER",
                1,
                "same-order-key-0002",
                BrokerageWriteReplayPurpose.FILL_APPLY,
            )
        val adminV1 =
            hasher.replayIdentity(
                "usr_demo_admin",
                "ADMIN",
                1,
                "same-order-key-0002",
                BrokerageWriteReplayPurpose.FILL_APPLY,
            )
        val adminV2 =
            hasher.replayIdentity(
                "usr_demo_admin",
                "ADMIN",
                2,
                "same-order-key-0002",
                BrokerageWriteReplayPurpose.FILL_APPLY,
            )

        assertEquals(3, setOf(userV1.scopeHash, adminV1.scopeHash, adminV2.scopeHash).size)
        assertEquals(1, setOf(userV1.ownerScopeHash, adminV1.ownerScopeHash, adminV2.ownerScopeHash).size)
    }

    private fun command(): SubmitMockOrderCommand =
        SubmitMockOrderCommand(
            decisionId = "dec_${"a".repeat(32)}",
            orderIntent =
                OrderIntentSnapshot(
                    symbol = "005930",
                    side = "BUY",
                    orderType = "MARKET",
                    quantity = 2,
                    estimatedPrice = 70_000,
                    estimatedAmount = 140_000,
                    timeframe = "1d",
                    strategyId = "paper-v1",
                ),
            userAcknowledgement = UserAcknowledgement(warningsAccepted = true),
        )
}
