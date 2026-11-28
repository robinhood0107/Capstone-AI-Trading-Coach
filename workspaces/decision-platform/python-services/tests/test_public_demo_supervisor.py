"""The public demo must never start background owner/account capabilities."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_SOURCE = Path(__file__).parents[4] / "deploy/p1/docker/decision-platform-supervisor.py"
_SPEC = importlib.util.spec_from_file_location("decision_platform_supervisor", _SOURCE)
assert _SPEC is not None and _SPEC.loader is not None
supervisor = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(supervisor)


def test_demo_requires_provider_and_rejects_owner_capabilities() -> None:
    with pytest.raises(RuntimeError, match="requires the bounded Agent provider"):
        supervisor._validate_demo_runtime({})
    supervisor._validate_demo_runtime({"S4_9_STRONG_LLM_ENABLED": "true"})
    for key in (
        "ASYNC_WORKER_ENABLED",
        "ASYNC_POLLING_ENABLED",
        "BROKERAGE_GRPC_ENABLED",
        "KIS_MOCK_BROKERAGE_ONLINE_ENABLED",
        "P1_AUTOMATION_RUNTIME_ENABLED",
        "RAG_V2_GRPC_ENABLED",
        "WORLD_NEWS_RETENTION_ENABLED",
    ):
        with pytest.raises(RuntimeError, match="forbids owner/background capabilities"):
            supervisor._validate_demo_runtime({"S4_9_STRONG_LLM_ENABLED": "true", key: "true"})


@pytest.mark.parametrize("optional", ["app.rag.rag_v2_grpc_server", "app.data.news.retention_cli"])
def test_optional_exit_keeps_required_children_alive(monkeypatch, tmp_path, capsys, optional):
    monkeypatch.setenv("MARS_PUBLIC_SURFACE_MODE", "FULL")
    monkeypatch.setenv("RAG_V2_GRPC_ENABLED", "true")
    monkeypatch.setenv("WORLD_NEWS_RETENTION_ENABLED", "true")
    monkeypatch.setenv("KIS_MOCK_BROKERAGE_ONLINE_ENABLED", "true")
    monkeypatch.setenv("P1_AUTOMATION_RUNTIME_ENABLED", "true")
    marker = tmp_path / "retention-running"
    monkeypatch.setattr(supervisor, "_RETENTION_RUNNING", marker)
    monkeypatch.setattr(supervisor, "_wait_for_spring", lambda _spring: True)

    children = {}

    class Child:
        def __init__(self, args, **_kwargs):
            key = args[2] if args[0] == "python" else args[0]
            self.status = 0 if key == optional else None
            children[key] = self

        def poll(self):
            return self.status

    def poll_required(_delay):
        for key in (
            "java",
            "app.brokerage.brokerage_grpc_server",
            "app.p1_owner.automation_runtime",
        ):
            assert children[key].status is None
        assert marker.exists() == (optional != "app.data.news.retention_cli")
        children["app.p1_owner.automation_runtime"].status = 7

    stopped = []
    monkeypatch.setattr(supervisor.subprocess, "Popen", Child)
    monkeypatch.setattr(supervisor.signal, "signal", lambda *_: None)
    monkeypatch.setattr(supervisor.time, "sleep", poll_required)
    monkeypatch.setattr(supervisor, "_terminate", stopped.extend)

    assert supervisor.main() == 7
    assert set(stopped) == set(children.values())
    assert not marker.exists()
    label = "rag-v2" if optional == "app.rag.rag_v2_grpc_server" else "world-news-retention"
    assert f"{label}=DEGRADED exit=0" in capsys.readouterr().err
