package com.capstone.decision

import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class S3OnlineMigrationContractTest {
    private val migration =
        Path.of("src/main/resources/db/migration/V15__s3_online_kis_mock_provider_outcomes.sql")

    @Test
    fun `provider outcome migration is additive bounded and live order stays absent`() {
        assertTrue(Files.isRegularFile(migration), "S3-online must use a new additive V15 migration")
        val sql = Files.readString(migration)

        assertTrue(sql.contains("record_mock_order_provider_outcome"))
        assertTrue(sql.contains("MOCK_ORDER_ACCEPTED"))
        assertTrue(sql.contains("PENDING_RECONCILIATION"))
        assertTrue(sql.contains("providerOrderRefHash"))
        assertTrue(sql.contains("requested_capability_token"))
        assertTrue(sql.contains("SECURITY DEFINER"))
        assertTrue(sql.contains("REVOKE ALL"))
        assertFalse(sql.contains("TTTC0011U"))
        assertFalse(sql.contains("TTTC0012U"))
        assertFalse(sql.contains("TTTC0013U"))
        assertFalse(sql.contains("KIS_LIVE"))
    }
}
