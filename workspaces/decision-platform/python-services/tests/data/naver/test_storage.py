from __future__ import annotations

import hashlib
import json
from copy import deepcopy
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
_FOUR_QUERY_EXAMPLE_PATH = (
    _REPO_ROOT / "contracts" / "examples" / "naver_news_metadata_snapshot.valid.json"
)
_ONE_QUERY_EXAMPLE_PATH = (
    _REPO_ROOT
    / "contracts"
    / "examples"
    / "naver_news_metadata_snapshot.one_query.valid.json"
)
_SNAPSHOT_PATH = "naver/2026/07/14/00000000-0000-4000-8000-000000000001/snapshot.json"


def _valid_snapshot() -> dict[str, Any]:
    return json.loads(_FOUR_QUERY_EXAMPLE_PATH.read_text(encoding="utf-8"))


def _one_query_snapshot() -> dict[str, Any]:
    return json.loads(_ONE_QUERY_EXAMPLE_PATH.read_text(encoding="utf-8"))


def _manifest_bytes(
    snapshot_bytes: bytes,
    *,
    operation: str = "naver-news-metadata-collect",
    snapshot_path: str = _SNAPSHOT_PATH,
    query_count: int = 4,
    overrides: dict[str, object] | None = None,
) -> bytes:
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "source": "naver",
        "providerProfile": "naver-legacy",
        "operation": operation,
        "generatedAt": "2026-07-14T01:10:00Z",
        "asOf": "2026-07-14",
        "snapshotPath": snapshot_path,
        "snapshotSha256": hashlib.sha256(snapshot_bytes).hexdigest(),
        "recordCount": 1,
        "countBreakdown": {
            "queryCount": query_count,
            "acceptedItemCount": 1,
            "filteredItemCount": 0,
            "redactedUrlCount": 0,
        },
        "partial": False,
        "coverage": "complete",
        "deferredQueries": 0,
        "physicalAttemptCount": query_count,
        "quotaPolicyVersion": "s1.3-naver-legacy-quota-v1",
        "provenance": {
            "documentationUrl": "https://developers.naver.com/docs/serviceapi/search/news/news.md",
            "policyUrl": "https://developers.naver.com/products/terms/",
        },
        "sanitizationVersion": "s1.3-sanitization-v1",
        "retentionDays": 30,
        "deleteOwner": "decision-platform:source-snapshot-retention",
    }
    if overrides is not None:
        payload.update(overrides)
    return canonical_json_bytes(payload)


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


@pytest.mark.parametrize(
    ("example_path", "expected_query_count"),
    [
        (_ONE_QUERY_EXAMPLE_PATH, 1),
        (_FOUR_QUERY_EXAMPLE_PATH, 4),
    ],
)
def test_snapshot_accepts_canonical_one_and_four_query_batches(
    example_path: Path,
    expected_query_count: int,
) -> None:
    payload = json.loads(example_path.read_text(encoding="utf-8"))

    encoded = serialize_naver_snapshot(payload)

    assert len(json.loads(encoded)["queries"]) == expected_query_count


@pytest.mark.parametrize("query_count", [0, 5])
def test_snapshot_rejects_query_count_outside_one_to_four(query_count: int) -> None:
    payload = _valid_snapshot()
    if query_count == 0:
        payload["queries"] = []
        payload["coverage"] = "empty"
    else:
        fifth = deepcopy(payload["queries"][-1])
        fifth.update({"rank": 5, "symbol": "000005", "query": "Synthetic Company Five"})
        payload["queries"].append(fifth)
    payload["nextBatchCursor"] = query_count

    with pytest.raises(NaverSnapshotStorageError, match="snapshot"):
        serialize_naver_snapshot(payload)


@pytest.mark.parametrize("unsafe_key", ["rawPayload", "providerHeaders", "clientSecret"])
def test_snapshot_rejects_raw_or_credential_fields(unsafe_key: str) -> None:
    payload = _valid_snapshot()
    payload[unsafe_key] = "validation-dummy-secret"

    with pytest.raises(NaverSnapshotStorageError, match="snapshot"):
        serialize_naver_snapshot(payload)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "<img src=x onerror=alert(1)>synthetic",
        "synthetic\ntext",
        "synthetic\u202etext",
        "A\u030a",
        "synthetic  text",
    ],
)
def test_snapshot_rejects_text_that_bypasses_sanitizer_output_contract(
    unsafe_text: str,
) -> None:
    payload = _valid_snapshot()
    payload["queries"][0]["items"][0]["title"] = unsafe_text

    with pytest.raises(NaverSnapshotStorageError, match="snapshot"):
        serialize_naver_snapshot(payload)


def test_snapshot_preserves_safe_literal_entity_from_one_decode_semantics() -> None:
    payload = _valid_snapshot()
    payload["queries"][0]["items"][0]["title"] = "&lt;img src=x&gt;synthetic"

    encoded = serialize_naver_snapshot(payload)

    assert b"&lt;img src=x&gt;synthetic" in encoded


@pytest.mark.parametrize("field", ["rank", "symbol", "query"])
def test_snapshot_rejects_duplicate_query_identity(field: str) -> None:
    payload = _valid_snapshot()
    payload["queries"][1][field] = payload["queries"][0][field]

    with pytest.raises(NaverSnapshotStorageError, match="snapshot"):
        serialize_naver_snapshot(payload)


@pytest.mark.parametrize("defect", ["batch_cursor", "next_cursor", "rank_order"])
def test_snapshot_rejects_cursor_or_query_order_inconsistency(defect: str) -> None:
    payload = _valid_snapshot()
    if defect == "batch_cursor":
        payload["batchCursor"] = 1
    elif defect == "next_cursor":
        payload["nextBatchCursor"] = 3
    else:
        payload["queries"][1], payload["queries"][2] = (
            payload["queries"][2],
            payload["queries"][1],
        )

    with pytest.raises(NaverSnapshotStorageError, match="snapshot"):
        serialize_naver_snapshot(payload)


def test_snapshot_derives_empty_coverage_from_all_four_query_statuses() -> None:
    payload = _valid_snapshot()
    first = payload["queries"][0]
    first.update(
        {
            "status": "empty",
            "providerTotal": 0,
            "providerDisplay": 0,
            "receivedCount": 0,
            "acceptedCount": 0,
            "filteredCount": 0,
            "redactedUrlCount": 0,
            "items": [],
        }
    )

    with pytest.raises(NaverSnapshotStorageError, match="snapshot"):
        serialize_naver_snapshot(payload)


def test_snapshot_requires_exact_deferred_ranks_and_partial_semantics() -> None:
    payload = _valid_snapshot()
    deferred_query = payload["queries"][3]
    deferred_query["status"] = "deferred"
    payload["partial"] = True
    payload["coverage"] = "partial"

    with pytest.raises(NaverSnapshotStorageError, match="snapshot"):
        serialize_naver_snapshot(payload)

    payload["deferredQueries"] = [deferred_query["rank"]]
    with pytest.raises(NaverSnapshotStorageError, match="snapshot"):
        serialize_naver_snapshot(payload)

    payload["nextBatchCursor"] = 3
    assert serialize_naver_snapshot(payload).endswith(b"\n")


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


def test_one_query_snapshot_and_manifest_publish_with_canonical_contract(tmp_path: Path) -> None:
    payload = _one_query_snapshot()
    snapshot_bytes = serialize_naver_snapshot(payload)

    published = publish_naver_snapshot(
        root=tmp_path / "snapshots",
        snapshot_path=_SNAPSHOT_PATH,
        snapshot=payload,
        manifest_bytes=_manifest_bytes(snapshot_bytes, query_count=1),
    )

    assert published.snapshot_path.read_bytes() == snapshot_bytes
    assert published.manifest_path.exists()


def test_one_query_manifest_rejects_more_than_two_physical_attempts(tmp_path: Path) -> None:
    payload = _one_query_snapshot()
    snapshot_bytes = serialize_naver_snapshot(payload)

    with pytest.raises(NaverSnapshotStorageError, match="manifest"):
        publish_naver_snapshot(
            root=tmp_path / "snapshots",
            snapshot_path=_SNAPSHOT_PATH,
            snapshot=payload,
            manifest_bytes=_manifest_bytes(
                snapshot_bytes,
                query_count=1,
                overrides={"physicalAttemptCount": 3},
            ),
        )


def test_publish_rejects_a_manifest_outside_the_naver_source_contract(tmp_path: Path) -> None:
    payload = _valid_snapshot()
    snapshot_bytes = serialize_naver_snapshot(payload)

    with pytest.raises(NaverSnapshotStorageError, match="manifest"):
        publish_naver_snapshot(
            root=tmp_path / "snapshots",
            snapshot_path=_SNAPSHOT_PATH,
            snapshot=payload,
            manifest_bytes=_manifest_bytes(snapshot_bytes, operation="wrong-operation"),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "recordCount": 0,
            "countBreakdown": {
                "queryCount": 4,
                "acceptedItemCount": 0,
                "filteredItemCount": 0,
                "redactedUrlCount": 0,
            },
        },
        {
            "countBreakdown": {
                "queryCount": 4,
                "acceptedItemCount": 1,
                "filteredItemCount": 1,
                "redactedUrlCount": 0,
            },
        },
        {"partial": True, "coverage": "partial"},
        {"deferredQueries": 1},
    ],
)
def test_publish_rejects_manifest_counts_and_coverage_that_disagree_with_snapshot(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    payload = _valid_snapshot()
    snapshot_bytes = serialize_naver_snapshot(payload)

    with pytest.raises(NaverSnapshotStorageError, match="manifest"):
        publish_naver_snapshot(
            root=tmp_path / "snapshots",
            snapshot_path=_SNAPSHOT_PATH,
            snapshot=payload,
            manifest_bytes=_manifest_bytes(snapshot_bytes, overrides=overrides),
        )


def test_publish_rejects_manifest_query_count_that_disagrees_with_snapshot(
    tmp_path: Path,
) -> None:
    payload = _valid_snapshot()
    snapshot_bytes = serialize_naver_snapshot(payload)

    with pytest.raises(NaverSnapshotStorageError, match="manifest"):
        publish_naver_snapshot(
            root=tmp_path / "snapshots",
            snapshot_path=_SNAPSHOT_PATH,
            snapshot=payload,
            manifest_bytes=_manifest_bytes(snapshot_bytes, query_count=1),
        )


def test_publish_rejects_boolean_manifest_query_count(tmp_path: Path) -> None:
    payload = _one_query_snapshot()
    snapshot_bytes = serialize_naver_snapshot(payload)

    with pytest.raises(NaverSnapshotStorageError, match="manifest"):
        publish_naver_snapshot(
            root=tmp_path / "snapshots",
            snapshot_path=_SNAPSHOT_PATH,
            snapshot=payload,
            manifest_bytes=_manifest_bytes(snapshot_bytes, query_count=True),
        )


def test_publish_rejects_manifest_profile_that_disagrees_with_snapshot(tmp_path: Path) -> None:
    payload = _valid_snapshot()
    snapshot_bytes = serialize_naver_snapshot(payload)
    hub_manifest = _manifest_bytes(
        snapshot_bytes,
        overrides={
            "providerProfile": "naver-api-hub",
            "quotaPolicyVersion": "s1.3-naver-api-hub-quota-v1",
            "provenance": {
                "documentationUrl": "https://api.ncloud-docs.com/docs/naver-api-hub-search-news",
                "policyUrl": "https://www.ncloud.com/policy/terms/svc",
            },
        },
    )

    with pytest.raises(NaverSnapshotStorageError, match="manifest"):
        publish_naver_snapshot(
            root=tmp_path / "snapshots",
            snapshot_path=_SNAPSHOT_PATH,
            snapshot=payload,
            manifest_bytes=hub_manifest,
        )


def test_publish_rejects_manifest_as_of_that_disagrees_with_snapshot(tmp_path: Path) -> None:
    payload = _valid_snapshot()
    snapshot_bytes = serialize_naver_snapshot(payload)
    alternate_path = "naver/2026/07/15/00000000-0000-4000-8000-000000000001/snapshot.json"

    with pytest.raises(NaverSnapshotStorageError, match="manifest"):
        publish_naver_snapshot(
            root=tmp_path / "snapshots",
            snapshot_path=alternate_path,
            snapshot=payload,
            manifest_bytes=_manifest_bytes(
                snapshot_bytes,
                snapshot_path=alternate_path,
                overrides={"asOf": "2026-07-15"},
            ),
        )
