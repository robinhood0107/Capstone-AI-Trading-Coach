from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.naver import storage
from app.data.naver.storage import (
    NAVER_SNAPSHOT_MAX_BYTES,
    NaverSnapshotStorageError,
    publish_naver_snapshot,
    serialize_naver_snapshot,
)


_REPO_ROOT = Path(__file__).resolve().parents[6]
_EXAMPLE_PATH = _REPO_ROOT / "contracts" / "examples" / "naver_news_metadata_snapshot.valid.json"
_SNAPSHOT_PATH = "naver/2026/07/14/00000000-0000-4000-8000-000000000001/snapshot.json"


def _valid_snapshot() -> dict[str, Any]:
    return json.loads(_EXAMPLE_PATH.read_text(encoding="utf-8"))


def _manifest_bytes(snapshot_bytes: bytes) -> bytes:
    return canonical_json_bytes(
        {
            "schemaVersion": 1,
            "source": "naver",
            "snapshotPath": _SNAPSHOT_PATH,
            "snapshotSha256": hashlib.sha256(snapshot_bytes).hexdigest(),
        }
    )


def test_snapshot_serialization_is_deterministic_sanitized_contract_only() -> None:
    first = _valid_snapshot()
    second = dict(reversed(list(first.items())))

    first_bytes = serialize_naver_snapshot(first)
    second_bytes = serialize_naver_snapshot(second)

    assert first_bytes == second_bytes
    assert first_bytes.endswith(b"\n")
    assert b"rawPayload" not in first_bytes
    assert b"providerHeaders" not in first_bytes
    assert b"clientSecret" not in first_bytes


@pytest.mark.parametrize("unsafe_key", ["rawPayload", "providerHeaders", "clientSecret"])
def test_snapshot_rejects_raw_or_credential_fields(unsafe_key: str) -> None:
    payload = _valid_snapshot()
    payload[unsafe_key] = "validation-dummy-secret"

    with pytest.raises(NaverSnapshotStorageError, match="snapshot"):
        serialize_naver_snapshot(payload)


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "http://localhost/article/1",
        "https://news.example.test/article/1?client_secret=synthetic",
    ],
)
def test_snapshot_rejects_urls_that_bypass_metadata_sanitization(unsafe_url: str) -> None:
    payload = _valid_snapshot()
    payload["queries"][0]["items"][0]["originalUrl"] = unsafe_url

    with pytest.raises(NaverSnapshotStorageError, match="snapshot"):
        serialize_naver_snapshot(payload)


def test_snapshot_enforces_four_mib_after_canonical_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert NAVER_SNAPSHOT_MAX_BYTES == 4 * 1024 * 1024
    monkeypatch.setattr(
        storage,
        "canonical_json_bytes",
        lambda _: b"x" * (NAVER_SNAPSHOT_MAX_BYTES + 1),
    )

    with pytest.raises(NaverSnapshotStorageError, match="4 MiB|size"):
        serialize_naver_snapshot(_valid_snapshot())


def test_publish_connects_canonical_bytes_to_secure_shared_store(tmp_path: Path) -> None:
    payload = _valid_snapshot()
    snapshot_bytes = serialize_naver_snapshot(payload)

    published = publish_naver_snapshot(
        root=tmp_path / "snapshots",
        snapshot_path=_SNAPSHOT_PATH,
        snapshot=payload,
        manifest_bytes=_manifest_bytes(snapshot_bytes),
    )

    assert published.snapshot_path.read_bytes() == snapshot_bytes
    assert published.snapshot_sha256 == hashlib.sha256(snapshot_bytes).hexdigest()
    assert published.manifest_path.exists()
