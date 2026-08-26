import json
from pathlib import Path

import pytest

from preview_contract import load_and_verify_preview, verify_received_file


def test_received_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    received = tmp_path / "model.pth"
    received.write_bytes(b"not the received model")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_received_file(received, "0" * 64, "received PTH")


def test_preview_must_be_explicitly_non_production(tmp_path: Path) -> None:
    artifact = tmp_path / "preview.json"
    artifact.write_text(
        json.dumps(
            {
                "stock_code": "005930.KS",
                "date": "2026-08-24",
                "prediction": {},
                "recent_prediction": [],
                "backtest": {},
                "_preview": {
                    "classification": "LEGACY_RECEIVED_PREVIEW",
                    "realTeamB": False,
                },
            }
        ),
        encoding="utf-8",
    )
    assert load_and_verify_preview(artifact)["_preview"]["realTeamB"] is False
