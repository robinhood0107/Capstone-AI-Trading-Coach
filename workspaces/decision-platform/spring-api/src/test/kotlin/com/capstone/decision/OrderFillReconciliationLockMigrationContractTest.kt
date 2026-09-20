package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class OrderFillReconciliationLockMigrationContractTest {
    @Test
    fun `reconciliation v3 waits within the statement budget and delegates the existing lock`() {
        val migration =
            Files.readString(
                Path.of("src/main/resources/db/migration/V159__order_fill_reconciliation_bounded_lock_wait.sql"),
            )
        assertThat(migration).contains(
            "set_config('lock_timeout','1500ms',true)",
            "acquire_order_fill_reconciliation_lock_authorized_v2(p_capability,p_payload_json)",
            "session_user<>'decision_app'",
            "REVOKE ALL",
        )
        assertThat(migration).doesNotContain("statement_timeout','0", "lock_timeout','0")
    }
}
