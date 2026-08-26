from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any


RECEIVED_PTH_SHA256 = "220d26e99bb571e5be1a5c86d527079893eb9db67b423128116923b924d7f471"
RECEIVED_CSV_SHA256 = "386d9c2c0422ac56c160744dc61cd3990ede5cdb6f6867043f75ee8e9d8e902d"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_received_file(path: Path, expected: str, label: str) -> str:
    observed = sha256_file(path)
    if observed != expected:
        raise ValueError(f"{label} SHA-256 mismatch: expected={expected} observed={observed}")
    return observed


def _assert_finite(value: Any, pointer: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_finite(item, f"{pointer}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_finite(item, f"{pointer}[{index}]")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite number at {pointer}")


def load_and_verify_preview(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    required = {"stock_code", "date", "prediction", "recent_prediction", "backtest", "_preview"}
    missing = sorted(required.difference(payload))
    if missing:
        raise ValueError(f"preview artifact missing fields: {', '.join(missing)}")
    preview = payload["_preview"]
    if preview.get("classification") != "LEGACY_RECEIVED_PREVIEW" or preview.get("realTeamB") is not False:
        raise ValueError("preview artifact must be explicitly non-production")
    _assert_finite(payload)
    return payload


def mark_preview(path: Path, pth_sha256: str, csv_sha256: str) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    payload["_preview"] = {
        "classification": "LEGACY_RECEIVED_PREVIEW",
        "realTeamB": False,
        "teamBRealArtifactMissing": True,
        "providerCalls": 0,
        "receivedPthSha256": pth_sha256,
        "receivedCsvSha256": csv_sha256,
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return load_and_verify_preview(path)
