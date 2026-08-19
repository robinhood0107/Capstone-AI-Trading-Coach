from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "contracts/catalogs/s5-bootstrap-calendar-recovery-lock.v1.json"


class S5BootstrapCalendarRecoveryLockTest(unittest.TestCase):
    def test_calendar_authority_and_correction_are_exact(self) -> None:
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))

        self.assertEqual(
            {
                "calendar",
                "contractId",
                "divergence",
                "downstream",
                "holidayAuthority",
                "issue",
                "packet",
                "recovery",
            },
            set(payload),
        )
        self.assertEqual("s5-bootstrap-calendar-recovery-lock.v1", payload["contractId"])
        self.assertEqual("4.13.2", payload["calendar"]["baseVersion"])
        self.assertEqual(
            "xkrx-4.13.2-kis-corrections-v1",
            payload["calendar"]["policyVersion"],
        )
        self.assertEqual(
            [
                {
                    "evidenceClass": "CONTRACT_LOCKED_CALENDAR_CORRECTION",
                    "isOpen": False,
                    "reason": "2026_LOCAL_ELECTION_MARKET_CLOSURE",
                    "sessionDate": "2026-06-03",
                }
            ],
            payload["calendar"]["corrections"],
        )

    def test_recovery_is_fail_closed_and_cannot_expand_downstream_authority(self) -> None:
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))

        self.assertEqual(4_441, payload["recovery"]["approvedKrxMaxPhysicalGet"])
        self.assertTrue(payload["recovery"]["blockBeforeProviderClientWhenCapacityExhausted"])
        self.assertTrue(payload["recovery"]["adoptionJournalRequired"])
        self.assertTrue(payload["packet"]["recoveryBindingRequired"])
        self.assertEqual("CALENDAR_RECOVERY", payload["packet"]["recoveryLineageMode"])
        self.assertEqual(0, payload["recovery"]["successfulChunkRecall"])
        self.assertEqual(0, payload["recovery"]["providerCallsDuringAssessment"])
        self.assertEqual(
            {
                "openApiChange": 0,
                "orderWiring": 0,
                "providerRawArtifactInGit": 0,
                "riskDecisionWiring": 0,
                "signalContractChange": 0,
            },
            payload["downstream"],
        )

    def test_fresh_packet_authority_is_one_shot_per_approved_root(self) -> None:
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))
        packet = payload["packet"]

        self.assertEqual(
            "fresh-bootstrap-authority.v1.json", packet["freshAuthorityFile"]
        )
        self.assertEqual(
            "IMMUTABLE_ABSENT_TO_SHA_CAS", packet["freshAuthorityMode"]
        )
        self.assertEqual(1, packet["freshExecutionsPerApprovedRoot"])
        self.assertEqual(".bootstrap-root.lock", packet["rootLockFile"])

    def test_superseded_allowance_is_bound_to_proven_consumed_calls(self) -> None:
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))
        recovery = payload["recovery"]

        self.assertEqual(
            "PROVEN_SUPERSEDED_CONSUMED_CALLS",
            recovery["supersededAllowanceDerivation"],
        )
        self.assertTrue(recovery["supersededAllowanceEqualsSupersededConsumedCalls"])
        self.assertEqual("CALENDAR_RECOVERY", recovery["supersededAllowanceLineage"])
        self.assertEqual(8, recovery["maxSupersededAllowance"])
        self.assertTrue(recovery["logicalQueryCountUnchangedByAllowance"])
        self.assertEqual(2, recovery["physicalAttemptsPerLogicalQuery"])
        self.assertEqual(
            [
                "PACKET_BYTES",
                "RECOVERY_BINDING_PREIMAGE",
                "RECOVERY_RECEIPT",
                "ADOPTION_JOURNAL",
            ],
            recovery["allowanceBindingSurfaces"],
        )

    def test_calendar_divergence_is_a_distinct_fail_closed_result(self) -> None:
        divergence = json.loads(CATALOG.read_text(encoding="utf-8"))["divergence"]

        self.assertEqual("CALENDAR_DIVERGENCE_SUSPECTED", divergence["result"])
        self.assertEqual(
            "EMPTY_DAILY_PROJECTION_ON_CLAIMED_SESSION", divergence["detection"]
        )
        self.assertEqual(
            "calendar-divergence-candidates.json", divergence["blockFile"]
        )
        self.assertFalse(divergence["resumePacketAuthored"])
        self.assertEqual(0, divergence["providerCallsDuringBlock"])
        self.assertTrue(divergence["unresolvedBlockStopsResume"])

    def test_holiday_authority_is_candidate_scoped_and_budget_separated(self) -> None:
        authority = json.loads(CATALOG.read_text(encoding="utf-8"))["holidayAuthority"]

        self.assertEqual("CTCA0903R", authority["transactionId"])
        self.assertEqual("kis-holiday-ctca0903r", authority["sourceId"])
        self.assertEqual("LIVE_ONLY", authority["mode"])
        self.assertEqual(32, authority["maxPhysicalCalls"])
        self.assertEqual(
            "DIVERGENCE_CANDIDATE_SESSIONS_ONLY", authority["scope"]
        )
        self.assertTrue(authority["separateFromBootstrapBudget"])
        self.assertEqual("decision_collector", authority["requiredRole"])
        self.assertEqual("trading_sessions", authority["canonicalTable"])
        self.assertEqual(
            "CALENDAR_AUTHORITY_UNVERIFIED",
            authority["unobservedCorrectionResult"],
        )
        self.assertTrue(authority["packetHashRemainsStatic"])


if __name__ == "__main__":
    unittest.main()
