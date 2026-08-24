import pytest

from app.data.opendart.risk_mapping import (
    RiskMappingValidationError,
    load_default_risk_mapping,
    load_risk_mapping_from_dict,
)


def test_default_mapping_keeps_active_and_blocked_entries_separate() -> None:
    mapping = load_default_risk_mapping()

    assert mapping.version == "s1.2-v1"
    assert mapping.active_by_code["OPENDART:piicDecsn"].score == 0.6
    assert mapping.active_by_code["OPENDART:cvbdIsDecsn"].score == 0.6
    assert (
        mapping.active_by_code["OPENDART:cvbdIsDecsn"].calibration_status == "policy_v1_unvalidated"
    )
    assert (
        mapping.active_by_code["OPENDART:cvbdIsDecsn"].evidence_level
        == "B_CONFLICTING_KOREA_MARKET_EVIDENCE"
    )
    assert mapping.active_by_code["OPENDART:lwstLg"].score == 0.4
    assert "OPENDART:KRX_MANAGEMENT_OR_DELISTING" in mapping.blocked_by_code


def test_high_severity_distress_events_are_active_with_official_endpoint_evidence() -> None:
    mapping = load_default_risk_mapping()

    # going-concern distress는 최상위(1.0), 운영중단·자본잠식 신호는 한 단계 낮은 정책 등급(0.8)으로 둔다.
    expected_scores = {
        "OPENDART:dfOcr": 1.0,
        "OPENDART:ctrcvsBgrq": 1.0,
        "OPENDART:dsRsOcr": 1.0,
        "OPENDART:bnkMngtPcbg": 1.0,
        "OPENDART:bsnSp": 0.8,
        "OPENDART:crDecsn": 0.8,
    }
    for code, score in expected_scores.items():
        entry = mapping.active_by_code[code]
        assert entry.score == score
        # endpoint identity 근거와 미검증 보정 상태를 모든 신규 고위험 mapping에 강제한다.
        assert entry.official_endpoint == code.split(":", 1)[1]
        assert entry.calibration_status == "policy_v1_unvalidated"
        # 상태 지속형 고위험 이벤트는 30일 뒤 조용히 사라지면 안 되므로 장기 유효기간을 갖는다.
        assert entry.effective_window_days == 365


def test_announcement_effect_events_use_short_window_and_persisting_use_long() -> None:
    mapping = load_default_risk_mapping()

    # 공시효과형(증자/CB/소송)은 30일, 상태 지속형(감사의견)은 365일.
    assert mapping.active_by_code["OPENDART:piicDecsn"].effective_window_days == 30
    assert mapping.active_by_code["OPENDART:cvbdIsDecsn"].effective_window_days == 30
    assert mapping.active_by_code["OPENDART:lwstLg"].effective_window_days == 30
    assert mapping.active_by_code["OPENDART:accnutAdtorNmNdAdtOpinion"].effective_window_days == 365


def test_active_mapping_requires_effective_window_days() -> None:
    with pytest.raises(RiskMappingValidationError):
        load_risk_mapping_from_dict(
            {
                "version": "bad",
                "entries": [
                    {
                        "code": "OPENDART:piicDecsn",
                        "label": "유상증자 결정",
                        "status": "active",
                        "score": 0.6,
                        "official_endpoint": "piicDecsn",
                        "evidence_level": "A_OFFICIAL_STRUCTURED_AND_KOREA_MARKET_EVIDENCE",
                        "calibration_status": "policy_v1_unvalidated",
                    }
                ],
            }
        )


def test_active_mapping_rejects_unbounded_effective_window() -> None:
    with pytest.raises(RiskMappingValidationError, match="effective_window_days"):
        load_risk_mapping_from_dict(
            {
                "version": "bad-window",
                "entries": [
                    {
                        "code": "OPENDART:piicDecsn",
                        "label": "유상증자 결정",
                        "status": "active",
                        "score": 0.6,
                        "official_endpoint": "piicDecsn",
                        "evidence_level": "A_OFFICIAL_STRUCTURED_AND_KOREA_MARKET_EVIDENCE",
                        "calibration_status": "policy_v1_unvalidated",
                        "effective_window_days": 3651,
                    }
                ],
            }
        )


def test_s1_2b_extension_events_are_active_with_official_endpoint_and_window() -> None:
    mapping = load_default_risk_mapping()

    # S1.2b: BW/EB는 30일(공시효과형), 합병·분할·분할합병·영업양도는 90일(reorg) 정책.
    expected_windows = {
        "OPENDART:bdwtIsDecsn": 30,
        "OPENDART:exbdIsDecsn": 30,
        "OPENDART:cmpMgDecsn": 90,
        "OPENDART:cmpDvDecsn": 90,
        "OPENDART:cmpDvmgDecsn": 90,
        "OPENDART:bsnTrfDecsn": 90,
    }
    for code, window in expected_windows.items():
        entry = mapping.active_by_code[code]
        assert entry.score == 0.6
        assert entry.official_endpoint == code.split(":", 1)[1]
        assert entry.calibration_status == "policy_v1_unvalidated"
        assert entry.effective_window_days == window


def test_active_mapping_requires_official_code_or_endpoint_evidence() -> None:
    with pytest.raises(RiskMappingValidationError):
        load_risk_mapping_from_dict(
            {
                "version": "bad",
                "entries": [
                    {
                        "code": "OPENDART:KRX_MANAGEMENT_OR_DELISTING",
                        "label": "관리종목 지정",
                        "status": "active",
                        "score": 1.0,
                    }
                ],
            }
        )


def test_active_mapping_requires_evidence_metadata() -> None:
    with pytest.raises(RiskMappingValidationError):
        load_risk_mapping_from_dict(
            {
                "version": "bad",
                "entries": [
                    {
                        "code": "OPENDART:piicDecsn",
                        "label": "유상증자 결정",
                        "status": "active",
                        "score": 0.6,
                        "official_endpoint": "piicDecsn",
                    }
                ],
            }
        )


def test_blocked_mapping_requires_source_gap_reason() -> None:
    with pytest.raises(RiskMappingValidationError):
        load_risk_mapping_from_dict(
            {
                "version": "bad",
                "entries": [
                    {
                        "code": "OPENDART:CONTROLLING_SHAREHOLDER_CHANGE",
                        "label": "최대주주 변경",
                        "status": "blocked",
                    }
                ],
            }
        )
