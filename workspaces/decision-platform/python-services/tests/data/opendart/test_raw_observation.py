from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

from app.data.opendart.raw_observation import request_fingerprint, write_raw_observation


def test_request_fingerprint_keeps_only_path_and_query_keys() -> None:
    fingerprint = request_fingerprint(
        "GET",
        "/api/list.json",
        {
            "crtfc_key": "REAL_OPEN_DART_KEY",
            "corp_code": "00126380",
            "bgn_de": "20260609",
            "end_de": "20260709",
        },
    )

    assert fingerprint == "GET /api/list.json?keys=bgn_de,corp_code,crtfc_key,end_de"
    assert "REAL_OPEN_DART_KEY" not in fingerprint
    assert "00126380" not in fingerprint


def test_write_raw_observation_masks_payload_and_writes_only_under_data_dir(tmp_path: Path) -> None:
    observation = write_raw_observation(
        data_dir=tmp_path,
        source_id="opendart_disclosure_list",
        method="GET",
        path="/api/list.json",
        request_params={"crtfc_key": "REAL_OPEN_DART_KEY", "corp_code": "00126380"},
        payload={
            "status": "000",
            "message": "정상",
            "crtfc_key": "REAL_OPEN_DART_KEY",
            "list": [{"corp_code": "00126380", "report_nm": "테스트"}],
        },
        retrieved_at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
        window_from=date(2026, 6, 9),
        window_to=date(2026, 7, 9),
        normalized_status="OK",
        known_secrets=["REAL_OPEN_DART_KEY"],
    )

    raw_path = Path(observation.raw_storage_uri)
    stored = raw_path.read_text(encoding="utf-8")

    assert raw_path.is_relative_to(tmp_path / "raw" / "opendart_disclosure_list")
    assert observation.payload_hash == hashlib.sha256(raw_path.read_bytes()).hexdigest()
    assert observation.request_fingerprint == "GET /api/list.json?keys=corp_code,crtfc_key"
    assert observation.normalized_status == "OK"
    assert observation.window_from == date(2026, 6, 9)
    assert "REAL_OPEN_DART_KEY" not in stored
    assert '"crtfc_key": "***"' in stored


def test_failed_observation_masks_error_message(tmp_path: Path) -> None:
    observation = write_raw_observation(
        data_dir=tmp_path,
        source_id="opendart_disclosure_list",
        method="GET",
        path="/api/list.json",
        request_params={"crtfc_key": "REAL_OPEN_DART_KEY"},
        payload={"status": "010", "message": "등록되지 않은 키 REAL_OPEN_DART_KEY"},
        retrieved_at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
        window_from=None,
        window_to=None,
        normalized_status="FAILED",
        error_code="010",
        error_message="등록되지 않은 키 REAL_OPEN_DART_KEY",
        known_secrets=["REAL_OPEN_DART_KEY"],
    )

    assert observation.error_code == "010"
    assert observation.error_message == "등록되지 않은 키 ***"
