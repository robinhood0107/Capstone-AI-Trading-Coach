package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test

class P1AutomationAccountHistoryIntegrityMigrationContractTest {
    private val migration =
        requireNotNull(
            javaClass.getResource(
                "/db/migration/V218__automation_account_history_integrity_and_disarm_schedule.sql",
            ),
        ).readText()

    @Test
    fun accountIdentityUsesFullKeyedIdentityAndSurvivesCredentialDeletion() {
        assertThat(migration)
            .contains("full_owner_account_identity_v218")
            .contains("p1_resolve_or_bind_mock_account_identity_v218")
            .contains("p1_register_mock_account_identity_before_disconnect_v218")
            .contains("p1_read_mock_credential_identity_envelope_v218")
            .contains("DISCONNECT_MOCK_CREDENTIAL")
            .contains("resolve_bound_mock_account_reuse_v1(text,text)")
    }

    @Test
    fun historicalAccountsLinkOnlyOnUniqueExactBalanceAndPositionEvidence() {
        assertThat(migration)
            .contains("balance.cash_krw=current_snapshot.cash_krw")
            .contains("balance.portfolio_equity_krw=current_snapshot.portfolio_equity_krw")
            .contains("balance.position_count=current_snapshot.position_count")
            .contains("candidate.position_set IS DISTINCT FROM current_snapshot.position_set")
            .contains("candidate_count<>1")
            .contains("full_owner_account_aliases_v218")
            .contains("source_account_scope_hash=COALESCE(order_row.source_account_scope_hash,order_row.account_scope_hash)")
    }

    @Test
    fun ownedInternalPaperHistoryStaysLinkedAndSeparateFromLiveKis() {
        assertThat(migration)
            .contains("paper.name='Offline history replay'")
            .contains("run.run_id LIKE 'auto_run_replay%'")
            .contains("reservation.reconciliation_status='MATCHED'")
            .contains("automation_position_history_events_v218")
            .contains("DETERMINISTIC_INTERNAL_PAPER_REPLAY")
            .contains("TEAM_A_ACCEPTANCE_PAPER_FIXTURE")
            .contains("data_quality_state='HISTORICAL_PAPER'")
            .contains("SET account_id=replay_account")
            .contains("auto_pos_team_a_closed_0001")
            .contains("SET account_id=paper_account")
            .contains("automation_paper_account_rekey_events_v218")
            .contains("acct_f1a5e315b7c8462b9338f0cf4c5a1d20")
            .doesNotContain("SET data_quality_state='QUARANTINED_UNVERIFIED'")
            .doesNotContain("SET status='HALTED_MISMATCH'")
            .doesNotContain("realized_pnl_krw=0")
            .doesNotContain("closed_at=statement_timestamp()")
    }

    @Test
    fun unreconciledKisOrderRemainsPreservedAndBlocksAutomation() {
        assertThat(migration)
            .contains("automation_order_integrity_events_v218")
            .contains("USER_REQUESTED_QUARANTINE_BLOCK_START")
            .contains("'SUBMITTED'")
            .contains("LEGACY_UNLINKED_ORDER_NO_RECONCILIATION")
    }

    @Test
    fun unlinkedHistoryBlocksArmAndRuntimeClaimWhileDisarmClearsScheduledRows() {
        assertThat(migration)
            .contains("ACCOUNT_HISTORY_UNLINKED")
            .contains("P1H01")
            .contains("automation_schedule_account_history_guard_v218")
            .contains("p1_arm_automation_full_v1")
            .contains("schedule.schedule_state='ARMED'")
            .contains("SCHEDULE_DISARMED")
            .contains("historical_paper_open_position_count")
            .contains("historical_paper_closed_position_count")
            .contains("historical_paper_run_count")
    }
}
