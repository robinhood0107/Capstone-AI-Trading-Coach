from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "workspaces/decision-platform/spring-api/src/main/resources/db/migration"
    / "V129__post_merge_runtime_integrity.sql"
)
RECOVERY_MIGRATION = MIGRATION.with_name("V130__automatic_mock_lineage_recovery.sql")
DISPLAY_MIGRATION = MIGRATION.with_name("V131__instrument_display_and_position_adoption.sql")
CLOSE_ADOPTION_MIGRATION = MIGRATION.with_name("V132__close_historical_position_adoption.sql")


class P1V129PostMergeRuntimeIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")
        cls.recovery_sql = RECOVERY_MIGRATION.read_text(encoding="utf-8")
        cls.display_sql = DISPLAY_MIGRATION.read_text(encoding="utf-8")
        cls.close_adoption_sql = CLOSE_ADOPTION_MIGRATION.read_text(encoding="utf-8")

    def test_reasoning_answers_fit_the_persisted_history_constraint(self) -> None:
        self.assertIn("citation_coverage >= 0.2", self.sql)
        self.assertIn("REASONING_SENTENCES_PRESENT", self.sql)
        self.assertIn("guardrail_flags = ARRAY['MODEL_KNOWLEDGE_ONLY']", self.sql)

    def test_all_brokerage_outbox_types_are_registered(self) -> None:
        for event_type in (
            "brokerage.mock-order-submitted.v1",
            "brokerage.mock-order-cancel-requested.v1",
            "brokerage.paper-order-accepted.v1",
            "brokerage.paper-order-filled.v1",
            "brokerage.paper-order-cancelled.v1",
        ):
            self.assertIn(event_type, self.sql)

    def test_missing_physical_order_reservation_fails_closed(self) -> None:
        self.assertGreaterEqual(
            self.sql.count("run.physical_submit_count=1"),
            2,
        )
        self.assertGreaterEqual(
            self.sql.count("reservation.run_id=run.run_id"),
            2,
        )
        self.assertIn("unresolved_reconciliation:=", self.sql)

    def test_acceptance_titles_are_sanitized_without_deleting_references(self) -> None:
        self.assertIn("SET title='균형형 원칙'", self.sql)
        self.assertNotIn("DELETE FROM public.principles", self.sql)
        self.assertNotIn("DELETE FROM public.principle_versions", self.sql)

    def test_automatic_recovery_requires_provider_and_local_evidence(self) -> None:
        for evidence in (
            "MOCK_ORDER_SUBMITTED",
            "brokerage.mock-order-submitted.v1",
            "automation_positions",
            "automation_order_reservations",
            "RECOVERY_BASELINE",
        ):
            self.assertIn(evidence, self.recovery_sql)
        self.assertIn("control_row.control_state<>'DISARMED'", self.recovery_sql)
        self.assertIn("automation recovery balance mismatch", self.recovery_sql)

    def test_recovery_updates_account_baselines_without_creating_an_order(self) -> None:
        self.assertIn("baseline_account_digest=full_digest", self.recovery_sql)
        self.assertIn("expected_account_digest_v2=risk_digest", self.recovery_sql)
        self.assertNotIn("INSERT INTO public.orders", self.recovery_sql)
        self.assertNotIn("INSERT INTO public.automation_order_reservations", self.recovery_sql)

    def test_display_catalog_is_exact_31_and_adoption_requires_disarm(self) -> None:
        self.assertEqual(len(re.findall(r"\('\d{6}'", self.display_sql)), 31)
        self.assertIn("control_row.control_state<>'DISARMED'", self.display_sql)
        self.assertIn("automation adoption balance mismatch", self.display_sql)
        self.assertNotIn("SELECT public.p1_adopt_historical_mock_position_v1", self.display_sql)
        self.assertIn("DROP FUNCTION public.p1_adopt_historical_mock_position_v1", self.close_adoption_sql)


if __name__ == "__main__":
    unittest.main()
