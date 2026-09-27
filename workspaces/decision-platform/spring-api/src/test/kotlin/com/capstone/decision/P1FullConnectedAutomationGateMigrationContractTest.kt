package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test

class P1FullConnectedAutomationGateMigrationContractTest {
    private val migration =
        requireNotNull(javaClass.getResource("/db/migration/V217__full_connected_account_automation_readiness.sql"))
            .readText()

    @Test
    fun `FULL admission binds the read-only proof to owner account revision and complete balance`() {
        assertThat(migration)
            .contains("CREATE TABLE public.full_owner_mock_connection_proofs_v217")
            .contains("credential.revision=proof.credential_revision")
            .contains("balance.observation_id=proof.balance_observation_id")
            .contains("balance.source_version='kis-mock-online-complete-v2'")
            .contains("CREATE FUNCTION public.p1_arm_automation_full_v1")
            .contains("app.full_connected_credential_arm")
            .contains("owner_connection_ready")
    }

    @Test
    fun `connection proof preserves certification status and local arm remains gated`() {
        assertThat(migration)
            .contains("p_owner_user_id,'REQUIRED',true,true")
            .contains("certification_receipt_sha256=CASE")
            .contains("CREATE OR REPLACE FUNCTION public.p1_arm_automation_v2")
            .contains("NOT gate_certified AND NOT owner_connection_ready")
            .contains("GRANT EXECUTE ON FUNCTION public.p1_arm_automation_full_v1")
        val fullConfig = requireNotNull(javaClass.getResource("/application-mars-full.yml")).readText()
        val localConfig = requireNotNull(javaClass.getResource("/application.yml")).readText()
        assertThat(fullConfig).contains("connected-kis-account-enabled: true")
        assertThat(localConfig).contains("connected-kis-account-enabled: false")
    }

    @Test
    fun `reconciled strategy orders verify the path and uncertain orders stop only the owner`() {
        assertThat(migration)
            .contains("automation_runs_full_owner_order_path_v217")
            .contains("NEW.state IN ('COMPLETED','CANCELLED_UNFILLED')")
            .contains("p1_stop_full_owner_after_order_failure_v217")
            .contains("reason_class='BROKERAGE_FAILURE_STOP'")
            .contains("KIS_ORDER_RESULT_UNCERTAIN")
            .contains("p1_full_owner_order_failure_code_v217")
    }
}
