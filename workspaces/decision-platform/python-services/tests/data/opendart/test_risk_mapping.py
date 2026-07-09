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
    assert mapping.active_by_code["OPENDART:cvbdIsDecsn"].calibration_status == "policy_v1_unvalidated"
    assert mapping.active_by_code["OPENDART:cvbdIsDecsn"].evidence_level == "B_CONFLICTING_KOREA_MARKET_EVIDENCE"
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
