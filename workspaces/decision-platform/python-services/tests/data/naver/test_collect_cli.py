from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.data.naver.collect_cli import CollectCliError, build_collect_command


_NOW = datetime(2026, 7, 14, 1, 0, tzinfo=UTC)


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
