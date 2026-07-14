from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.data._shared.source_snapshot_models import SourceSnapshotManifest
from app.data.naver.collect_cli import CollectCliError, _persist, build_collect_command
from app.data.naver.collector import NaverCollectionResult, NaverQueryResult


_NOW = datetime(2026, 7, 14, 1, 0, tzinfo=UTC)
_PYTHON_SERVICE_ROOT = Path(__file__).resolve().parents[3]


def _write_manifest(path: Path) -> Path:
    payload = {
        "schemaVersion": 1,
        "generatedAt": "2026-07-14T00:00:00+00:00",
        "asOfDate": "2026-07-14",
        "source": "synthetic-krx-export.csv",
        "sourceSha256": "a" * 64,
        "rankingRule": "market cap desc, trading value desc, symbol asc",
        "limit": 4,
        "symbols": [
            {
                "rank": rank,
                "symbol": f"{rank:06d}",
                "name": f"{rank}번 합성회사",
                "market": "KOSPI",
                "marketCap": 10_000 - rank,
                "tradingValue": 5_000 - rank,
            }
            for rank in range(1, 5)
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_online_is_explicit_and_persist_requires_the_online_gate(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "universe_manifest.json")
    base_args = ["--profile", "legacy", "--universe-manifest", str(manifest)]

    offline_dry_run = build_collect_command(base_args, now=_NOW)
    online_no_write = build_collect_command([*base_args, "--online"], now=_NOW)

    assert (offline_dry_run.online, offline_dry_run.persist) == (False, False)
    assert (online_no_write.online, online_no_write.persist) == (True, False)
    assert offline_dry_run.profile.provider_profile == "naver-legacy"

    with pytest.raises(CollectCliError, match="persist.*online"):
        build_collect_command([*base_args, "--persist"], now=_NOW)


def test_cli_requires_operator_profile_instead_of_date_based_auto_selection(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(tmp_path / "universe_manifest.json")

    with pytest.raises(CollectCliError, match="profile"):
        build_collect_command(["--universe-manifest", str(manifest)], now=_NOW)


def test_disabled_api_hub_profile_fails_before_execution(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "universe_manifest.json")

    with pytest.raises(CollectCliError, match="profile_disabled"):
        build_collect_command(
            ["--profile", "api-hub", "--universe-manifest", str(manifest), "--online"],
            now=_NOW,
        )


def test_missing_universe_manifest_never_falls_back_to_kis_seed(tmp_path: Path) -> None:
    missing = tmp_path / "missing-universe-manifest.json"

    with pytest.raises(CollectCliError, match="universe manifest"):
        build_collect_command(
            ["--profile", "legacy", "--universe-manifest", str(missing), "--online"],
            now=_NOW,
        )


def test_command_preserves_exact_manifest_names_and_rank_order(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "universe_manifest.json")

    command = build_collect_command(
        ["--profile", "legacy", "--universe-manifest", str(manifest)],
        now=_NOW,
    )

    assert [(item.rank, item.name) for item in command.universe.symbols] == [
        (1, "1번 합성회사"),
        (2, "2번 합성회사"),
        (3, "3번 합성회사"),
        (4, "4번 합성회사"),
    ]
    assert command.universe_manifest_sha256 == hashlib.sha256(manifest.read_bytes()).hexdigest()


def test_cli_uses_absolute_default_snapshot_root_and_preserves_override(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "universe_manifest.json")
    base_args = ["--profile", "legacy", "--universe-manifest", str(manifest)]

    default_command = build_collect_command(base_args, now=_NOW)
    override = tmp_path / "operator-source-snapshots"
    override_command = build_collect_command(
        [*base_args, "--data-root", str(override)],
        now=_NOW,
    )

    assert default_command.data_root == _PYTHON_SERVICE_ROOT / "data" / "source_snapshots"
    assert default_command.data_root.is_absolute()
    assert override_command.data_root == override


def test_persist_writes_contract_valid_manifest_operation_and_commit_marker(
    tmp_path: Path,
) -> None:
    universe_manifest = _write_manifest(tmp_path / "universe_manifest.json")
    data_root = tmp_path / "source_snapshots"
    command = build_collect_command(
        [
            "--profile",
            "legacy",
            "--universe-manifest",
            str(universe_manifest),
            "--online",
            "--persist",
            "--data-root",
            str(data_root),
        ],
        now=_NOW,
    )
    queries = tuple(
        NaverQueryResult(
            rank=rank,
            symbol=f"{rank:06d}",
            query=f"{rank}번 합성회사",
            status="empty",
            provider_total=0,
            requested_display=10,
            provider_display=0,
            received_count=0,
            accepted_count=0,
            filtered_count=0,
            redacted_url_count=0,
            items=(),
        )
        for rank in range(1, 5)
    )
    result = NaverCollectionResult(
        queries=queries,
        next_batch_cursor=0,
        deferred_queries=[],
        partial=False,
        coverage="empty",
    )

    _persist(command, result, physical_attempt_count=4)

    manifest_paths = list(data_root.rglob("manifest.json"))
    snapshot_paths = list(data_root.rglob("snapshot.json"))
    assert len(manifest_paths) == len(snapshot_paths) == 1
    manifest_payload = json.loads(manifest_paths[0].read_text(encoding="utf-8"))
    validated = SourceSnapshotManifest.model_validate(manifest_payload)
    assert validated.operation == "naver-news-metadata-collect"
    assert validated.snapshot_sha256 == hashlib.sha256(snapshot_paths[0].read_bytes()).hexdigest()
