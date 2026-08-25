from __future__ import annotations

import os
import queue
import re
import threading
from collections.abc import Callable, Iterator
from concurrent import futures
from dataclasses import asdict, dataclass
from hmac import compare_digest
from typing import cast

import grpc
from google.genai.errors import APIError
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from pydantic import ValidationError

from app.generated import strong_llm_agent_pb2, strong_llm_agent_pb2_grpc
from app.strong_llm.models import Evidence, RunRequest
from app.strong_llm.runtime import BoundedStrongLlmGraph, StrongLlmProvider
from app.strong_llm.vertex_provider import LangChainVertexProvider, VertexProviderSettings

_AUTH_KEY = "x-decision-strong-llm-grpc-auth"
_SAFE_SECRET = re.compile(r"^[A-Za-z0-9._~:-]{32,256}$")
_RUN_ID = re.compile(r"^s49_run_[0-9a-f]{32}$")


@dataclass(frozen=True, slots=True)
class StrongLlmGrpcSettings:
    bind_address: str
    shared_secret: str

    @classmethod
    def from_env(cls) -> StrongLlmGrpcSettings:
        bind = os.environ.get("STRONG_LLM_GRPC_BIND_ADDRESS", "127.0.0.1:50055").strip()
        secret = os.environ.get("STRONG_LLM_GRPC_SHARED_SECRET", "").strip()
        if not bind.startswith("127.0.0.1:") or _SAFE_SECRET.fullmatch(secret) is None:
            raise ValueError("Strong LLM gRPC settings are invalid")
        return cls(bind, secret)


ProviderFactory = Callable[[RunRequest], StrongLlmProvider]


class StrongLlmAgentServicer(strong_llm_agent_pb2_grpc.StrongLlmAgentServiceServicer):
    """bidi host가 permit을 보낸 뒤에만 provider call을 수행하는 single-run stream이다."""

    def __init__(self, shared_secret: str, provider_factory: ProviderFactory) -> None:
        self._secret = shared_secret
        self._provider_factory = provider_factory
        self._graph = BoundedStrongLlmGraph()

    def Generate(
        self,
        request_iterator: Iterator[strong_llm_agent_pb2.HostEvent],
        context: grpc.ServicerContext,
    ) -> Iterator[strong_llm_agent_pb2.AgentEvent]:
        supplied = cast(str, dict(context.invocation_metadata()).get(_AUTH_KEY, ""))
        if not compare_digest(supplied, self._secret):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "invalid Strong LLM gRPC authentication")
        inbound: queue.Queue[object] = queue.Queue(maxsize=8)
        outbound: queue.Queue[object] = queue.Queue(maxsize=8)

        def read_requests() -> None:
            try:
                for event in request_iterator:
                    inbound.put(event)
            except Exception as error:  # gRPC disconnect is converted to typed cancellation below.
                inbound.put(error)
            finally:
                inbound.put(_END)

        threading.Thread(target=read_requests, daemon=True).start()
        first = inbound.get(timeout=5)
        if (
            not isinstance(first, strong_llm_agent_pb2.HostEvent)
            or first.WhichOneof("payload") != "start_run"
        ):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "StartRun must be the first frame")
        request = _request(first)
        sequence = _Sequence(request.run_id)
        permitted_provider_calls = [0]

        def permit(call_id: str, phase: str, google_attached: bool) -> None:
            outbound.put(
                sequence.event(
                    call_id,
                    provider_call_planned=strong_llm_agent_pb2.ProviderCallPlanned(
                        planned_call_id=call_id,
                        phase=phase,
                        google_search_attached=google_attached,
                    ),
                )
            )
            response = _next_host(inbound, request.run_id)
            if (
                response.WhichOneof("payload") != "provider_call_permit"
                or response.provider_call_permit.planned_call_id != call_id
            ):
                raise ValueError("STRONG_LLM_PROVIDER_PERMIT_INVALID")
            permitted_provider_calls[0] += 1

        def execute_tool(call_id: str, name: str, arguments: dict[str, object]) -> str:
            if name == "capstone_web_search":
                query = arguments.get("query")
                if not isinstance(query, str):
                    raise ValueError("STRONG_LLM_SEARCH_ARGUMENT_INVALID")
                outbound.put(
                    sequence.event(
                        call_id,
                        web_search=strong_llm_agent_pb2.WebSearch(
                            tool_call_id=call_id, query=query
                        ),
                    )
                )
            else:
                result_id = arguments.get("resultId")
                if not isinstance(result_id, str):
                    raise ValueError("STRONG_LLM_READ_ARGUMENT_INVALID")
                outbound.put(
                    sequence.event(
                        call_id,
                        web_read=strong_llm_agent_pb2.WebRead(
                            tool_call_id=call_id, result_id=result_id
                        ),
                    )
                )
            response = _next_host(inbound, request.run_id)
            if (
                response.WhichOneof("payload") != "tool_result"
                or response.tool_result.tool_call_id != call_id
            ):
                raise ValueError("STRONG_LLM_TOOL_RESULT_INVALID")
            if response.tool_result.failed:
                raise ValueError(response.tool_result.failure_leaf or "STRONG_LLM_TOOL_FAILED")
            return response.tool_result.result_json

        def worker() -> None:
            try:
                result = self._graph.run(
                    request, self._provider_factory(request), permit, execute_tool
                )
                outbound.put(
                    sequence.event(
                        "completed",
                        completed=strong_llm_agent_pb2.Completed(
                            answer_json=result.answer_json,
                            prompt_token_count=result.prompt_token_count,
                            output_token_count=result.output_token_count,
                            vertex_generate_call_count=result.vertex_generate_call_count,
                            google_grounding_query_count=result.google_grounding_query_count,
                            search_backend=result.search_backend,
                            evidence_validation_mode=result.evidence_validation_mode,
                            grounding_roots=[
                                strong_llm_agent_pb2.GroundingRoot(**asdict(item))
                                for item in result.grounding_roots
                            ],
                            grounding_supports=[
                                strong_llm_agent_pb2.GroundingSupport(**asdict(item))
                                for item in result.grounding_supports
                            ],
                            web_search_queries=result.web_search_queries,
                        ),
                    )
                )
            except Exception as error:
                outbound.put(
                    sequence.event(
                        "failed",
                        failed=strong_llm_agent_pb2.Failed(
                            failure_leaf=_failure_leaf(error),
                            provider_attempted=permitted_provider_calls[0] > 0,
                            vertex_generate_call_count=permitted_provider_calls[0],
                        ),
                    )
                )
            finally:
                outbound.put(_END)

        threading.Thread(target=worker, daemon=True).start()
        while True:
            item = outbound.get()
            if item is _END:
                return
            yield cast(strong_llm_agent_pb2.AgentEvent, item)


class _Sequence:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.value = 0

    def event(self, call_id: str, **payload: object) -> strong_llm_agent_pb2.AgentEvent:
        self.value += 1
        return strong_llm_agent_pb2.AgentEvent(
            run_id=self.run_id,
            sequence=self.value,
            call_id=call_id,
            **payload,  # type: ignore[arg-type]
        )


def _request(event: strong_llm_agent_pb2.HostEvent) -> RunRequest:
    start = event.start_run
    if event.sequence != 1 or not _RUN_ID.fullmatch(event.run_id):
        raise ValueError("STRONG_LLM_START_FRAME_INVALID")
    return RunRequest(
        run_id=event.run_id,
        model_id=start.model_id,
        question=start.question,
        answer_mode=start.answer_mode,
        related_symbols=tuple(start.related_symbols),
        topics=tuple(start.topics),
        public_evidence=tuple(_evidence(item, False) for item in start.public_evidence),
        owner_evidence=tuple(_evidence(item, True) for item in start.owner_evidence),
        google_search_enabled=start.google_search_enabled,
        max_tool_rounds=start.max_tool_rounds,
        current_time=start.current_time,
        timezone=start.timezone,
    )


def _evidence(item: strong_llm_agent_pb2.EvidenceItem, owner: bool) -> Evidence:
    return Evidence(
        item.ordinal,
        item.citation_id,
        item.chunk_revision_id,
        item.canonical_text,
        item.canonical_text_sha256,
        owner,
    )


def _next_host(inbound: queue.Queue[object], run_id: str) -> strong_llm_agent_pb2.HostEvent:
    item = inbound.get(timeout=35)
    if not isinstance(item, strong_llm_agent_pb2.HostEvent) or item.run_id != run_id:
        raise ValueError("STRONG_LLM_HOST_FRAME_INVALID")
    return item


def _failure_leaf(error: Exception) -> str:
    if isinstance(error, ValidationError):
        first = error.errors(include_url=False, include_context=False, include_input=False)[0]
        location = "_".join(str(part) for part in first.get("loc", ())) or "ROOT"
        error_type = str(first.get("type", "INVALID"))
        detail = re.sub(r"[^A-Z0-9]+", "_", f"{error_type}_{location}".upper()).strip("_")
        return f"STRONG_LLM_SCHEMA_{detail}"[:96]
    cause: BaseException | None = error
    while cause is not None:
        if isinstance(cause, APIError):
            status = re.sub(r"[^A-Z0-9]+", "_", str(cause.status).upper()).strip("_")
            suffix = status if status else "UNKNOWN"
            return f"STRONG_LLM_VERTEX_HTTP_{cause.code}_{suffix}"[:96]
        cause = cause.__cause__
    text = str(error)
    return text if re.fullmatch(r"[A-Z0-9_]{3,96}", text) else type(error).__name__.upper()


_END = object()


def serve(
    settings: StrongLlmGrpcSettings | None = None, provider_factory: ProviderFactory | None = None
) -> None:
    effective = settings or StrongLlmGrpcSettings.from_env()
    vertex_settings = VertexProviderSettings.from_env()
    factory = provider_factory or (
        lambda request: LangChainVertexProvider(request, vertex_settings)
    )
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=8),
        options=(
            ("grpc.max_receive_message_length", 262_144),
            ("grpc.max_send_message_length", 262_144),
        ),
    )
    strong_llm_agent_pb2_grpc.add_StrongLlmAgentServiceServicer_to_server(  # type: ignore[no-untyped-call]
        StrongLlmAgentServicer(effective.shared_secret, factory), server
    )
    health_service = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_service, server)
    health_service.set(
        "capstone.decision.internal.s49.StrongLlmAgentService",
        health_pb2.HealthCheckResponse.SERVING,
    )
    _require_bound_port(server.add_insecure_port(effective.bind_address))
    server.start()
    server.wait_for_termination()


def _require_bound_port(bound_port: int) -> None:
    # gRPC는 성공 시 실제 bound port를 반환하며 0만 bind 실패를 뜻한다.
    if bound_port == 0:
        raise RuntimeError("Strong LLM gRPC loopback bind failed")
