package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class PaperBrokerageMigrationContractTest {
    private val migration =
        Files.readString(
            Path.of("src/main/resources/db/migration/V13__s3_2_internal_paper_ledger.sql"),
        )

    @Test
    fun `V13은 additive precondition과 mode-prefix 상호강제를 고정한다`() {
        assertTrue(migration.contains("S3.2 V13 precondition failed"))
        assertTrue(migration.contains("brokerage_mode IN ('KIS_MOCK', 'INTERNAL_PAPER')"))
        assertTrue(migration.contains("brokerage_mode = 'KIS_MOCK'"))
        assertTrue(migration.contains("brokerage_mode = 'INTERNAL_PAPER'"))
        assertTrue(migration.contains("^ord_mock_[0-9a-f]{32}$"))
        assertTrue(migration.contains("^ord_paper_[0-9a-f]{32}$"))
        assertFalse(migration.contains("'KIS_LIVE'"))
    }

    @Test
    fun `V13은 paper 원장을 append-only 최소권한 definer 경계로 만든다`() {
        assertTrue(migration.contains("ALTER TABLE paper_accounts FORCE ROW LEVEL SECURITY"))
        assertTrue(migration.contains("ALTER TABLE paper_positions FORCE ROW LEVEL SECURITY"))
        assertTrue(migration.contains("ALTER TABLE paper_order_events FORCE ROW LEVEL SECURITY"))
        assertTrue(migration.contains("CREATE FUNCTION create_paper_order("))
        assertTrue(migration.contains("CREATE FUNCTION rebuild_paper_state("))
        assertTrue(migration.contains("REVOKE ALL PRIVILEGES ON TABLE"))
        assertTrue(migration.contains("paper_order_events_order_unique"))
        assertTrue(migration.contains("paper_order_events_account_sequence_unique"))
        assertFalse(migration.contains("GRANT UPDATE ON TABLE paper_order_events"))
        assertFalse(migration.contains("GRANT DELETE ON TABLE paper_order_events"))
        assertFalse(migration.contains("GRANT TRUNCATE ON TABLE paper_order_events"))
    }

    @Test
    fun `V13 paper 함수는 actor kill-switch decision 계좌 순서로 재검증한다`() {
        val actor = migration.indexOf("FROM public.users actor")
        val idempotencyLock = migration.indexOf("paper-order:idempotency:")
        val decisionLock = migration.indexOf("paper-order:decision:")
        val killSwitch = migration.indexOf("FROM public.risk_kill_switch gate")
        val account = migration.indexOf("FROM public.paper_accounts account")

        assertTrue(actor in 0..<idempotencyLock)
        assertTrue(idempotencyLock < decisionLock)
        assertTrue(decisionLock < killSwitch)
        assertTrue(killSwitch < account)
        assertTrue(migration.contains("valid_until > pg_catalog.clock_timestamp()"))
        assertTrue(migration.contains("portfolio_source <> 'INTERNAL_PAPER'"))
    }

    @Test
    fun `V13 evidence와 payload는 allowlist key만 허용한다`() {
        assertTrue(migration.contains("'PAPER_ORDER_ACCEPTED'"))
        assertTrue(migration.contains("'PAPER_ORDER_FILLED'"))
        assertTrue(migration.contains("'PAPER_ORDER_CANCELLED'"))
        assertTrue(migration.contains("'brokerage.paper-order-accepted.v1'"))
        assertTrue(migration.contains("'brokerage.paper-order-filled.v1'"))
        assertTrue(migration.contains("'brokerage.paper-order-cancelled.v1'"))
        assertTrue(migration.contains("'LAST_QUOTE', 'PREVIOUS_CLOSE'"))
        assertTrue(migration.contains("'feeModel'"))
        assertTrue(migration.contains("'slippageBps'"))
        assertFalse(migration.contains("account_number", ignoreCase = true))
        assertFalse(migration.contains("access_token", ignoreCase = true))
    }
}
