from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final, cast
from urllib.parse import urlsplit

from app.data._shared.canonical_json import canonical_json_bytes
from app.data._shared.secure_snapshot_storage import (
    PublishedSourceSnapshot,
    SecureSnapshotStorageError,
    publish_source_snapshot,
)
from app.data._shared.source_snapshot_models import (
    NaverCountBreakdown,
    SourceSnapshotManifest,
)
from app.data.naver.policy import request_policy_for, validate_news_query
from app.data.naver.quota import quota_policy_for
from app.data.naver.url_metadata import normalize_metadata_url

NAVER_SNAPSHOT_MAX_BYTES: Final = 4 * 1024 * 1024

_TOP_LEVEL_KEYS: Final = frozenset(
    {
        "schemaVersion",
        "source",
        "providerProfile",
        "asOf",
        "retrievedAt",
        "universeManifestSha256",
        "universeAsOfDate",
        "batchCursor",
        "nextBatchCursor",
        "queries",
        "partial",
        "coverage",
        "deferredQueries",
    }
)
_QUERY_KEYS: Final = frozenset(
    {
        "rank",
        "symbol",
        "query",
        "status",
        "providerTotal",
        "requestedDisplay",
        "providerDisplay",
        "receivedCount",
        "acceptedCount",
        "filteredCount",
        "redactedUrlCount",
        "items",
    }
)
_ITEM_KEYS: Final = frozenset(
    {"title", "description", "originalUrl", "naverUrl", "providerPubDate"}
)
_FORBIDDEN_KEYS: Final = frozenset(
    {
        "authorization",
        "clientsecret",
        "credential",
        "providerheaders",
        "rawbody",
        "rawpayload",
        "requestheaders",
        "responseheaders",
    }
)
_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_SYMBOL_PATTERN: Final = re.compile(r"[0-9A-Z._:-]{1,20}")
_UTC_TIMESTAMP_PATTERN: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z"
)
_STATUSES: Final = frozenset({"complete", "empty", "failed", "deferred"})
_COVERAGES: Final = frozenset({"complete", "partial", "empty"})
_CANONICAL_WHITESPACE: Final = re.compile(r"\s+")


class NaverSnapshotStorageError(ValueError):
    """Naver snapshot 계약·크기·게시 오류를 원문과 절대경로 없이 보고한다."""


def serialize_naver_snapshot(snapshot: object) -> bytes:
    """sanitized Naver metadata 계약만 canonical JSON bytes로 직렬화한다."""
    try:
        _reject_forbidden_fields(snapshot)
        _validate_snapshot_contract(snapshot)
        encoded = canonical_json_bytes(snapshot)
    except NaverSnapshotStorageError:
        raise
    except (TypeError, ValueError, RecursionError):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid") from None
    if len(encoded) > NAVER_SNAPSHOT_MAX_BYTES:
        raise NaverSnapshotStorageError("Naver snapshot size exceeds 4 MiB")
    return encoded


def publish_naver_snapshot(
    *,
    root: Path,
    snapshot_path: str,
    snapshot: object,
    manifest_bytes: bytes,
) -> PublishedSourceSnapshot:
    """검증한 snapshot을 먼저 쓰고 manifest를 commit marker로 마지막에 게시한다."""
    snapshot_bytes = serialize_naver_snapshot(snapshot)
    _validate_manifest_contract(
        manifest_bytes,
        snapshot_path=snapshot_path,
        snapshot_bytes=snapshot_bytes,
    )
    try:
        return publish_source_snapshot(
            root=root,
            snapshot_path=snapshot_path,
            snapshot_bytes=snapshot_bytes,
            manifest_bytes=manifest_bytes,
        )
    except SecureSnapshotStorageError:
        raise NaverSnapshotStorageError("Naver snapshot publish failed") from None


def _validate_manifest_contract(
    manifest_bytes: bytes,
    *,
    snapshot_path: str,
    snapshot_bytes: bytes,
) -> None:
    try:
        manifest = SourceSnapshotManifest.model_validate_json(manifest_bytes)
        if not isinstance(manifest.count_breakdown, NaverCountBreakdown):
            raise ValueError
        snapshot_value = json.loads(snapshot_bytes)
        if not isinstance(snapshot_value, dict):
            raise ValueError
        snapshot_payload = cast(dict[str, object], snapshot_value)
        policy = request_policy_for(manifest.provider_profile)
        quota = quota_policy_for(manifest.provider_profile)
        canonical = canonical_json_bytes(manifest.model_dump(by_alias=True, mode="json"))
    except (TypeError, ValueError, RecursionError):
        raise NaverSnapshotStorageError("Naver snapshot manifest was invalid") from None

    counts = manifest.count_breakdown
    if (
        canonical != manifest_bytes
        or manifest.source != "naver"
        or manifest.operation != "naver-news-metadata-collect"
        or manifest.snapshot_path != snapshot_path
        or manifest.snapshot_sha256 != hashlib.sha256(snapshot_bytes).hexdigest()
        or manifest.retention_days != 30
        or manifest.record_count > 80
        or manifest.deferred_queries > 4
        or manifest.physical_attempt_count > 8
        or manifest.physical_attempt_count > 2 * counts.query_count
        or not 1 <= counts.query_count <= 4
        or counts.accepted_item_count > 80
        or counts.filtered_item_count > 80
        or counts.redacted_url_count > 160
        or manifest.quota_policy_version != quota.version
        or manifest.sanitization_version != policy.sanitization_version
        or str(manifest.provenance.documentation_url) != policy.documentation_url
        or str(manifest.provenance.policy_url) != policy.policy_url
        or not _manifest_matches_snapshot(manifest, snapshot_payload)
    ):
        raise NaverSnapshotStorageError("Naver snapshot manifest was invalid")


def _manifest_matches_snapshot(
    manifest: SourceSnapshotManifest,
    snapshot: dict[str, object],
) -> bool:
    counts = manifest.count_breakdown
    if not isinstance(counts, NaverCountBreakdown):
        return False
    queries = snapshot.get("queries")
    deferred = snapshot.get("deferredQueries")
    if not isinstance(queries, list) or not isinstance(deferred, list):
        return False

    accepted_count = 0
    filtered_count = 0
    redacted_url_count = 0
    for value in queries:
        if not isinstance(value, dict):
            return False
        query = cast(dict[str, object], value)
        accepted = query.get("acceptedCount")
        filtered = query.get("filteredCount")
        redacted = query.get("redactedUrlCount")
        if any(
            not isinstance(count, int) or isinstance(count, bool)
            for count in (accepted, filtered, redacted)
        ):
            return False
        accepted_count += cast(int, accepted)
        filtered_count += cast(int, filtered)
        redacted_url_count += cast(int, redacted)

    return (
        manifest.provider_profile == snapshot.get("providerProfile")
        and manifest.as_of.isoformat() == snapshot.get("asOf")
        and manifest.record_count == accepted_count
        and counts.query_count == len(queries)
        and counts.accepted_item_count == accepted_count
        and counts.filtered_item_count == filtered_count
        and counts.redacted_url_count == redacted_url_count
        and manifest.partial == snapshot.get("partial")
        and manifest.coverage == snapshot.get("coverage")
        and manifest.deferred_queries == len(deferred)
    )


def _reject_forbidden_fields(value: object) -> None:
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        for key, child in mapping.items():
            if not isinstance(key, str):
                raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
            normalized = "".join(character for character in key.casefold() if character.isalnum())
            if normalized in _FORBIDDEN_KEYS:
                raise NaverSnapshotStorageError("Naver snapshot contains a forbidden field")
            _reject_forbidden_fields(child)
    elif isinstance(value, list):
        for child in cast(list[object], value):
            _reject_forbidden_fields(child)


def _validate_snapshot_contract(snapshot: object) -> None:
    if not isinstance(snapshot, dict):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    payload = cast(dict[str, object], snapshot)
    _require_exact_keys(payload, _TOP_LEVEL_KEYS)

    schema_version = payload["schemaVersion"]
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
        or payload["source"] != "naver"
    ):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    if payload["providerProfile"] not in {"naver-legacy", "naver-api-hub"}:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    _require_date(payload["asOf"])
    _require_utc_timestamp(payload["retrievedAt"])
    _require_date(payload["universeAsOfDate"])
    if not isinstance(payload["universeManifestSha256"], str) or (
        _SHA256_PATTERN.fullmatch(payload["universeManifestSha256"]) is None
    ):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    _require_integer(payload["batchCursor"], minimum=0)
    _require_integer(payload["nextBatchCursor"], minimum=0)

    queries = payload["queries"]
    if not isinstance(queries, list) or not 1 <= len(queries) <= 4:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    query_fingerprints: set[str] = set()
    query_ranks: list[int] = []
    query_symbols: list[str] = []
    query_names: list[str] = []
    query_statuses: list[str] = []
    for query in cast(list[object], queries):
        _validate_query(query)
        query_fingerprints.add(_contract_fingerprint(query))
        query_row = cast(dict[str, object], query)
        query_ranks.append(cast(int, query_row["rank"]))
        query_symbols.append(cast(str, query_row["symbol"]))
        query_names.append(cast(str, query_row["query"]))
        query_statuses.append(cast(str, query_row["status"]))
    if len(query_fingerprints) != len(queries):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    if (
        len(set(query_ranks)) != len(queries)
        or len(set(query_symbols)) != len(queries)
        or len(set(query_names)) != len(queries)
    ):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    batch_cursor = cast(int, payload["batchCursor"])
    next_batch_cursor = cast(int, payload["nextBatchCursor"])
    if query_ranks[0] != batch_cursor + 1 or any(
        current != previous + 1 and current != 1
        for previous, current in zip(query_ranks, query_ranks[1:])
    ):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")

    partial = payload["partial"]
    coverage = payload["coverage"]
    deferred = payload["deferredQueries"]
    if not isinstance(partial, bool) or coverage not in _COVERAGES:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    if not isinstance(deferred, list) or len(deferred) > 4:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    deferred_values = cast(list[object], deferred)
    for rank in deferred_values:
        _require_integer(rank, minimum=1)
    if len(set(cast(list[int], deferred_values))) != len(deferred_values):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    expected_deferred = [
        rank
        for rank, status in zip(query_ranks, query_statuses, strict=True)
        if status == "deferred"
    ]
    if expected_deferred:
        first_deferred = query_statuses.index("deferred")
        if any(status != "deferred" for status in query_statuses[first_deferred:]):
            raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
        expected_next_cursors = {expected_deferred[0] - 1}
    elif any(current == 1 for current in query_ranks[1:]):
        expected_next_cursors = {query_ranks[-1]}
    else:
        # universe 전체 크기는 snapshot에 복제하지 않으므로 끝에 닿은 경우의 0과 단순 증가를 허용한다.
        expected_next_cursors = {0, batch_cursor + len(queries)}
    expected_partial = any(status in {"failed", "deferred"} for status in query_statuses)
    if expected_partial:
        expected_coverage = "partial"
    elif all(status == "empty" for status in query_statuses):
        expected_coverage = "empty"
    else:
        expected_coverage = "complete"
    if (
        deferred_values != expected_deferred
        or next_batch_cursor not in expected_next_cursors
        or partial != expected_partial
        or coverage != expected_coverage
    ):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")


def _validate_query(value: object) -> None:
    if not isinstance(value, dict):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    query = cast(dict[str, object], value)
    _require_exact_keys(query, _QUERY_KEYS)
    _require_integer(query["rank"], minimum=1)
    symbol = query["symbol"]
    if not isinstance(symbol, str) or _SYMBOL_PATTERN.fullmatch(symbol) is None:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    try:
        validate_news_query(cast(str, query["query"]))
    except (TypeError, ValueError):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid") from None
    status = query["status"]
    if status not in _STATUSES:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")

    provider_total = _require_integer(query["providerTotal"], minimum=0, maximum=2_147_483_647)
    requested_display = _require_integer(query["requestedDisplay"], minimum=1, maximum=20)
    provider_display = _require_integer(query["providerDisplay"], minimum=0, maximum=20)
    received_count = _require_integer(query["receivedCount"], minimum=0, maximum=20)
    accepted_count = _require_integer(query["acceptedCount"], minimum=0, maximum=20)
    filtered_count = _require_integer(query["filteredCount"], minimum=0, maximum=20)
    redacted_count = _require_integer(query["redactedUrlCount"], minimum=0, maximum=40)
    del provider_total, requested_display, provider_display

    items = query["items"]
    if not isinstance(items, list) or len(items) > 20:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    item_fingerprints: set[str] = set()
    for item in cast(list[object], items):
        _validate_item(item)
        item_fingerprints.add(_contract_fingerprint(item))
    if len(item_fingerprints) != len(items):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    if (
        accepted_count != len(items)
        or received_count != accepted_count + filtered_count
        or redacted_count > received_count * 2
    ):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    if status in {"empty", "failed", "deferred"} and (accepted_count != 0 or items):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")


def _validate_item(value: object) -> None:
    if not isinstance(value, dict):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    item = cast(dict[str, object], value)
    _require_exact_keys(item, _ITEM_KEYS)
    _require_sanitized_text(item["title"], max_codepoints=512, max_bytes=2_048)
    _require_sanitized_text(item["description"], max_codepoints=2_048, max_bytes=8_192)
    original_url = _require_safe_url(item["originalUrl"])
    naver_url = _require_safe_url(item["naverUrl"])
    if original_url is None and naver_url is None:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    _require_utc_timestamp(item["providerPubDate"])


def _require_exact_keys(mapping: dict[str, object], expected: frozenset[str]) -> None:
    if set(mapping) != expected:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")


def _contract_fingerprint(value: object) -> str:
    # uniqueItems 검증은 최종 encoder와 분리해 게시 byte 생성이 정확히 한 번만 일어나게 한다.
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid") from None


def _require_integer(value: object, *, minimum: int, maximum: int | None = None) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    return value


def _require_text(value: object, *, max_codepoints: int, max_bytes: int) -> str:
    if not isinstance(value, str) or not value:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid") from None
    if len(value) > max_codepoints or len(encoded) > max_bytes:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    return value


def _require_sanitized_text(value: object, *, max_codepoints: int, max_bytes: int) -> str:
    text = _require_text(value, max_codepoints=max_codepoints, max_bytes=max_bytes)
    if (
        "<" in text
        or ">" in text
        or any(unicodedata.category(character) in {"Cc", "Cf"} for character in text)
        or not unicodedata.is_normalized("NFC", text)
        or _CANONICAL_WHITESPACE.sub(" ", text).strip() != text
    ):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    return text


def _require_date(value: object) -> date:
    if not isinstance(value, str):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid") from None
    if parsed.isoformat() != value:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    return parsed


def _require_utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or _UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    return parsed


def _require_safe_url(value: object) -> str | None:
    if value is None:
        return None
    text = _require_text(value, max_codepoints=2_048, max_bytes=8_192)
    if any(character.isspace() or ord(character) < 0x20 for character in text):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    try:
        parsed = urlsplit(text)
        host = parsed.hostname
    except ValueError:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    # parser에서 이미 canonicalized된 metadata만 저장해 local host와 credential query 재유입을 막는다.
    if normalize_metadata_url(text) != text:
        raise NaverSnapshotStorageError("Naver snapshot contract was invalid")
    return text
