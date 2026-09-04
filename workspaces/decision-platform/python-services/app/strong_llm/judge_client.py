from __future__ import annotations

import json
import os
import queue
import re
from collections.abc import Iterator
from dataclasses import dataclass

import grpc

from app.generated import strong_llm_agent_pb2, strong_llm_agent_pb2_grpc
from app.strong_llm.models import StrongLlmJudgement

# Automation uses the loopback judge; the RAG host remains the citation and usage authority.
_AUTH_KEY = "x-decision-strong-llm-grpc-auth"
_RUN_ID = re.compile(r"^s49_run_[0-9a-f]{32}$")
_SAFE_SECRET = re.compile(r"^[A-Za-z0-9._~:-]{32,256}$")
# One primary and one fallback provider call are allowed per judgement.
_MAX_PROVIDER_CALLS = 2
_DEADLINE_SECONDS = 60.0


class StrongLlmJudgeUnavailableError(RuntimeError):
    """판단을 받지 못했다. 자동매매는 이 경우에도 규칙만으로 계속한다."""


@dataclass(frozen=True, slots=True)
class JudgeClientSettings:
    target: str
    shared_secret: str

    @classmethod
    def from_env(cls) -> JudgeClientSettings | None:
        """설정이 온전할 때만 client를 만든다. 반쯤 설정된 상태로 붙지 않는다."""

        if os.environ.get("S4_9_STRONG_LLM_ENABLED", "false").lower() != "true":
            return None
        target = os.environ.get("STRONG_LLM_GRPC_TARGET", "127.0.0.1:50055").strip()
        secret = os.environ.get("STRONG_LLM_GRPC_SHARED_SECRET", "").strip()
        if not target.startswith("127.0.0.1:") or _SAFE_SECRET.fullmatch(secret) is None:
            return None
        return cls(target, secret)


class StrongLlmJudgeClient:
    """S4.9 bidi stream의 host 쪽. permit을 세어 발급하고 Completed 하나만 받아 온다."""

    def __init__(self, settings: JudgeClientSettings) -> None:
        self._settings = settings

    def judge(
        self,
        *,
        run_id: str,
        model_id: str,
        question: str,
        language: str,
        candidates: tuple[strong_llm_agent_pb2.JudgementCandidate, ...],
    ) -> StrongLlmJudgement:
        if _RUN_ID.fullmatch(run_id) is None:
            raise StrongLlmJudgeUnavailableError("STRONG_LLM_JUDGE_RUN_ID_INVALID")
        outbound: queue.Queue[strong_llm_agent_pb2.HostEvent | None] = queue.Queue(maxsize=8)
        sequence = _Sequence(run_id)
        outbound.put(
            sequence.event(
                "start",
                start_run=strong_llm_agent_pb2.StartRun(
                    model_id=model_id,
                    question=question,
                    answer_mode="DETAILED",
                    # 판단에는 공개 웹도 owner 문서도 붙이지 않는다. 후보와 그 수치가 전부다.
                    google_search_enabled=False,
                    max_tool_rounds=0,
                    current_time="",
                    timezone="Asia/Seoul",
                    language=language,
                    mode="JUDGE",
                    candidates=list(candidates),
                ),
            )
        )
        channel = grpc.insecure_channel(
            self._settings.target,
            options=(
                ("grpc.max_receive_message_length", 262_144),
                ("grpc.max_send_message_length", 262_144),
            ),
        )
        try:
            stub = strong_llm_agent_pb2_grpc.StrongLlmAgentServiceStub(channel)  # type: ignore[no-untyped-call]
            events = stub.Generate(
                _drain(outbound),
                metadata=((_AUTH_KEY, self._settings.shared_secret),),
                timeout=_DEADLINE_SECONDS,
            )
            return self._consume(events, outbound, sequence)
        except grpc.RpcError as error:
            raise StrongLlmJudgeUnavailableError("STRONG_LLM_JUDGE_TRANSPORT_FAILED") from error
        finally:
            outbound.put(None)
            channel.close()

    def _consume(
        self,
        events: Iterator[strong_llm_agent_pb2.AgentEvent],
        outbound: queue.Queue[strong_llm_agent_pb2.HostEvent | None],
        sequence: _Sequence,
    ) -> StrongLlmJudgement:
        permitted = 0
        for event in events:
            payload = event.WhichOneof("payload")
            if payload == "provider_call_planned":
                permitted += 1
                if permitted > _MAX_PROVIDER_CALLS:
                    raise StrongLlmJudgeUnavailableError("STRONG_LLM_JUDGE_BUDGET_EXCEEDED")
                outbound.put(
                    sequence.event(
                        event.call_id,
                        provider_call_permit=strong_llm_agent_pb2.ProviderCallPermit(
                            planned_call_id=event.provider_call_planned.planned_call_id
                        ),
                    )
                )
            elif payload in {"web_search", "web_read"}:
                # 판단 turn에는 도구를 붙이지 않았다. 그래도 왔다면 계약이 어긋난 것이다.
                raise StrongLlmJudgeUnavailableError("STRONG_LLM_JUDGE_TOOL_CALL_FORBIDDEN")
            elif payload == "completed":
                return StrongLlmJudgement.model_validate_json(event.completed.answer_json)
            elif payload == "failed":
                raise StrongLlmJudgeUnavailableError("STRONG_LLM_JUDGE_AGENT_FAILED")
        raise StrongLlmJudgeUnavailableError("STRONG_LLM_JUDGE_STREAM_CLOSED")


class _Sequence:
    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        self._value = 0

    def event(self, call_id: str, **payload: object) -> strong_llm_agent_pb2.HostEvent:
        self._value += 1
        return strong_llm_agent_pb2.HostEvent(
            run_id=self._run_id,
            sequence=self._value,
            call_id=call_id,
            **payload,  # type: ignore[arg-type]
        )


def _drain(
    outbound: queue.Queue[strong_llm_agent_pb2.HostEvent | None],
) -> Iterator[strong_llm_agent_pb2.HostEvent]:
    while True:
        item = outbound.get()
        if item is None:
            return
        yield item


def judgement_json(value: StrongLlmJudgement) -> str:
    """원장에 남길 정규 형태. 모델이 쓴 글은 여기까지만 오고 결정에 다시 쓰이지 않는다."""

    return json.dumps(value.model_dump(), ensure_ascii=False, separators=(",", ":"))
