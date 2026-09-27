package com.capstone.decision

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test

class P1OwnerConfirmedLegacyOrderRetirementMigrationContractTest {
    private val migration =
        requireNotNull(
            javaClass.getResource("/db/migration/V219__retire_owner_confirmed_legacy_order.sql"),
        ).readText()

    @Test
    fun `only the confirmed demo-user row can be retired and the owner history gate can clear`() {
        assertThat(migration)
            .contains("expected_order_id_sha256")
            .contains("59ac1cd9ca4c736d29c86493325a508db084e17501e22580a11152f61af40cea")
            .contains("username='demo-user'")
            .doesNotContain("ord_mock_")
            .contains("order_row.symbol IS DISTINCT FROM '005930'")
            .contains("order_row.side IS DISTINCT FROM 'BUY'")
            .contains("order_row.status IS DISTINCT FROM 'SUBMITTED'")
            .contains("order_row.provider_order_ref_hash IS NOT NULL")
            .contains("order_row.provider_tr_id IS NOT NULL")
            .contains("automation_order_integrity_events_v218")
            .contains("automation_order_reservations")
            .contains("automation_runtime_schedule")
            .contains("portfolio_balance_observations")
            .contains("full_owner_account_aliases_v218")
            .contains("SET status='CANCELLED'")
            .contains("unfilled_terminated_quantity=1")
            .contains("CREATE OR REPLACE FUNCTION public.read_mock_order_owner_projection")
            .contains("CREATE OR REPLACE FUNCTION public.read_order_reconciliation_state_authorized_v2")
            .contains("CREATE OR REPLACE FUNCTION public.apply_stored_order_fills_authorized_v2")
            .contains("resolution.broker_cancel_confirmed=false")
            .contains("LOCAL_RETIREMENT_NOT_RECONCILABLE")
            .contains("AND stored.status='CANCELLED' THEN 'LOCAL_RETIRED'")
    }

    @Test
    fun `retirement is append-only and explicitly does not claim broker cancellation`() {
        assertThat(migration)
            .contains("full_owner_order_resolution_events_v219")
            .contains("broker_cancel_confirmed boolean NOT NULL DEFAULT false CHECK (NOT broker_cancel_confirmed)")
            .contains("OWNER_CONFIRMED_LOCAL_UNRECONCILED_ORDER_RETIREMENT")
            .contains("broker-side cancellation is not asserted")
            .contains("CREATE TRIGGER full_owner_order_resolution_events_append_only_v219")
            .contains("ALTER TABLE public.orders FORCE ROW LEVEL SECURITY")
            .doesNotContain("DELETE FROM public.orders")
            .doesNotContain("MOCK_ORDER_CANCELLED")
    }
}
