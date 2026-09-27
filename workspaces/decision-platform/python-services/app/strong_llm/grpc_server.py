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
from app.strong_llm.models import Evidence, JudgementCandidate, RunRequest
from app.strong_llm.provider import ProviderChainSettings, build_provider_chain
from app.strong_llm.runtime import BoundedStrongLlmGraph, StrongLlmProvider
from app.strong_llm.vertex_provider import VertexProviderSettings

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


# run 하나에 1차와 선택적 2차를 함께 세운다. 둘을 따로 만들면 permit마다 다시 세우게 되고,
# 그 사이에 credential 읽기가 실패하면 같은 run이 다른 provider로 이어질 수 있다.
ProviderFactory = Callable[[RunRequest], tuple[StrongLlmProvider, StrongLlmProvider | None]]
# 사용자 자기 Vertex 서비스 계정(표준 Base64 한 줄)으로 같은 chain 을 세운다.
OwnerProviderFactory = Callable[
    [RunRequest, str], tuple[StrongLlmProvider, StrongLlmProvider | None]
]


class StrongLlmAgentServicer(strong_llm_agent_pb2_grpc.StrongLlmAgentServiceServicer):
    """bidi host가 permit을 보낸 뒤에만 provider call을 수행하는 single-run stream이다."""

    def __init__(
        self,
        shared_secret: str,
        provider_factory: ProviderFactory,
        owner_provider_factory: OwnerProviderFactory | None = None,
    ) -> None:
        self._secret = shared_secret
        self._provider_factory = provider_factory
        # 사용자 자기 Vertex 서비스 계정으로 부르는 경로. 없으면 사용자 자격증명이 실린 run 은 운영자
        # 자격증명으로 조용히 바꾸지 않고 실패한다 - 누구의 비용으로 불렀는지가 host 기록과 어긋나면 안 된다.
        self._owner_provider_factory = owner_provider_factory
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
        # 비밀은 RunRequest 에 넣지 않는다. 모델 repr·검증 오류 메시지에 실려 나갈 길을 만들지 않는다.
        owner_credential_b64 = first.start_run.owner_vertex_service_account_json_b64
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
                if owner_credential_b64:
                    if self._owner_provider_factory is None:
                        raise ValueError("STRONG_LLM_OWNER_CREDENTIAL_UNSUPPORTED")
                    primary, secondary = self._owner_provider_factory(
                        request, owner_credential_b64
                    )
                else:
                    primary, secondary = self._provider_factory(request)
                result = self._graph.run(
                    request, primary, permit, execute_tool, fallback_provider=secondary
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
                            provider_id=result.provider_id,
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
        # host가 값을 보내지 않은 예전 프레임도 받는다. 그때의 뜻은 기존 동작과 같아야 한다.
        language=start.language or "ko",
        mode=start.mode or "EXPLAIN",
        candidates=tuple(_candidate(item) for item in start.candidates),
        thinking_level=start.thinking_level or "low",
        grounding_discovery_only=start.grounding_discovery_only,
    )


def _candidate(item: strong_llm_agent_pb2.JudgementCandidate) -> JudgementCandidate:
    return JudgementCandidate(
        item.symbol,
        item.expected_return,
        item.lstm_signal,
        item.baseline_signal,
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
    chain_settings = ProviderChainSettings.from_env()
    # Vertex를 쓸 배포라면 서비스계정을 기동에서 읽는다. run 때 처음 읽으면 credential이 없는
    # 배포가 멀쩡히 뜬 뒤 모든 판단 요청에서 실패한다.
    declared = {chain_settings.primary.provider}
    if chain_settings.secondary is not None:
        declared.add(chain_settings.secondary.provider)
    vertex = VertexProviderSettings.from_env() if "vertex" in declared else None

    def default_factory(
        request: RunRequest,
    ) -> tuple[StrongLlmProvider, StrongLlmProvider | None]:
        effective_vertex = (
            vertex.for_thinking_level(request.thinking_level) if vertex is not None else None
        )
        return build_provider_chain(request, chain_settings, effective_vertex)

    def owner_factory(
        request: RunRequest, owner_credential_b64: str
    ) -> tuple[StrongLlmProvider, StrongLlmProvider | None]:
        # 사용자 키는 1차 Vertex 에만 쓴다. 운영 배포 설정의 timeout·출력 상한은 운영자 설정을 따른다.
        if vertex is None:
            raise ValueError("STRONG_LLM_OWNER_CREDENTIAL_UNSUPPORTED")
        owned = VertexProviderSettings.from_b64(
            owner_credential_b64,
            timeout_seconds=vertex.timeout_seconds,
            thinking_level=request.thinking_level,
            max_output_tokens=vertex.max_output_tokens,
        )
        return build_provider_chain(request, chain_settings, owned)

    factory = provider_factory or default_factory
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=8),
        options=(
            ("grpc.max_receive_message_length", 262_144),
            ("grpc.max_send_message_length", 262_144),
        ),
    )
    strong_llm_agent_pb2_grpc.add_StrongLlmAgentServiceServicer_to_server(  # type: ignore[no-untyped-call]
        StrongLlmAgentServicer(
            effective.shared_secret,
            factory,
            owner_factory if provider_factory is None else None,
        ),
        server,
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


if __name__ == "__main__":
    serve()
