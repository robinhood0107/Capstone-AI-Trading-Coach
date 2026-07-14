from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, Sequence, cast
from uuid import uuid4

from app.data._shared.canonical_json import canonical_json_bytes
from app.data._shared.source_snapshot_models import SourceSnapshotManifest
from app.data.kis.universe import UniverseManifest
from app.data.naver.collector import NaverCollectionResult, collect_news_batch
from app.data.naver.http_client import NaverHttpClient
from app.data.naver.profiles import NaverProfile, profile_for
from app.data.naver.policy import request_policy_for
from app.data.naver.quota import quota_policy_for
from app.data.naver.settings import NaverSettings
from app.data.naver.storage import publish_naver_snapshot, serialize_naver_snapshot


class CollectCliError(RuntimeError):
    """CLI gate·manifest 오류를 credential, query, filesystem 원문 없이 보고한다."""


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
    batch_cursor: int
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
    parser.add_argument("--batch-cursor", type=int, default=0)
    parser.add_argument("--display", type=int, default=10)
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args(list(argv))

    if not isinstance(args.profile, str) or not args.profile:
        raise CollectCliError("operator profile is required")
    if args.persist and not args.online:
        raise CollectCliError("persist requires online gate")
    checked_at = now or datetime.now(UTC)
    try:
        profile = profile_for(args.profile, now=checked_at)
    except ValueError as error:
        raise CollectCliError(str(error)) from None

    if not isinstance(args.universe_manifest, str) or not args.universe_manifest:
        raise CollectCliError("universe manifest is required")
    manifest_path = Path(args.universe_manifest)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise CollectCliError("universe manifest is unavailable")
    try:
        manifest_bytes = manifest_path.read_bytes()
        payload = json.loads(manifest_bytes)
        if not isinstance(payload, dict):
            raise ValueError
        universe = UniverseManifest.from_json(cast(dict[str, Any], payload))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise CollectCliError("universe manifest is invalid") from None
    if (
        isinstance(args.batch_cursor, bool)
        or not 0 <= args.batch_cursor < len(universe.symbols)
        or isinstance(args.display, bool)
        or not 1 <= args.display <= 20
    ):
        raise CollectCliError("universe batch arguments are invalid")
    if args.data_root is None:
        try:
            data_root = NaverSettings().snapshot_root
        except (OSError, ValueError):
            raise CollectCliError("collector settings are invalid") from None
    else:
        data_root = args.data_root

    return NaverCollectCommand(
        profile=profile,
        universe=universe,
        universe_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        online=bool(args.online),
        persist=bool(args.persist),
        batch_cursor=args.batch_cursor,
        requested_display=args.display,
        data_root=data_root,
        now=checked_at.astimezone(UTC),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """기본 offline dry-run으로 gate를 검증하고 `--online --persist`에서만 artifact를 쓴다."""
    try:
        command = build_collect_command(tuple(argv) if argv is not None else tuple(sys.argv[1:]))
        if not command.online:
            _print_summary(command, result=None, published=False)
            return 0
        result = _execute_online(command)
        _print_summary(command, result=result, published=command.persist)
        return 0
    except CollectCliError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception:
        print("naver collection failed", file=sys.stderr)
        return 1


def _execute_online(command: NaverCollectCommand) -> NaverCollectionResult:
    settings = NaverSettings(
        naver_search_profile=command.profile.name,
        naver_display=command.requested_display,
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
        )
        if command.persist:
            _persist(command, result, physical_attempt_count=client.physical_attempt_count)
        return result
    finally:
        client.close()


def _persist(
    command: NaverCollectCommand,
    result: NaverCollectionResult,
    *,
    physical_attempt_count: int,
) -> None:
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
