from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.data.calendar.errors import PrivacyProjectionError
from app.data.calendar.privacy import (
    assert_sanitized_payload,
    project_ds004_ownership,
    sanitize_source_ref,
)


@pytest.mark.parametrize(
    "source_type, row, expected_category",
    [
        (
            "majorstock",
            {
                "corp_code": "00126380",
                "rcept_dt": "20260722",
                "report_tp": "신규",
                "stkqy": "1,000",
                "stkrt": "5.25",
                "repror": "홍길동",
                "corp_name": "삼성전자",
                "report_resn": "개인 주소 서울시 어딘가",
            },
            "MAJOR_HOLDER",
        ),
        (
            "elestock",
            {
                "corp_code": "00126380",
                "rcept_dt": "2026-07-22",
                "isu_exctv_rgist_at": "Y",
                "isu_main_shrholdr": "N",
                "sp_stock_lmp_cnt": "500",
                "sp_stock_lmp_rate": "0.25",
                "repror": "김개인",
                "isu_exctv_ofcps": "대표이사",
                "jurir_no": "110111-1234567",
                "bizr_no": "123-45-67890",
            },
            "REGISTERED_EXECUTIVE",
        ),
    ],
)
def test_ds004_projection_keeps_only_allowlisted_non_pii_fields(
    tmp_path: Path,
    source_type: str,
    row: dict[str, str],
    expected_category: str,
) -> None:
    projection = project_ds004_ownership(source_type, row)
    serialized = json.dumps(projection.as_dict(), ensure_ascii=False, sort_keys=True)

    assert projection.corp_code == "00126380"
    assert projection.role_category == expected_category
    assert set(projection.as_dict()) == {
        "corp_code",
        "role_category",
        "occurred_on",
        "share_count",
        "share_ratio_bps",
    }
    for forbidden in ("홍길동", "김개인", "서울시", "대표이사", "110111", "123-45"):
        assert forbidden not in serialized
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"reporter_name": "홍길동"},
        {"raw_response": {"status": "000"}},
        {"request_url": "https://example.invalid/?crtfc_key=value"},
        {"api_key": "secret"},
        {"account_number": "12345678"},
        {"headers": {"authorization": "Bearer value"}},
    ],
)
def test_sanitized_payload_guard_rejects_pii_secret_query_raw_and_headers(
    payload: dict[str, object],
) -> None:
    with pytest.raises(PrivacyProjectionError):
        assert_sanitized_payload(payload)


def test_source_ref_is_opaque_and_does_not_embed_provider_identity_or_query() -> None:
    source_ref = sanitize_source_ref(
        source_id="opendart-structured-events",
        stable_key="00126380:20260722000001",
    )
    assert len(source_ref) == 64
    assert "opendart" not in source_ref
    assert "00126380" not in source_ref
