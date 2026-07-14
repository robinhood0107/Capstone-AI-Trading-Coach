from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.data._shared.redis_quota import QuotaUnavailableError
from app.data._shared.source_snapshot_models import SourceSnapshotManifest
from app.data.naver import collect_cli
from app.data.naver._credential_transport import NaverCredentialError
from app.data.naver.collect_cli import CollectCliError, _persist, build_collect_command, main
from app.data.naver.collector import (
    NaverCollectionIncompleteError,
    NaverCollectionResult,
    NaverQueryResult,
)
from app.data.naver.errors import NaverParseError, NaverResponseError
from app.data.naver.storage import NaverSnapshotStorageError


_NOW = datetime(2026, 7, 14, 1, 0, tzinfo=UTC)
_PYTHON_SERVICE_ROOT = Path(__file__).resolve().parents[3]


def _write_manifest(path: Path, *, symbol_count: int = 4) -> Path:
    payload = {
        "schemaVersion": 1,
        "generatedAt": "2026-07-14T00:00:00+00:00",
        "asOfDate": "2026-07-14",
        "source": "synthetic-krx-export.csv",
        "sourceSha256": "a" * 64,
        "rankingRule": "market cap desc, trading value desc, symbol asc",
        "limit": symbol_count,
        "symbols": [
            {
                "rank": rank,
                "symbol": f"{rank:06d}",
                "name": f"{rank}번 합성회사",
                "market": "KOSPI",
                "marketCap": 10_000 - rank,
                "tradingValue": 5_000 - rank,
            }
            for rank in range(1, symbol_count + 1)
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


def test_command_resolves_batch_retry_and_strict_mode_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _write_manifest(tmp_path / "universe_manifest.json")
    monkeypatch.setenv("NAVER_BATCH_SIZE", "1")
    monkeypatch.setenv("NAVER_MAX_ATTEMPTS_PER_QUERY", "1")

    command = build_collect_command(
        [
            "--profile",
            "legacy",
            "--universe-manifest",
            str(manifest),
            "--online",
            "--require-complete",
        ],
        now=_NOW,
    )

    assert command.batch_size == 1
    assert command.max_attempts_per_query == 1
    assert command.require_complete is True


def test_offline_gate_rejects_universe_smaller_than_resolved_batch_before_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = _write_manifest(tmp_path / "universe_manifest.json", symbol_count=1)

    def forbidden_client(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid offline input must not construct the provider client")

    monkeypatch.setattr(collect_cli, "NaverHttpClient", forbidden_client)

    exit_code = main(["--profile", "legacy", "--universe-manifest", str(manifest)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "source=naver operation=news_metadata_collect code=invalid_arguments\n"


def test_offline_gate_rejects_duplicate_audited_query_names(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "universe_manifest.json")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["symbols"][1]["name"] = payload["symbols"][0]["name"]
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(CollectCliError, match="universe manifest"):
        build_collect_command(
            ["--profile", "legacy", "--universe-manifest", str(manifest)],
            now=_NOW,
        )


def test_oversized_universe_manifest_is_rejected_before_json_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "oversized-universe-manifest.json"
    manifest.write_bytes(b'{"padding":"' + (b"a" * 300_000) + b'"}')

    def forbidden_decode(*args: object, **kwargs: object) -> object:
        raise AssertionError("oversized manifest must be bounded before JSON decode")

    monkeypatch.setattr(
        collect_cli,
        "parse_bounded_json_bytes",
        forbidden_decode,
        raising=False,
    )

    with pytest.raises(CollectCliError, match="universe manifest"):
        build_collect_command(
            ["--profile", "legacy", "--universe-manifest", str(manifest)],
            now=_NOW,
        )


def test_deep_universe_manifest_parse_failure_is_sanitized(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(tmp_path / "universe_manifest.json")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["syntheticUnexpected"] = {"a": {"b": {"c": {"secret": "hidden"}}}}
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(CollectCliError, match="universe manifest") as captured:
        build_collect_command(
            ["--profile", "legacy", "--universe-manifest", str(manifest)],
            now=_NOW,
        )

    assert "secret" not in str(captured.value)


def test_require_complete_is_rejected_without_online_before_execution(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path / "universe_manifest.json")

    with pytest.raises(CollectCliError, match="complete|online"):
        build_collect_command(
            [
                "--profile",
                "legacy",
                "--universe-manifest",
                str(manifest),
                "--require-complete",
            ],
            now=_NOW,
        )


def test_offline_gate_validation_is_the_success_exit_zero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = _write_manifest(tmp_path / "universe_manifest.json")

    exit_code = main(["--profile", "legacy", "--universe-manifest", str(manifest)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out) == {
        "online": False,
        "persisted": False,
        "profile": "naver-legacy",
        "source": "naver",
    }
    assert captured.err == ""


def test_unexpected_command_construction_failure_uses_sanitized_one_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_before_command(*args: object, **kwargs: object) -> object:
        raise RuntimeError("synthetic provider URL secret traceback")

    monkeypatch.setattr(collect_cli, "build_collect_command", fail_before_command)

    exit_code = main([])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == "source=naver operation=news_metadata_collect code=collection_failed\n"
    assert "secret" not in captured.err


def test_partial_result_uses_resume_exit_three_and_exact_allowlisted_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = _write_manifest(tmp_path / "universe_manifest.json")
    query = NaverQueryResult(
        rank=1,
        symbol="000001",
        query="1번 합성회사",
        status="failed",
        provider_total=0,
        requested_display=10,
        provider_display=0,
        received_count=0,
        accepted_count=0,
        filtered_count=0,
        redacted_url_count=0,
        items=(),
    )
    result = NaverCollectionResult(
        queries=(query,),
        next_batch_cursor=1,
        deferred_queries=[],
        partial=True,
        coverage="partial",
        failure_codes=("transport_unavailable",),
    )
    monkeypatch.setattr(collect_cli, "_execute_online", lambda command: result)

    exit_code = main(
        ["--profile", "legacy", "--universe-manifest", str(manifest), "--online"]
    )

    captured = capsys.readouterr()
    assert exit_code == 3
    assert captured.out == ""
    assert captured.err == (
        "source=naver operation=news_metadata_collect code=transport_unavailable\n"
    )


def test_failure_code_allowlist_renders_only_the_stable_single_line() -> None:
    expected = frozenset(
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

    assert collect_cli.NAVER_ERROR_CODES == expected
    assert {
        collect_cli._failure_line(code) for code in expected
    } == {
        f"source=naver operation=news_metadata_collect code={code}" for code in expected
    }
    assert collect_cli._failure_line("unexpected-provider-secret") == (
        "source=naver operation=news_metadata_collect code=collection_failed"
    )


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_exit"),
    [
        (CollectCliError("synthetic secret URL"), "invalid_arguments", 2),
        (
            NaverCredentialError("authentication_unavailable"),
            "authentication_unavailable",
            1,
        ),
        (
            NaverCredentialError("logical_deadline_exceeded"),
            "logical_deadline_exceeded",
            1,
        ),
        (
            NaverCredentialError("transport_unavailable", retryable=True),
            "transport_unavailable",
            1,
        ),
        (NaverCredentialError("response_too_large"), "invalid_response", 1),
        (NaverCredentialError("response_unavailable"), "invalid_response", 1),
        (
            NaverResponseError("authentication_failed", retryable=False),
            "authentication_failed",
            1,
        ),
        (NaverResponseError("rate_limited", retryable=False), "rate_limited", 1),
        (
            NaverResponseError("provider_unavailable", retryable=True),
            "transport_unavailable",
            1,
        ),
        (NaverParseError(), "invalid_response", 1),
        (QuotaUnavailableError("synthetic quota secret"), "quota_unavailable", 1),
        (NaverSnapshotStorageError("synthetic path and secret"), "persistence_failed", 1),
        (NaverCollectionIncompleteError("partial_collection"), "partial_collection", 1),
        (RuntimeError("provider URL secret traceback"), "collection_failed", 1),
    ],
)
def test_failure_mapping_never_echoes_exception_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    expected_code: str,
    expected_exit: int,
) -> None:
    manifest = _write_manifest(tmp_path / "universe_manifest.json")
    monkeypatch.setattr(
        collect_cli,
        "_execute_online",
        lambda command: (_ for _ in ()).throw(error),
    )

    exit_code = main(
        ["--profile", "legacy", "--universe-manifest", str(manifest), "--online"]
    )

    captured = capsys.readouterr()
    assert exit_code == expected_exit
    assert captured.out == ""
    assert captured.err == (
        f"source=naver operation=news_metadata_collect code={expected_code}\n"
    )
    assert "secret" not in captured.err
    assert "URL" not in captured.err
    assert "traceback" not in captured.err


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


def test_persist_accepts_resolved_batch_size_one_and_records_one_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    universe_manifest = _write_manifest(tmp_path / "universe_manifest.json")
    data_root = tmp_path / "source_snapshots"
    monkeypatch.setenv("NAVER_BATCH_SIZE", "1")
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
    query = NaverQueryResult(
        rank=1,
        symbol="000001",
        query="1번 합성회사",
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
    result = NaverCollectionResult(
        queries=(query,),
        next_batch_cursor=1,
        deferred_queries=[],
        partial=False,
        coverage="empty",
    )

    _persist(command, result, physical_attempt_count=1)

    manifest_path = next(data_root.rglob("manifest.json"))
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["countBreakdown"]["queryCount"] == 1


@pytest.mark.parametrize(
    "defect",
    ["query_count", "next_cursor", "rank", "symbol", "query"],
)
def test_persist_rejects_result_that_does_not_match_audited_command(
    tmp_path: Path,
    defect: str,
) -> None:
    universe_manifest = _write_manifest(
        tmp_path / "universe_manifest.json",
        symbol_count=6,
    )
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
    next_cursor = 4
    if defect == "query_count":
        queries = queries[:1]
        next_cursor = 1
    elif defect == "next_cursor":
        next_cursor = 0
    elif defect == "rank":
        queries = (replace(queries[0], rank=6), *queries[1:])
    elif defect == "symbol":
        queries = (replace(queries[0], symbol="999999"), *queries[1:])
    else:
        queries = (replace(queries[0], query="감사되지 않은 합성회사"), *queries[1:])
    result = NaverCollectionResult(
        queries=queries,
        next_batch_cursor=next_cursor,
        deferred_queries=[],
        partial=False,
        coverage="empty",
    )

    with pytest.raises(NaverSnapshotStorageError, match="command|result"):
        _persist(command, result, physical_attempt_count=len(queries))

    assert list(data_root.rglob("snapshot.json")) == []
    assert list(data_root.rglob("manifest.json")) == []
