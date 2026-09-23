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
            supervisor._validate_demo_runtime(
                {"S4_9_STRONG_LLM_ENABLED": "true", key: "true"}
            )
