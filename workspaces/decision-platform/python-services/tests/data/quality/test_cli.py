from datetime import date
from pathlib import Path
import socket

import pytest

from app.data.quality.cli import main
from tests.data.quality.helpers import prepare_snapshot


REVISION = "7131f695293472ea16ee05322ed9b05f7b69d129"


def _args(identifiers, *, collection: bool = True) -> list[str]:
    values = [
        "generate",
        "--window-start",
        "2026-07-21",
        "--window-end",
        "2026-07-21",
        "--evaluated-at",
        "2026-07-21T07:00:00Z",
        "--universe-manifest",
        identifiers.universe,
        "--dataset-manifest",
        identifiers.dataset,
        "--software-revision",
        REVISION,
    ]
    if collection:
        values.extend(("--collection-run", identifiers.collection))
    return values


def test_cli_success_and_repeat_noop_use_stable_relative_output_only(
    posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identifiers = prepare_snapshot(posix_tmp_path)
    monkeypatch.setenv("KIS_DATA_DIR", str(posix_tmp_path))

    assert main(_args(identifiers)) == 0
    first = capsys.readouterr()
    assert "status=PASS" in first.out
    assert "evidence=COMPLETE" in first.out
    assert "bundle=quality/" in first.out
    assert str(posix_tmp_path) not in first.out + first.err

    assert main(_args(identifiers)) == 0
    second = capsys.readouterr()
    assert "idempotent=true" in second.out
    assert second.err == ""


def test_cli_fail_on_quality_publishes_truthful_bundle_then_returns_one(
    posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identifiers = prepare_snapshot(
        posix_tmp_path,
        data_sessions=(date(2026, 7, 20),),
    )
    monkeypatch.setenv("KIS_DATA_DIR", str(posix_tmp_path))
    args = _args(identifiers)
    args[args.index("2026-07-21")] = "2026-07-20"
    args.append("--fail-on-quality")

    assert main(args) == 1
    captured = capsys.readouterr()
    assert "status=FAIL" in captured.out
    assert (posix_tmp_path / "quality" / "latest-manifest.json").exists()


def test_cli_incomplete_evidence_precedes_quality_gate_and_returns_three(
    posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identifiers = prepare_snapshot(posix_tmp_path)
    monkeypatch.setenv("KIS_DATA_DIR", str(posix_tmp_path))
    args = _args(identifiers, collection=False)
    args.extend(("--require-complete-evidence", "--fail-on-quality"))

    assert main(args) == 3
    captured = capsys.readouterr()
    assert "evidence=PARTIAL" in captured.out
    assert (posix_tmp_path / "quality" / "latest-manifest.json").exists()


def test_cli_input_error_returns_two_without_echo_and_preserves_last_good(
    posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identifiers = prepare_snapshot(posix_tmp_path)
    monkeypatch.setenv("KIS_DATA_DIR", str(posix_tmp_path))
    assert main(_args(identifiers)) == 0
    capsys.readouterr()
    latest = posix_tmp_path / "quality" / "latest-manifest.json"
    last_good = latest.read_bytes()
    malicious = "/private/credential-token-canary.json"
    args = _args(identifiers)
    args[args.index(identifiers.universe)] = malicious

    assert main(args) == 2
    captured = capsys.readouterr()
    assert "code=INPUT_OR_PUBLISH_ERROR" in captured.err
    assert malicious not in captured.out + captured.err
    assert str(posix_tmp_path) not in captured.out + captured.err
    assert latest.read_bytes() == last_good


def test_reporter_cli_does_not_create_network_send(
    posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifiers = prepare_snapshot(posix_tmp_path)
    monkeypatch.setenv("KIS_DATA_DIR", str(posix_tmp_path))

    def forbidden_network(*args, **kwargs):
        raise AssertionError("reporter network send must remain zero")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    assert main(_args(identifiers)) == 0


def test_cli_usage_error_does_not_echo_raw_argument(
    capsys: pytest.CaptureFixture[str],
) -> None:
    canary = "credential-token-canary"

    assert main(["generate", "--unknown", canary]) == 2
    captured = capsys.readouterr()
    assert "code=USAGE_ERROR" in captured.err
    assert canary not in captured.out + captured.err


def test_cli_passes_one_wall_deadline_guard_through_all_phases(
    posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.data.quality import cli

    identifiers = prepare_snapshot(posix_tmp_path)
    monkeypatch.setenv("KIS_DATA_DIR", str(posix_tmp_path))
    observed_guards: list[object] = []
    original_load = cli.load_quality_snapshot
    original_analyze = cli.analyze_quality
    original_publish = cli.publish_quality_bundle

    def guarded_load(*, deadline_check, **kwargs):
        deadline_check()
        observed_guards.append(deadline_check)
        return original_load(deadline_check=deadline_check, **kwargs)

    def guarded_analyze(context, datasets, *, deadline_check):
        deadline_check()
        observed_guards.append(deadline_check)
        return original_analyze(context, datasets, deadline_check=deadline_check)

    def guarded_publish(root, report, *, deadline_check):
        deadline_check()
        observed_guards.append(deadline_check)
        return original_publish(root, report, deadline_check=deadline_check)

    monkeypatch.setattr(cli, "load_quality_snapshot", guarded_load)
    monkeypatch.setattr(cli, "analyze_quality", guarded_analyze)
    monkeypatch.setattr(cli, "publish_quality_bundle", guarded_publish)

    assert main(_args(identifiers)) == 0
    assert len(observed_guards) == 3
    assert len({id(guard) for guard in observed_guards}) == 1
