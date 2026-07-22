from __future__ import annotations

from datetime import date

import pytest

from app.data.calendar.adapters.opendart_events import normalize_opendart_structured_event
from app.data.calendar.errors import AdapterValidationError


@pytest.mark.parametrize(
    "endpoint_id, transition",
    [("bnkMngtPcbg", "OPEN"), ("bnkMngtPcsp", "CLOSE")],
)
def test_bank_management_uses_structured_endpoint_identity(
    endpoint_id: str,
    transition: str,
) -> None:
    event = normalize_opendart_structured_event(
        endpoint_id,
        {
            "corp_code": "00126380",
            "rcept_no": "20260722000001",
            "rcept_dt": "20260722",
            "report_nm": "이 문자열은 상태 매핑 근거가 아니다",
        },
        symbol="005930",
    )

    assert event.event_type == "DISCLOSURE_RISK_STATE"
    assert event.event_date == date(2026, 7, 22)
    assert event.detail == {
        "corp_code": "00126380",
        "state_type": "BANK_MANAGEMENT",
        "transition": transition,
    }
    assert "report_nm" not in event.canonical_json


def test_report_name_alone_cannot_create_or_close_risk_state() -> None:
    with pytest.raises(AdapterValidationError, match="endpoint"):
        normalize_opendart_structured_event(
            "list",
            {
                "corp_code": "00126380",
                "rcept_no": "20260722000001",
                "rcept_dt": "20260722",
                "report_nm": "채권은행 관리절차 중단",
            },
            symbol="005930",
        )
