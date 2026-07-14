from __future__ import annotations

from typing import Any

import pytest

from app.data.ecos import collect_cli
from app.data.ecos.collect_cli import main
from app.data.ecos.series_registry import CANDIDATE_SERIES


def _verified_series():
    return tuple(entry.model_copy(update={"verified": True}) for entry in CANDIDATE_SERIES)


def test_provisional_registry_stops_before_online_client_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builds = 0

    def fail_build(*args: object, **kwargs: object) -> object:
        nonlocal builds
        builds += 1
        raise AssertionError("provisional registry must stop before client construction")

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
