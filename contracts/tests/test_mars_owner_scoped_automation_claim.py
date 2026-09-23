"""The shared automation runtime may enumerate/claim only through scoped DB functions."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "../workspaces/decision-platform/spring-api/src/main/resources/db/migration/"
    / "V207__owner_scoped_automation_runtime_claim.sql"
).resolve()


class OwnerScopedAutomationClaimMigrationTest(unittest.TestCase):
    def test_list_is_runtime_only_bounded_and_contains_only_armed_owners(self) -> None:
        migration = MIGRATION.read_text(encoding="utf-8")
        listing = migration.split(
            "$p1_list_armed_automation_users_v1$", maxsplit=2
        )[1]
        self.assertIn("session_user <> 'decision_automation_runtime'", listing)
        self.assertIn("active_count > 100", listing)
        self.assertIn("app_user.status='ACTIVE'", listing)
        self.assertIn("control.control_state='ARMED'", listing)
        self.assertIn("claim.claim_state='ACTIVE'", listing)
        self.assertIn("ORDER BY control.user_id", listing)
        self.assertIn("TO decision_automation_runtime", migration)

    def test_claim_filters_both_replay_and_new_schedule_by_the_requested_owner(self) -> None:
        migration = MIGRATION.read_text(encoding="utf-8")
        claim = migration.split("$p1_claim_automation_session_for_owner_v1$", maxsplit=2)[1]
        self.assertIn("p_owner_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$'", claim)
        self.assertIn("schedule.user_id=p_owner_user_id", claim)
        self.assertIn("WHERE user_id=p_owner_user_id AND session_date=p_session_date", claim)
        self.assertIn("app.automation_owner_user_id',schedule_row.user_id", claim)
        self.assertIn("FOR UPDATE OF schedule,claim", claim)
        self.assertIn("SET claim_token_hash=p_claim_token_hash", claim)
        self.assertIn("AND claim_state='ACTIVE' AND claim_token_hash<>p_claim_token_hash", claim)
        self.assertIn("FROM PUBLIC, decision_app, decision_worker, decision_auth, decision_identity", migration)


if __name__ == "__main__":
    unittest.main()
