from __future__ import annotations

import json
import os
from typing import cast

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import ValidationError

from app.strong_llm.models import Evidence, JudgementCandidate, RunRequest, answer_model
from app.strong_llm.provider import (
    SUPPORTED_PROVIDERS,
    LangChainChatProvider,
    ProviderChainSettings,
    ProviderSpec,
    build_provider_chain,
)
from app.strong_llm.runtime import BoundedStrongLlmGraph, ProviderResult, _is_provider_failure


def _explain_json() -> str:
    return json.dumps(
        {
            "basis": "MODEL_KNOWLEDGE",
            "answer": "분산투자는 일반적으로 위험 집중을 줄입니다.",
            "sentences": [
                {
                    "text": "분산투자는 일반적으로 위험 집중을 줄입니다.",
                    "citationIds": [],
                    "evidenceSpans": [],
                    "numericSpans": [],
                }
            ],
            "warnings": [],
        },
        ensure_ascii=False,
    )


def _judge_json() -> str:
    return json.dumps(
        {
            "candidates": [
                {
                    "symbol": "005930",
                    "score": 0.7,
                    "veto": False,
                    "reason": "근거에 위험 신호 없음.",
                    "evidenceSpans": [],
                }
            ],
            "summary": "후보 하나를 중립보다 조금 높게 본다.",
        },
        ensure_ascii=False,
    )


def _request(*, google: bool = False, mode: str = "EXPLAIN") -> RunRequest:
    return RunRequest(
        run_id="s49_run_" + "1" * 32,
        model_id="gemini-3.5-flash",
        question="분산투자를 설명해 주세요.",
        answer_mode="DETAILED",
        related_symbols=(),
        topics=("RISK",),
        public_evidence=(Evidence(1, "cit_1", "rag_v2_chk_" + "a" * 32, "public", "a" * 64),),
        owner_evidence=(),
        google_search_enabled=google,
        max_tool_rounds=3,
        current_time="2026-08-15T00:00:00Z",
        timezone="Asia/Seoul",
        mode=mode,
        candidates=((JudgementCandidate("005930", 0.02, "BUY", "BUY"),) if mode == "JUDGE" else ()),
    )


class _Stub:
    """provider 하나를 흉내낸다. 실패는 생성자가 받은 예외를 그대로 올린다."""

    def __init__(
        self,
        provider_id: str,
        *,
        error: Exception | None = None,
        answer: str | None = None,
        google: bool = True,
    ) -> None:
        self.provider_id = provider_id
        self.supports_google_search = google
        self._error = error
        self._answer = answer or _explain_json()
        self.calls = 0

    def _result(self) -> ProviderResult:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return cast(
            ProviderResult,
            {
                "message": AIMessage(content=self._answer),
                "answer_json": self._answer,
                "prompt_tokens": 10,
                "output_tokens": 5,
                "google_queries": [],
                "grounding_roots": [],
                "grounding_supports": [],
            },
        )

    def invoke_google(self, request: RunRequest, *, include_owner: bool) -> ProviderResult:
        return self._result()

    def invoke_fallback(
        self, request: RunRequest, messages: list[BaseMessage], *, tools_enabled: bool
    ) -> ProviderResult:
        return self._result()

    def tool_calls(self, message: AIMessage) -> list[dict[str, object]]:
        return []

    def append_tool_result(
        self,
        messages: list[BaseMessage],
        message: AIMessage,
        call: dict[str, object],
        result_json: str,
    ) -> list[BaseMessage]:
        raise AssertionError("stub does not run a tool loop")


def _run(
    primary: _Stub, secondary: _Stub | None, request: RunRequest
) -> tuple[list[tuple[str, str, bool]], object]:
    events: list[tuple[str, str, bool]] = []
    result = BoundedStrongLlmGraph().run(
        request,
        primary,
        lambda call_id, phase, attached: events.append((call_id, phase, attached)),
        lambda *_: pytest.fail("no host tool call is expected"),
        fallback_provider=secondary,
    )
    return events, result


# ---------------------------------------------------------------- 실패 분류


def test_leaf_invariant_failure_is_not_handed_to_the_second_provider() -> None:
    # leaf code를 담은 ValueError는 우리가 세운 경계다. provider를 바꿔도 결론이 같으므로
    # 재시도하면 같은 경계를 두 번 두드리게 된다.
    assert _is_provider_failure(ValueError("STRONG_LLM_OWNER_PUBLIC_DISCOVERY_FORBIDDEN")) is False


def test_contract_violation_and_transport_failure_are_handed_over() -> None:
    with pytest.raises(ValidationError) as raised:
        answer_model("EXPLAIN").model_validate_json("{}")
    # ValidationError는 ValueError의 하위형이라 검사 순서가 뒤바뀌면 조용히 안 넘어간다.
    assert _is_provider_failure(raised.value) is True
    assert _is_provider_failure(TimeoutError("read timeout")) is True


# ---------------------------------------------------------------- 체인 동작


def test_second_provider_answers_and_its_attempt_is_permitted_separately() -> None:
    primary = _Stub("openai", error=TimeoutError("read timeout"))
    secondary = _Stub("vertex")

    events, result = _run(primary, secondary, _request(google=True))

    assert events == [
        ("google_discovery", "GOOGLE_DISCOVERY", True),
        ("google_discovery_fallback1", "GOOGLE_DISCOVERY", True),
        ("grounded_final", "GROUNDED_FINAL", False),
    ]
    assert primary.calls == 1 and secondary.calls == 2
    assert getattr(result, "provider_id") == "vertex"


def test_leaf_failure_stops_the_run_without_touching_the_second_provider() -> None:
    primary = _Stub("openai", error=ValueError("STRONG_LLM_TOOL_ROUND_INVALID"))
    secondary = _Stub("vertex")

    with pytest.raises(ValueError, match="STRONG_LLM_TOOL_ROUND_INVALID"):
        _run(primary, secondary, _request(google=True))

    assert secondary.calls == 0


def test_a_run_without_a_second_provider_keeps_the_original_single_permit() -> None:
    primary = _Stub("vertex")

    events, result = _run(primary, None, _request(google=True))

    assert events == [
        ("google_discovery", "GOOGLE_DISCOVERY", True),
        ("grounded_final", "GROUNDED_FINAL", False),
    ]
    assert getattr(result, "provider_id") == "vertex"


def test_google_search_is_dropped_when_the_first_provider_cannot_attach_it() -> None:
    # OpenAI·Anthropic에는 Google grounding이 없다. 그 능력을 흉내내면 provider마다 다른
    # 근거 규칙이 생기므로 근거만으로 답하는 경로로 간다.
    primary = _Stub("openai", google=False)

    events, result = _run(primary, None, _request(google=True))

    assert [phase for _, phase, _ in events] == ["SEARXNG_TOOL"]
    assert getattr(result, "search_backend") == "SEARXNG"


def test_judge_mode_output_is_validated_against_the_judgement_contract() -> None:
    primary = _Stub("anthropic", answer=_judge_json(), google=False)

    _, result = _run(primary, None, _request(mode="JUDGE"))

    assert json.loads(getattr(result, "answer_json"))["candidates"][0]["symbol"] == "005930"


def test_judge_answer_is_rejected_when_it_is_shaped_like_an_explanation() -> None:
    primary = _Stub("anthropic", answer=_explain_json(), google=False)

    with pytest.raises(ValidationError):
        _run(primary, None, _request(mode="JUDGE"))


# ---------------------------------------------------------------- 구성


def test_every_supported_provider_builds_without_a_network_call() -> None:
    assert set(SUPPORTED_PROVIDERS) == {
        "vertex",
        "openai",
        "anthropic",
        "google_genai",
        "custom",
    }
    for provider in ("openai", "anthropic", "google_genai"):
        built = LangChainChatProvider(
            _request(), ProviderSpec(provider=provider, model_id="m", api_key="k")
        )
        assert built.provider_id == provider
        assert built.supports_google_search is False
    custom = LangChainChatProvider(
        _request(),
        ProviderSpec(
            provider="custom", model_id="m", api_key="k", base_url="https://example.invalid/v1"
        ),
    )
    assert custom.provider_id == "custom"


@pytest.mark.parametrize(
    "spec",
    [
        {"provider": "unknown", "api_key": "k"},
        {"provider": "openai", "api_key": ""},
        {"provider": "custom", "api_key": "k"},
        {"provider": "openai", "api_key": "k", "timeout_seconds": 90.0},
        {"provider": "openai", "api_key": "k", "max_output_tokens": 40000},
    ],
)
def test_provider_spec_refuses_configurations_the_host_cannot_bound(
    spec: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        ProviderSpec(**spec)  # type: ignore[arg-type]


def test_a_broken_second_provider_does_not_take_down_the_first() -> None:
    settings = ProviderChainSettings(
        primary=ProviderSpec(provider="openai", model_id="m", api_key="k"),
        secondary=ProviderSpec(provider="vertex"),
    )

    # 2차는 서비스계정 파일이 없어 세워지지 않는다. 그래도 1차 경로는 살아 있어야 한다.
    primary, secondary = build_provider_chain(_request(), settings)

    assert getattr(primary, "provider_id") == "openai"
    assert secondary is None


def test_the_chain_reads_the_second_provider_only_when_it_is_declared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in [key for key in os.environ if key.startswith("STRONG_LLM_")]:
        monkeypatch.delenv(name, raising=False)

    assert ProviderChainSettings.from_env().secondary is None

    monkeypatch.setenv("STRONG_LLM_FALLBACK_PROVIDER", "anthropic")
    monkeypatch.setenv("STRONG_LLM_FALLBACK_MODEL_ID", "claude")
    monkeypatch.setenv("STRONG_LLM_FALLBACK_API_KEY", "k")
    settings = ProviderChainSettings.from_env()

    assert settings.primary.provider == "vertex"
    assert settings.secondary is not None
    assert settings.secondary.provider == "anthropic"
