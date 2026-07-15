from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.data.ecos import collect_cli
from app.data.ecos.collect_cli import main
from app.data.ecos.series_registry import CANDIDATE_SERIES


def _verified_series():
    verified_at = datetime(2026, 7, 13, tzinfo=UTC)
    return tuple(
        entry.model_copy(
            update={
                "verified": True,
                "registry_verified_at": verified_at,
                "name": f"synthetic-{entry.series_id}",
                "unit": "synthetic-unit",
            }
        )
        for entry in CANDIDATE_SERIES
    )


def _provisional_series():
    return tuple(
        entry.model_copy(
            update={
                "verified": False,
                "registry_verified_at": None,
                "name": None,
                "unit": None,
            }
        )
        for entry in CANDIDATE_SERIES
    )


def test_provisional_registry_stops_before_online_client_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builds = 0

    def fail_build(*args: object, **kwargs: object) -> object:
        nonlocal builds
        builds += 1
        raise AssertionError("provisional registry must stop before client construction")

    monkeypatch.setattr(collect_cli, "_load_series_registry", _provisional_series)
    monkeypatch.setattr(collect_cli, "_build_collector", fail_build)

    exit_code = main(["--online", "--from", "2026-07-01", "--to", "2026-07-14"])

    assert exit_code == 2
    assert builds == 0


def test_persist_without_online_is_rejected_before_client_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(collect_cli, "_load_series_registry", _verified_series)
    monkeypatch.setattr(
        collect_cli,
        "_build_collector",
        lambda *args, **kwargs: pytest.fail("invalid gate must not construct a client"),
    )

    assert main(["--persist", "--from", "2026-07-01", "--to", "2026-07-14"]) == 2


@pytest.mark.parametrize("persist", [False, True])
def test_online_and_persist_flags_are_forwarded_explicitly(
    monkeypatch: pytest.MonkeyPatch,
    persist: bool,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeCollector:
        def collect(self, **kwargs: Any) -> object:
            calls.append(kwargs)
            return type("Result", (), {"coverage": "complete", "partial": False})()

    monkeypatch.setattr(collect_cli, "_load_series_registry", _verified_series)
    monkeypatch.setattr(collect_cli, "_build_collector", lambda *args, **kwargs: FakeCollector())
    arguments = ["--online", "--from", "2026-07-01", "--to", "2026-07-14"]
    if persist:
        arguments.append("--persist")

    assert main(arguments) == 0
    assert len(calls) == 1
    assert calls[0]["persist"] is persist
    assert calls[0]["require_complete"] is False


def test_require_complete_is_online_only_and_forwarded_to_the_collector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeCollector:
        def collect(self, **kwargs: Any) -> object:
            calls.append(kwargs)
            return type("Result", (), {"coverage": "complete", "partial": False})()

    monkeypatch.setattr(collect_cli, "_load_series_registry", _verified_series)
    monkeypatch.setattr(collect_cli, "_build_collector", lambda *args, **kwargs: FakeCollector())
    base = ["--from", "2026-07-01", "--to", "2026-07-14", "--require-complete"]

    assert main(base) == 2
    assert calls == []

    assert main(["--online", *base]) == 0
    assert len(calls) == 1
    assert calls[0]["require_complete"] is True


def test_normal_partial_collection_returns_resume_exit_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PartialCollector:
        def collect(self, **kwargs: Any) -> object:
            del kwargs
            return type("Result", (), {"coverage": "partial", "partial": True})()

    monkeypatch.setattr(collect_cli, "_load_series_registry", _verified_series)
    monkeypatch.setattr(collect_cli, "_build_collector", lambda *args, **kwargs: PartialCollector())

    assert main(["--online", "--from", "2026-07-01", "--to", "2026-07-14"]) == 3


@pytest.mark.parametrize("max_attempts", [0, 3])
def test_invalid_retry_setting_exits_two_before_collector_construction(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    max_attempts: int,
) -> None:
    builds = 0

    def forbidden_build(*args: object, **kwargs: object) -> object:
        nonlocal builds
        builds += 1
        raise AssertionError("invalid settings must stop before resource construction")

    monkeypatch.setenv("ECOS_MAX_ATTEMPTS_PER_REQUEST", str(max_attempts))
    monkeypatch.setattr(collect_cli, "_load_series_registry", _verified_series)
    monkeypatch.setattr(collect_cli, "_build_collector", forbidden_build)

    exit_code = main(["--online", "--from", "2026-07-01", "--to", "2026-07-14"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert builds == 0
    assert captured.out == "source=ecos operation=macro_collect code=invalid_arguments\n"
    assert captured.err == ""


def test_invalid_argv_cannot_echo_a_mistaken_secret_or_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_argument = "--synthetic-key=https://ecos.bok.or.kr/private?key=synthetic-secret"

    assert main([secret_argument]) == 2

    captured = capsys.readouterr()
    assert captured.out == "source=ecos operation=macro_collect code=invalid_arguments\n"
    assert captured.err == ""
    assert "synthetic-secret" not in f"{captured.out}{captured.err}"
    assert "https://" not in f"{captured.out}{captured.err}"


def test_close_failure_cannot_override_the_stable_collection_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class CloseFailingCollector:
        def collect(self, **kwargs: Any) -> object:
            del kwargs
            return type("Result", (), {"coverage": "complete", "partial": False})()

        def close(self) -> None:
            raise RuntimeError(
                "synthetic-ecos-key https://ecos.bok.or.kr/private raw provider response"
            )

    monkeypatch.setattr(collect_cli, "_load_series_registry", _verified_series)
    monkeypatch.setattr(
        collect_cli, "_build_collector", lambda *args, **kwargs: CloseFailingCollector()
    )

    assert main(["--online", "--from", "2026-07-01", "--to", "2026-07-14"]) == 1

    rendered = capsys.readouterr().out
    assert rendered == "source=ecos operation=macro_collect code=collection_failed\n"
    assert "synthetic-ecos-key" not in rendered
    assert "https://" not in rendered
    assert "provider" not in rendered
