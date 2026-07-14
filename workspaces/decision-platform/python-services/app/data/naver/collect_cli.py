from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, Sequence, cast
from uuid import uuid4

from pydantic import ValidationError

from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_bytes,
)
from app.data._shared.canonical_json import canonical_json_bytes
from app.data._shared.redis_quota import QuotaUnavailableError
from app.data._shared.source_snapshot_models import SourceSnapshotManifest
from app.data.kis.universe import UniverseManifest
from app.data.naver._credential_transport import NaverCredentialError
from app.data.naver.collector import (
    NaverCollectionError,
    NaverCollectionIncompleteError,
    NaverCollectionResult,
    collect_news_batch,
    select_audited_news_batch,
)
from app.data.naver.errors import NaverError, NaverParseError, NaverResponseError
from app.data.naver.http_client import NaverHttpClient
from app.data.naver.profiles import NaverProfile, profile_for
from app.data.naver.policy import request_policy_for
from app.data.naver.quota import quota_policy_for
from app.data.naver.settings import NaverSettings
from app.data.naver.storage import (
    NaverSnapshotStorageError,
    publish_naver_snapshot,
    serialize_naver_snapshot,
)


NAVER_ERROR_CODES = frozenset(
    {
        "invalid_arguments",
        "authentication_unavailable",
        "authentication_failed",
        "logical_deadline_exceeded",
        "transport_unavailable",
        "rate_limited",
        "quota_unavailable",
        "invalid_response",
        "partial_collection",
        "persistence_failed",
        "collection_failed",
    }
)
_UNIVERSE_MANIFEST_MAX_BYTES = 256 * 1024
_UNIVERSE_MANIFEST_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_UNIVERSE_MANIFEST_JSON_LIMITS = BoundedJsonLimits(
    max_bytes=_UNIVERSE_MANIFEST_MAX_BYTES,
    max_depth=3,
    max_list_items=100,
    max_object_keys=8,
    max_text_codepoints=4_096,
    max_text_bytes=16_384,
    max_number_characters=32,
)


class CollectCliError(RuntimeError):
    """CLI gate·manifest 오류를 credential, query, filesystem 원문 없이 보고한다."""


class _PersistenceError(RuntimeError):
    """게시 실패의 원인·절대경로를 CLI 출력에서 제거하는 내부 경계다."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise CollectCliError("collect arguments are invalid") from None


@dataclass(frozen=True)
class NaverCollectCommand:
    """부작용 전에 확정된 operator profile·manifest·online/persist gate다."""

    profile: NaverProfile
    universe: UniverseManifest
    universe_manifest_sha256: str
    online: bool
    persist: bool
    require_complete: bool
    batch_cursor: int
    batch_size: int
    max_attempts_per_query: int
    requested_display: int
    data_root: Path
    now: datetime


def build_collect_command(
    argv: Sequence[str],
    *,
    now: datetime | None = None,
) -> NaverCollectCommand:
    """명시 profile과 감사 manifest를 검증하고 network/storage gate를 순수하게 구성한다."""
    parser = _Parser(prog="naver-news-metadata-collect", add_help=False)
    parser.add_argument("--profile")
    parser.add_argument("--universe-manifest")
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--batch-cursor", type=int, default=0)
    parser.add_argument("--display", type=int, default=10)
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args(list(argv))

    if not isinstance(args.profile, str) or not args.profile:
        raise CollectCliError("operator profile is required")
    if args.persist and not args.online:
        raise CollectCliError("persist requires online gate")
    if args.require_complete and not args.online:
        raise CollectCliError("require complete requires online gate")
    checked_at = now or datetime.now(UTC)
    try:
        profile = profile_for(args.profile, now=checked_at)
    except ValueError as error:
        raise CollectCliError(str(error)) from None

    if not isinstance(args.universe_manifest, str) or not args.universe_manifest:
        raise CollectCliError("universe manifest is required")
    manifest_path = Path(args.universe_manifest)
    try:
        manifest_bytes = _read_universe_manifest(manifest_path)
        payload = parse_bounded_json_bytes(
            manifest_bytes,
            limits=_UNIVERSE_MANIFEST_JSON_LIMITS,
        )
        if not isinstance(payload, dict):
            raise ValueError
        universe = UniverseManifest.from_json(cast(dict[str, Any], payload))
    except (
        OSError,
        BoundedJsonError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        raise CollectCliError("universe manifest is invalid") from None
    if isinstance(args.display, bool) or not 1 <= args.display <= 20:
        raise CollectCliError("universe batch arguments are invalid")
    try:
        if args.data_root is None:
            settings = NaverSettings(
                naver_search_profile=profile.name,
                naver_display=args.display,
            )
        else:
            settings = NaverSettings(
                naver_search_profile=profile.name,
                naver_display=args.display,
                snapshot_root=args.data_root,
            )
    except (OSError, ValidationError, ValueError):
        raise CollectCliError("collector settings are invalid") from None
    try:
        select_audited_news_batch(
            universe,
            batch_size=settings.batch_size,
            batch_cursor=args.batch_cursor,
        )
    except NaverCollectionError:
        raise CollectCliError("universe manifest or batch is invalid") from None

    return NaverCollectCommand(
        profile=profile,
        universe=universe,
        universe_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        online=bool(args.online),
        persist=bool(args.persist),
        require_complete=bool(args.require_complete),
        batch_cursor=args.batch_cursor,
        batch_size=settings.batch_size,
        max_attempts_per_query=settings.max_attempts_per_query,
        requested_display=args.display,
        data_root=settings.snapshot_root,
        now=checked_at.astimezone(UTC),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """기본 offline dry-run으로 gate를 검증하고 `--online --persist`에서만 artifact를 쓴다."""
    try:
        command = build_collect_command(tuple(argv) if argv is not None else tuple(sys.argv[1:]))
    except CollectCliError:
        print(_failure_line("invalid_arguments"), file=sys.stderr)
        return 2
    except Exception:
        print(_failure_line("collection_failed"), file=sys.stderr)
        return 1

    if not command.online:
        _print_summary(command, result=None, published=False)
        return 0
    try:
        result = _execute_online(command)
    except Exception as error:
        code = _failure_code(error)
        print(_failure_line(code), file=sys.stderr)
        return 2 if code == "invalid_arguments" else 1
    if result.partial:
        print(_failure_line(_partial_failure_code(result)), file=sys.stderr)
        return 3
    _print_summary(command, result=result, published=command.persist)
    return 0


def _execute_online(command: NaverCollectCommand) -> NaverCollectionResult:
    settings = NaverSettings(
        naver_search_profile=command.profile.name,
        naver_display=command.requested_display,
        naver_batch_size=command.batch_size,
        naver_max_attempts_per_query=command.max_attempts_per_query,
        snapshot_root=command.data_root,
    )
    client = NaverHttpClient(settings=settings, profile=command.profile)
    try:
        result = collect_news_batch(
            universe=command.universe,
            client=client,
            batch_cursor=command.batch_cursor,
            retrieved_at=command.now,
            requested_display=command.requested_display,
            batch_size=command.batch_size,
            require_complete=command.require_complete,
        )
        if command.persist:
            try:
                _persist(command, result, physical_attempt_count=client.physical_attempt_count)
            except Exception:
                raise _PersistenceError("persistence_failed") from None
        return result
    finally:
        client.close()


def _persist(
    command: NaverCollectCommand,
    result: NaverCollectionResult,
    *,
    physical_attempt_count: int,
) -> None:
    _validate_result_against_command(command, result)
    run_id = str(uuid4())
    as_of = command.now.date()
    timestamp = command.now.strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshot_path = f"naver/{as_of:%Y/%m/%d}/{run_id}/snapshot.json"
    snapshot: dict[str, object] = {
        "schemaVersion": 1,
        "source": "naver",
        "providerProfile": command.profile.provider_profile,
        "asOf": as_of.isoformat(),
        "retrievedAt": timestamp,
        "universeManifestSha256": command.universe_manifest_sha256,
        "universeAsOfDate": command.universe.as_of_date.isoformat(),
        "batchCursor": command.batch_cursor,
        "nextBatchCursor": result.next_batch_cursor,
        "queries": [query.to_json() for query in result.queries],
        "partial": result.partial,
        "coverage": result.coverage,
        "deferredQueries": result.deferred_queries,
    }
    snapshot_bytes = serialize_naver_snapshot(snapshot)
    accepted = sum(query.accepted_count for query in result.queries)
    request_policy = request_policy_for(command.profile.provider_profile)
    manifest = {
        "schemaVersion": 1,
        "source": "naver",
        "providerProfile": command.profile.provider_profile,
        "operation": "naver-news-metadata-collect",
        "generatedAt": timestamp,
        "asOf": as_of.isoformat(),
        "snapshotPath": snapshot_path,
        "snapshotSha256": hashlib.sha256(snapshot_bytes).hexdigest(),
        "recordCount": accepted,
        "countBreakdown": {
            "queryCount": len(result.queries),
            "acceptedItemCount": accepted,
            "filteredItemCount": sum(query.filtered_count for query in result.queries),
            "redactedUrlCount": sum(query.redacted_url_count for query in result.queries),
        },
        "partial": result.partial,
        "coverage": result.coverage,
        "deferredQueries": len(result.deferred_queries),
        "physicalAttemptCount": physical_attempt_count,
        "quotaPolicyVersion": quota_policy_for(command.profile.provider_profile).version,
        "provenance": {
            "documentationUrl": request_policy.documentation_url,
            "policyUrl": request_policy.policy_url,
        },
        "sanitizationVersion": request_policy.sanitization_version,
        "retentionDays": 30,
        "deleteOwner": "decision-platform:source-snapshot-retention",
    }
    validated_manifest = SourceSnapshotManifest.model_validate(manifest)
    publish_naver_snapshot(
        root=command.data_root,
        snapshot_path=snapshot_path,
        snapshot=snapshot,
        manifest_bytes=canonical_json_bytes(
            validated_manifest.model_dump(by_alias=True, mode="json")
        ),
    )


def _validate_result_against_command(
    command: NaverCollectCommand,
    result: NaverCollectionResult,
) -> None:
    """게시 전 result가 승인된 universe 선택·batch·cursor와 정확히 같은지 재검증한다."""
    try:
        selected, expected_next_cursor = select_audited_news_batch(
            command.universe,
            batch_size=command.batch_size,
            batch_cursor=command.batch_cursor,
        )
    except NaverCollectionError:
        raise NaverSnapshotStorageError(
            "Naver publish command/result contract is invalid"
        ) from None
    if len(result.queries) != command.batch_size:
        raise NaverSnapshotStorageError("Naver publish command/result contract is invalid")
    expected_identities = tuple((item.rank, item.symbol, item.name) for item in selected)
    actual_identities = tuple((query.rank, query.symbol, query.query) for query in result.queries)
    if actual_identities != expected_identities:
        raise NaverSnapshotStorageError("Naver publish command/result contract is invalid")

    deferred_offsets = tuple(
        offset for offset, query in enumerate(result.queries) if query.status == "deferred"
    )
    if deferred_offsets:
        first_deferred = deferred_offsets[0]
        if deferred_offsets != tuple(range(first_deferred, command.batch_size)):
            raise NaverSnapshotStorageError("Naver publish command/result contract is invalid")
        expected_next_cursor = (command.batch_cursor + first_deferred) % len(
            command.universe.symbols
        )
    expected_deferred_queries = [selected[offset].rank for offset in deferred_offsets]
    if (
        result.deferred_queries != expected_deferred_queries
        or result.next_batch_cursor != expected_next_cursor
    ):
        raise NaverSnapshotStorageError("Naver publish command/result contract is invalid")


def _read_universe_manifest(path: Path) -> bytes:
    """symlink를 따르지 않고 256 KiB 이내 regular universe manifest만 읽는다."""
    file_fd = os.open(path, _UNIVERSE_MANIFEST_FLAGS)
    try:
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _UNIVERSE_MANIFEST_MAX_BYTES:
            raise ValueError("universe manifest is invalid")
        chunks: list[bytes] = []
        remaining = _UNIVERSE_MANIFEST_MAX_BYTES + 1
        while remaining > 0:
            chunk = os.read(file_fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > _UNIVERSE_MANIFEST_MAX_BYTES:
            raise ValueError("universe manifest is invalid")
        return content
    finally:
        os.close(file_fd)


def _print_summary(
    command: NaverCollectCommand,
    *,
    result: NaverCollectionResult | None,
    published: bool,
) -> None:
    summary: dict[str, object] = {
        "source": "naver",
        "profile": command.profile.provider_profile,
        "online": command.online,
        "persisted": published,
    }
    if result is not None:
        summary.update(
            {
                "coverage": result.coverage,
                "queryCount": len(result.queries),
                "deferredQueries": len(result.deferred_queries),
            }
        )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def _failure_line(code: str) -> str:
    """오류 원문을 받지 않고 allowlisted code 하나만 stable line으로 직렬화한다."""
    safe_code = code if code in NAVER_ERROR_CODES else "collection_failed"
    return f"source=naver operation=news_metadata_collect code={safe_code}"


def _partial_failure_code(result: NaverCollectionResult) -> str:
    codes = {code for code in result.failure_codes if code in NAVER_ERROR_CODES}
    if len(codes) == 1:
        return codes.pop()
    return "partial_collection"


def _failure_code(error: Exception) -> str:
    if isinstance(error, (_PersistenceError, NaverSnapshotStorageError)):
        return "persistence_failed"
    if isinstance(error, NaverCollectionIncompleteError):
        return error.code if error.code in NAVER_ERROR_CODES else "collection_failed"
    if isinstance(error, CollectCliError):
        return "invalid_arguments"
    if isinstance(error, QuotaUnavailableError):
        return "quota_unavailable"
    if isinstance(error, NaverCredentialError):
        if error.code in {
            "authentication_unavailable",
            "authentication_failed",
            "logical_deadline_exceeded",
            "transport_unavailable",
        }:
            return error.code
        if error.code == "profile_invalid":
            return "invalid_arguments"
        if error.code in {"response_too_large", "response_unavailable"}:
            return "invalid_response"
        return "collection_failed"
    if isinstance(error, NaverResponseError):
        if error.code in {"authentication_failed", "rate_limited", "invalid_response"}:
            return error.code
        if error.code == "provider_unavailable":
            return "transport_unavailable"
        return "invalid_response"
    if isinstance(error, NaverParseError):
        return "invalid_response"
    if isinstance(error, NaverCollectionError):
        return "invalid_arguments"
    if isinstance(error, NaverError):
        return "collection_failed"
    if isinstance(error, (ValidationError, ValueError)):
        return "invalid_arguments"
    return "collection_failed"
