package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class OrderFillContractPresenceTest {
    @Test
    fun `S3_3 순수 체결 도메인 경계가 존재한다`() {
        val requiredClasses =
            listOf(
                "com.capstone.decision.domain.brokerage.OrderFillTransition",
                "com.capstone.decision.domain.brokerage.OrderFillAggregation",
                "com.capstone.decision.domain.brokerage.OrderReconciliationPolicy",
            )

        requiredClasses.forEach { className ->
            assertTrue(
                runCatching { Class.forName(className) }.isSuccess,
                "$className must exist as an infrastructure-free S3.3 domain boundary.",
            )
        }
    }

    @Test
    fun `V14는 관측 원본과 주문 보존식을 additive migration으로 고정한다`() {
        val migrationPath =
            Path.of("src/main/resources/db/migration/V14__s3_3_fill_events_reconciliation.sql")
        assertTrue(Files.isRegularFile(migrationPath), "V14 migration must exist.")
        val migration = Files.readString(migrationPath)

        assertTrue(migration.contains("CREATE TABLE order_fill_observations"))
        assertTrue(migration.contains("orders_quantity_conservation_check"))
        assertTrue(migration.contains("filled_quantity + leaves_quantity + unfilled_terminated_quantity = quantity"))
        assertTrue(migration.contains("ALTER TABLE order_fill_observations FORCE ROW LEVEL SECURITY"))
        assertTrue(migration.contains("CREATE FUNCTION apply_stored_order_fills("))
        assertTrue(migration.contains("CREATE FUNCTION read_owned_order_fills("))
        assertTrue(migration.contains("CREATE FUNCTION read_order_reconciliation_state("))
    }
}
