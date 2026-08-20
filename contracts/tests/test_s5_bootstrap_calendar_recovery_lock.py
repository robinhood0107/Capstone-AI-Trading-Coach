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
                "autonomy",
                "calendar",
                "contractId",
                "coverage",
                "derivedDimensions",
                "diagnostics",
                "divergence",
                "downstream",
                "holidayAuthority",
                "issue",
                "packet",
                "recovery",
                "trainingAppend",
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

    def test_kis_coverage_is_bound_to_collected_krx_trading_evidence(self) -> None:
        """union 종목 전원에게 전수 커버리지를 요구하면 상장폐지 종목에서 충족 불가가 된다.

        실측: 010620은 KRX와 KIS 양쪽에서 정확히 910 session(2022-03-29..2025-12-12)이고 그 뒤
        거래 증거가 없다. 두 provider가 일치하므로 불일치가 아니라 상장폐지다. 수집된 KRX 일별
        projection이 커버리지 권위이며, 요구는 그 증거와의 정확한 일치다.
        """

        coverage = json.loads(CATALOG.read_text(encoding="utf-8"))["coverage"]

        self.assertEqual(
            "COLLECTED_KRX_DAILY_TRADING_EVIDENCE", coverage["kisCoverageAuthority"]
        )
        self.assertEqual(
            "EXACT_MATCH_WITH_KRX_TRADING_EVIDENCE", coverage["kisCoverageRule"]
        )
        # 고정 ETF는 일별 stock projection에 없고 월별 etf_bydd_trd에만 나타난다.
        self.assertEqual("FULL_RAW_WINDOW", coverage["fixedEtfCoverageRule"])
        # rolling window는 종목 자기 행에 대한 위치 기반이라 중간 결손이 feature 의미를 바꾼다.
        self.assertTrue(coverage["tradedSessionsMustBeContiguous"])
        # window는 query 신원에 들어간다. 종목별로 좁히면 봉인된 chunk가 도달 불가가 된다.
        self.assertEqual("PACKET_RAW_WINDOW", coverage["pagingWindowSource"])
        # 역사가 정확히 100의 배수로 끝나면 응답 모양만으로는 "더 없음"을 구분할 수 없다.
        self.assertTrue(coverage["pagingStopsWhenEvidenceIsSatisfied"])
        # 커버리지 결손은 별도 sidecar가 아니라 진단 원장 한 곳에 남는다.
        self.assertEqual("diagnostics.jsonl", coverage["diagnosticLedger"])
        self.assertTrue(coverage["ledgerNamesDivergingSymbol"])
        self.assertFalse(coverage["ledgerCarriesProviderPayload"])

    def test_diagnostic_ledger_lets_the_code_decide_and_fails_closed(self) -> None:
        """실패가 이유를 들고 나오지 않으면 코드가 재시도·제외·정지를 고를 수 없다.

        분류를 선언하지 않은 예외를 재시도나 제외로 넘기면 승인 호출을 태우거나 데이터를 조용히
        축소한다. 그래서 기본값은 CONTRACT_VIOLATION이다.
        """

        diagnostics = json.loads(CATALOG.read_text(encoding="utf-8"))["diagnostics"]

        self.assertEqual("diagnostics.jsonl", diagnostics["ledgerFile"])
        self.assertTrue(diagnostics["appendOnly"])
        self.assertFalse(diagnostics["carriesProviderPayload"])
        self.assertEqual(
            [
                "BUDGET_EXHAUSTED",
                "CONTRACT_VIOLATION",
                "EVIDENCE_GAP",
                "RETRYABLE_TRANSIENT",
            ],
            diagnostics["outcomeClasses"],
        )
        self.assertEqual(
            "CONTRACT_VIOLATION", diagnostics["unclassifiedFailureOutcome"]
        )
        # 보고는 실패 분류가 아니다. 섞으면 원장을 읽는 코드가 재시도할 것이 있다고 오해한다.
        self.assertTrue(diagnostics["reportKindsAreNotOutcomeClasses"])
        for kind in diagnostics["reportKinds"]:
            self.assertNotIn(kind, diagnostics["outcomeClasses"])
        # 증거 있는 제외라도 대규모면 조용한 축소다.
        self.assertEqual(0.01, diagnostics["maxExcludedUnitRatio"])
        # 진단을 남기지 못하는 것과 데이터가 틀린 것은 다르다.
        self.assertFalse(diagnostics["recordingFailureChangesCollectionOutcome"])

    def test_divergence_block_stays_a_deletable_gate_token(self) -> None:
        """존재가 차단을 뜻하는 파일은 append-only 원장으로 접을 수 없다.

        계약이 unresolvedBlockStopsResume으로 못 박았고 실행 경로가 파일 존재를 게이트로 쓴다.
        원장은 "해소됨"을 표현할 수 없으므로 토큰은 별도로 남기고 사건만 미러링한다.
        """

        divergence = json.loads(CATALOG.read_text(encoding="utf-8"))["divergence"]

        self.assertEqual("calendar-divergence-candidates.json", divergence["blockFile"])
        self.assertTrue(divergence["blockIsGateTokenNotLedgerEntry"])
        self.assertTrue(divergence["mirroredToDiagnosticLedger"])
        blocking = [
            item
            for item in divergence["evidenceClasses"]
            if item["evidence"] == "EMPTY_DAILY_PROJECTION"
        ]
        self.assertEqual(1, len(blocking))
        self.assertTrue(blocking[0]["unresolvedBlockStopsResume"])
    def test_autonomy_phases_match_where_a_tick_can_actually_stop(self) -> None:
        """코드가 지킬 수 없는 구분을 상태로 만들면 전이 검증이 자기 모순을 잡는다.

        provider별로 나누고 싶었지만 execute_bootstrap_materialization이 KRX·KIS·ECOS·bundle을
        한 호출로 수행한다. 실제로 멈출 수 있는 경계만 단계로 둔다.
        """

        autonomy = json.loads(CATALOG.read_text(encoding="utf-8"))["autonomy"]

        self.assertEqual(
            ["MATERIALIZING", "QUALIFYING", "SERVING", "NEEDS_HUMAN"],
            autonomy["phases"],
        )
        self.assertTrue(autonomy["forwardOnlyExceptRequalification"])
        self.assertEqual("SERVING", autonomy["requalificationReentersFrom"])
        self.assertTrue(autonomy["needsHumanIsTerminalWithoutOperator"])
        self.assertTrue(autonomy["stateHistoryAppendOnly"])
        # tick 중간 종료가 안전한 것은 journal이 이미 query 단위 멱등성을 보장하기 때문이다.
        self.assertTrue(autonomy["tickIsIdempotent"])
        self.assertTrue(autonomy["tickReliesOnJournalQueryIdempotence"])

    def test_no_progress_is_not_failure_and_activation_stays_manual(self) -> None:
        """무진척을 실패로 보면 watchdog이 계속 울려 곧 무시된다.

        자동 재학습은 열되 pointer 전환은 사람이 한다. 서빙 모델이 승인 없이 바뀌지 않는다.
        """

        autonomy = json.loads(CATALOG.read_text(encoding="utf-8"))["autonomy"]

        self.assertEqual(
            {"progress": 0, "noProgress": 1, "needsHuman": 2}, autonomy["exitCodes"]
        )
        self.assertTrue(autonomy["noProgressIsNotFailure"])
        self.assertTrue(autonomy["automaticRetrain"])
        self.assertFalse(autonomy["automaticModelActivation"])
        self.assertTrue(autonomy["activationRemainsManualCas"])
    def test_training_append_grows_the_dataset_without_recollecting_history(self) -> None:
        """일일 수집분이 누적돼야 코드가 알아서 갱신한다.

        packet window를 옮기면 KIS query 신원이 전부 바뀌어 승인 상한만큼 재수집이 필요해진다.
        그래서 window 밖 새 세션만 별도 이름공간으로 쌓는다.
        """

        append = json.loads(CATALOG.read_text(encoding="utf-8"))["trainingAppend"]

        self.assertTrue(append["packetWindowUnchanged"])
        self.assertEqual(0, append["historyRecollection"])
        self.assertEqual("BUNDLE_UNION_APPEND", append["trainingWindowDerivedFrom"])
        self.assertTrue(append["trainingWindowKeepsApprovedDimensions"])
        # cutoff를 그대로 두면 append된 세션의 label maturity가 cutoff보다 늦어 PIT가 깨진다.
        self.assertTrue(append["trainingWindowCutoffRederivedFromLatestSession"])

    def test_training_append_index_is_append_only_and_bounded(self) -> None:
        """index를 고쳐 쓰면 학습 데이터셋이 조용히 달라진다."""

        append = json.loads(CATALOG.read_text(encoding="utf-8"))["trainingAppend"]

        self.assertTrue(append["indexAppendOnly"])
        self.assertTrue(append["replayIsIdempotent"])
        self.assertTrue(append["conflictingSessionEvidenceRefused"])
        self.assertEqual(41, append["maxChunksPerSession"])
        # 경로 참조는 owner-private 컨테인먼트를 깬다.
        self.assertTrue(append["chunksCopiedNotReferenced"])
        # 휴장일은 달력 권위가 이미 다음 session을 고르므로 별도 no-op 분기가 없다.
        self.assertTrue(append["holidayTickIsNoOpByCalendarAuthority"])
        # warm-up 역사를 못 채운 새 멤버는 그 달만 제외되고 원장에 남는다.
        self.assertTrue(append["newMonthlyMemberWithoutWarmupIsEvidenceGap"])
    def test_derived_dimensions_are_not_duplicated_as_literals(self) -> None:
        """이번 실물화에서 멈춘 7건 중 3건이 이 부류였다.

        KIS 행 상한은 union 180 시절 리터럴이 남아 실제 수집을 거부했고, ECOS page 범위는 요청당
        행 상한과 한 번도 맞춰지지 않았고, chunk 길이는 그 상한을 넘겼다. 유도식이 상한의 유일한
        정의여야 승인 차원을 바꿀 때 두 곳이 어긋나지 않는다.
        """

        derived = json.loads(CATALOG.read_text(encoding="utf-8"))["derivedDimensions"]

        self.assertFalse(derived["literalDuplicationOfDerivedDimension"])
        self.assertEqual(
            "RAW_SESSION_COUNT - WARMUP_SESSIONS - LABEL_TAIL_SESSIONS",
            derived["eligibleSessionCount"],
        )
        self.assertEqual("ELIGIBLE_SESSION_COUNT", derived["walkForwardExpectedSessions"])
        self.assertEqual(
            "HORIZON_UNION_SIZE * ceil(RAW_SESSION_COUNT / 100)", derived["kisMaxGet"]
        )
        self.assertEqual(
            "HORIZON_UNION_SIZE * RAW_SESSION_COUNT", derived["kisSourceRowCap"]
        )
        self.assertEqual("<= ECOS_MAX_ROWS_PER_REQUEST", derived["ecosChunkDays"])
        # 유도 불가한 계약 상수를 억지로 유도하면 의미가 사라진다.
        self.assertTrue(derived["walkForwardBlockSizesRemainLiteral"])

if __name__ == "__main__":
    unittest.main()
