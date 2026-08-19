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
                    "evidenceClass": "CTCA0903R_CONFIRMED_CALENDAR_CORRECTION",
                    "isOpen": False,
                    "reason": "2026_LOCAL_ELECTION_MARKET_CLOSURE",
                    "sessionDate": "2026-06-03",
                },
                {
                    "evidenceClass": "CTCA0903R_CONFIRMED_CALENDAR_CORRECTION",
                    "isOpen": False,
                    "reason": "2026_CONSTITUTION_DAY_MARKET_CLOSURE",
                    "sessionDate": "2026-07-17",
                },
            ],
            payload["calendar"]["corrections"],
        )
        # correction은 오름차순 고유 날짜여야 하며 evidenceClass는 실제 관측 확정만 허용한다.
        dates = [item["sessionDate"] for item in payload["calendar"]["corrections"]]
        self.assertEqual(sorted(set(dates)), dates)
        self.assertTrue(
            all(
                item["evidenceClass"] == "CTCA0903R_CONFIRMED_CALENDAR_CORRECTION"
                and item["isOpen"] is False
                for item in payload["calendar"]["corrections"]
            )
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
            "calendar-divergence-candidates.json", divergence["blockFile"]
        )
        self.assertEqual(0, divergence["providerCallsDuringBlock"])
        self.assertTrue(divergence["blockBytesDependOnPacketAndCandidatesOnly"])

        by_evidence = {item["evidence"]: item for item in divergence["evidenceClasses"]}
        self.assertEqual({"EMPTY_DAILY_PROJECTION", "SINGLE_SESSION_QUERY_FAILURE"}, set(by_evidence))

        empty = by_evidence["EMPTY_DAILY_PROJECTION"]
        self.assertEqual("EMPTY_DAILY_PROJECTION_ON_CLAIMED_SESSION", empty["detection"])
        self.assertFalse(empty["resumePacketAuthored"])
        self.assertTrue(empty["unresolvedBlockStopsResume"])

        # 단일 실패는 provider 일시 오류와 구분할 수 없어 계약이 허용한 resume을 막지 않는다.
        failure = by_evidence["SINGLE_SESSION_QUERY_FAILURE"]
        self.assertEqual(
            "SINGLE_SESSION_FAILURE_AFTER_HEALTHY_NEIGHBOURS", failure["detection"]
        )
        self.assertTrue(failure["resumePacketAuthored"])
        self.assertFalse(failure["unresolvedBlockStopsResume"])

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

    def test_superseded_correction_generations_are_preserved(self) -> None:
        calendar = json.loads(CATALOG.read_text(encoding="utf-8"))["calendar"]

        self.assertTrue(calendar["supersededGenerationsArePreserved"])
        generations = calendar["supersededCorrectionSets"]
        # 수정 전 pinned base와 첫 correction 세대를 모두 보존해야 이미 소비한 packet과
        # journal을 재수집 없이 read-only로 검증할 수 있다.
        self.assertEqual([[], ["2026-06-03"]], [item["sessionDates"] for item in generations])
        self.assertTrue(
            all(item["usage"] == "READ_ONLY_RECOVERY_VALIDATION" for item in generations)
        )

        current = calendar["correctionSetSha256"]
        digests = [item["correctionSetSha256"] for item in generations]
        self.assertEqual(len(set(digests)), len(digests))
        self.assertNotIn(current, digests)

        # 보존된 세대는 현재 correction 목록의 진부분집합이어야 한다.
        current_dates = [item["sessionDate"] for item in calendar["corrections"]]
        for item in generations:
            self.assertLess(set(item["sessionDates"]), set(current_dates))

    def test_recovery_can_chain_from_a_superseded_generation(self) -> None:
        recovery = json.loads(CATALOG.read_text(encoding="utf-8"))["recovery"]

        self.assertTrue(recovery["chainableFromSupersededRecoveryPacket"])
        # prior는 체인 head다. supersede 사유가 달력 correction이면 세대 해시가 바뀌지만 승인 상한
        # 변경이면 같은 세대 안에서 갈리므로, 세대 해시가 아니라 체인 관계가 head를 정한다.
        self.assertTrue(recovery["priorPacketIsChainHead"])
        # head는 소비 증거를 모두 포함하는 run이다. ordinal은 세대마다 다시 붙어 신원이 아니다.
        self.assertTrue(recovery["chainHeadDerivedFromConsumedQueryMultiset"])
        # 같은 packet을 prior로 삼으면 체인이 자기 자신을 가리켜 누적 회계가 끊긴다.
        self.assertTrue(recovery["supersedeMustChangePacketIdentity"])
        # prior journal은 그 journal이 봉인된 세대의 clock으로만 읽어야 한다.
        self.assertTrue(recovery["priorJournalReadUnderItsOwnGeneration"])
        # 체인에서는 superseded query가 세대마다 누적되므로 실패 query 하나로 표현되지 않는다.
        self.assertTrue(recovery["receiptCarriesSupersededQuerySet"])
        self.assertTrue(
            recovery["supersededQueryIdentityResolvedAcrossApprovedGenerations"]
        )

    def test_supersede_covers_every_provider_that_can_consume_calls(self) -> None:
        """KRX만 supersede된다는 가정은 달력 correction 사유에서만 성립했다.

        local finalization 결함이 드러나면 이미 소비한 KIS 호출도 세대와 함께 이관해야 한다.
        그러지 않으면 소진된 query가 영구히 열리지 않아 packet이 완주 불가가 된다.
        """

        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        recovery = catalog["recovery"]

        self.assertEqual(["KRX", "KIS"], recovery["supersededProviders"])
        self.assertEqual(["KRX", "KIS"], recovery["adoptedProvidersCarryingChunks"])
        # Access token 성공은 값이 보존되지 않아 채택할 수 없다. superseded로만 이관된다.
        self.assertFalse(recovery["kisTokenSuccessAdoptable"])
        # 이관된 소비 원장이 새 세대의 재시도 자격을 먹으면 이관 자체가 무의미해진다.
        self.assertEqual("CURRENT_GENERATION", recovery["perQueryRetryBudgetScope"])
        # 그래도 누적 예산은 SUPERSEDED_CONSUMED까지 세므로 상한은 그대로 지켜진다.
        self.assertTrue(recovery["supersededConsumedCallsCountTowardCumulativeBudget"])
        self.assertEqual(2, recovery["physicalAttemptsPerLogicalQuery"])
        # KIS 논리 query는 수집된 KRX 증거에서 파생돼 packet만으로 열거할 수 없다.
        self.assertTrue(recovery["kisLogicalQuerySetDerivedFromCollectedKrxEvidence"])


if __name__ == "__main__":
    unittest.main()
