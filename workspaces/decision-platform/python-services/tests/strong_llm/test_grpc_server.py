from __future__ import annotations

import queue
from collections.abc import Iterator
from typing import NoReturn

import grpc
import pytest

from app.generated import strong_llm_agent_pb2
from app.strong_llm.grpc_server import StrongLlmAgentServicer, _require_bound_port
from tests.strong_llm.test_runtime import FakeProvider


class _Context:
    def invocation_metadata(self) -> tuple[tuple[str, str], ...]:
        return (("x-decision-strong-llm-grpc-auth", "s" * 64),)

    def abort(self, code: grpc.StatusCode, details: str) -> NoReturn:
        raise RuntimeError(f"{code.name}:{details}")


class _HostFrames:
    def __init__(self) -> None:
        self._queue: queue.Queue[object] = queue.Queue()

    def put(self, event: strong_llm_agent_pb2.HostEvent) -> None:
        self._queue.put(event)

    def __iter__(self) -> Iterator[strong_llm_agent_pb2.HostEvent]:
        while True:
            value = self._queue.get(timeout=5)
            if not isinstance(value, strong_llm_agent_pb2.HostEvent):
                return
            yield value


def test_grpc_stream_opens_provider_only_after_matching_host_permit() -> None:
    provider = FakeProvider()
    frames = _HostFrames()
    run_id = "s49_run_" + "1" * 32
    frames.put(
        strong_llm_agent_pb2.HostEvent(
            run_id=run_id,
            sequence=1,
            call_id="start",
            start_run=strong_llm_agent_pb2.StartRun(
                model_id="gemini-3.5-flash",
                question="분산투자를 설명해 주세요.",
                answer_mode="DETAILED",
                topics=["RISK"],
                google_search_enabled=True,
                max_tool_rounds=3,
                current_time="2026-08-15T00:00:00Z",
                timezone="Asia/Seoul",
            ),
        )
    )
    events = StrongLlmAgentServicer("s" * 64, lambda _request: provider).Generate(iter(frames), _Context())  # type: ignore[arg-type]

    planned = next(events)
    assert planned.WhichOneof("payload") == "provider_call_planned"
    assert planned.provider_call_planned.planned_call_id == "google_discovery"
    assert provider.invocations == []

    frames.put(
        strong_llm_agent_pb2.HostEvent(
            run_id=run_id,
            sequence=2,
            call_id="google_discovery",
            provider_call_permit=strong_llm_agent_pb2.ProviderCallPermit(
                planned_call_id="google_discovery"
            ),
        )
    )
    completed = next(events)
    assert completed.WhichOneof("payload") == "completed"
    assert completed.completed.vertex_generate_call_count == 1
    assert provider.invocations == [("google", False)]


def test_grpc_bind_accepts_the_actual_bound_port_and_rejects_only_zero() -> None:
    _require_bound_port(50055)

    with pytest.raises(RuntimeError, match="loopback bind failed"):
        _require_bound_port(0)
