from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, cast

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.strong_llm.models import RunRequest, answer_model
from app.strong_llm.prompt import render_prompt
from app.strong_llm.runtime import ProviderResult, StrongLlmProvider
from app.strong_llm.vertex_provider import LangChainVertexProvider, VertexProviderSettings

# Strong LLM은 교체 가능해야 한다. 어떤 벤더든 같은 프롬프트를 받고 같은 출력 계약을 통과한다.
# `vertex`만 서비스계정과 Google grounding을 쓰고 나머지는 API 키 하나로 붙는다. `custom`은
# 사용자가 직접 넣는 OpenAI 호환 endpoint다 - 모델 이름도 base_url도 우리가 알지 못한다.
_LANGCHAIN_PROVIDER = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google_genai": "google_genai",
    "custom": "openai",
}
SUPPORTED_PROVIDERS = ("vertex", *sorted(_LANGCHAIN_PROVIDER))

# provider마다 출력 상한의 인자 이름이 다르다. 이 표가 유일한 차이라 분기 대신 표로 둔다.
_MAX_TOKEN_KWARG = {"google_genai": "max_output_tokens"}


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """provider 하나를 세우는 데 필요한 전부. API 키는 여기까지만 오고 로그로 나가지 않는다."""

    provider: str
    model_id: str = ""
    api_key: str = ""
    base_url: str = ""
    timeout_seconds: float = 50.0
    max_output_tokens: int = 32_768

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError("STRONG_LLM_PROVIDER_UNSUPPORTED")
        if self.provider != "vertex" and not self.api_key:
            raise ValueError("STRONG_LLM_PROVIDER_API_KEY_MISSING")
        if self.provider == "custom" and not self.base_url:
            raise ValueError("STRONG_LLM_PROVIDER_BASE_URL_MISSING")
        if not 10.0 <= self.timeout_seconds <= 55.0:
            raise ValueError("STRONG_LLM_PROVIDER_TIMEOUT_INVALID")
        if not 1 <= self.max_output_tokens <= 32768:
            raise ValueError("STRONG_LLM_PROVIDER_OUTPUT_CAP_INVALID")


@dataclass(frozen=True, slots=True)
class ProviderChainSettings:
    """1차와 선택적 2차. 2차가 없으면 1차 실패가 곧 전체 실패다."""

    primary: ProviderSpec
    secondary: ProviderSpec | None = None

    @classmethod
    def from_env(cls) -> ProviderChainSettings:
        return cls(primary=_spec_from_env(""), secondary=_optional_spec_from_env("FALLBACK_"))


def _spec_from_env(prefix: str) -> ProviderSpec:
    return ProviderSpec(
        provider=os.environ.get(f"STRONG_LLM_{prefix}PROVIDER", "vertex").strip() or "vertex",
        model_id=os.environ.get(f"STRONG_LLM_{prefix}MODEL_ID", "").strip(),
        api_key=os.environ.get(f"STRONG_LLM_{prefix}API_KEY", "").strip(),
        base_url=os.environ.get(f"STRONG_LLM_{prefix}BASE_URL", "").strip(),
    )


def _optional_spec_from_env(prefix: str) -> ProviderSpec | None:
    if not os.environ.get(f"STRONG_LLM_{prefix}PROVIDER", "").strip():
        return None
    return _spec_from_env(prefix)


class LangChainChatProvider:
    """Vertex가 아닌 provider. 웹 도구를 붙이지 않고 구조화 출력 한 번으로 끝낸다.

    Google grounding과 SearXNG tool loop는 Vertex 경로의 기능이다. 여기서 흉내내면 provider마다
    다른 근거 규칙이 생기고, 그러면 "어떤 provider로 나온 답인지"가 검증 결과를 바꾼다. 그래서
    이 provider는 주어진 근거만으로 답하고 도구 호출을 내지 않는다.
    """

    supports_google_search = False

    def __init__(self, request: RunRequest, spec: ProviderSpec) -> None:
        self.provider_id = spec.provider
        self._schema: type[BaseModel] = answer_model(request.mode)
        self._structured = _chat_model(request, spec).with_structured_output(
            self._schema, include_raw=True
        )

    def invoke_google(self, request: RunRequest, *, include_owner: bool) -> ProviderResult:
        raise ValueError("STRONG_LLM_PROVIDER_GOOGLE_SEARCH_UNSUPPORTED")

    def invoke_fallback(
        self,
        request: RunRequest,
        messages: list[BaseMessage],
        *,
        tools_enabled: bool,
    ) -> ProviderResult:
        prompt = render_prompt(request, request.public_evidence + request.owner_evidence)
        outcome = cast(
            dict[str, Any],
            self._structured.invoke(
                messages
                or [SystemMessage(content=prompt.system), HumanMessage(content=prompt.user)]
            ),
        )
        error = outcome.get("parsing_error")
        parsed = outcome.get("parsed")
        if error is not None or not isinstance(parsed, self._schema):
            # 계약을 못 지킨 출력이다. 같은 프롬프트라도 다른 provider는 지킬 수 있으므로
            # 이 실패는 2차로 넘어갈 자격이 있다. 그래서 leaf code가 아닌 예외로 올린다.
            raise RuntimeError("STRONG_LLM_PROVIDER_STRUCTURED_OUTPUT_INVALID") from error
        raw = cast(AIMessage, outcome["raw"])
        usage: dict[str, Any] = dict(raw.usage_metadata or {})
        return ProviderResult(
            message=raw,
            answer_json=parsed.model_dump_json(),
            prompt_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            google_queries=[],
            grounding_roots=[],
            grounding_supports=[],
        )

    def tool_calls(self, message: AIMessage) -> list[dict[str, object]]:
        return []

    def append_tool_result(
        self,
        messages: list[BaseMessage],
        message: AIMessage,
        call: dict[str, object],
        result_json: str,
    ) -> list[BaseMessage]:
        raise ValueError("STRONG_LLM_PROVIDER_TOOL_LOOP_UNSUPPORTED")


def _chat_model(request: RunRequest, spec: ProviderSpec) -> BaseChatModel:
    kwargs: dict[str, Any] = {
        "api_key": spec.api_key,
        "timeout": spec.timeout_seconds,
        # 재시도는 host가 permit과 함께 세는 것이라 client가 몰래 늘리면 예산이 어긋난다.
        "max_retries": 0,
        _MAX_TOKEN_KWARG.get(spec.provider, "max_tokens"): spec.max_output_tokens,
    }
    if spec.base_url:
        kwargs["base_url"] = spec.base_url
    model = init_chat_model(
        spec.model_id or request.model_id,
        model_provider=_LANGCHAIN_PROVIDER[spec.provider],
        **kwargs,
    )
    return cast(BaseChatModel, model)


def build_provider(
    request: RunRequest, spec: ProviderSpec, vertex: VertexProviderSettings | None = None
) -> StrongLlmProvider:
    if spec.provider != "vertex":
        return LangChainChatProvider(request, spec)
    return LangChainVertexProvider(request, vertex or VertexProviderSettings.from_env())


def build_provider_chain(
    request: RunRequest,
    settings: ProviderChainSettings,
    vertex: VertexProviderSettings | None = None,
) -> tuple[StrongLlmProvider, StrongLlmProvider | None]:
    """2차는 세우다 실패해도 1차를 죽이지 않는다. 키가 잘못된 2차 때문에 답이 안 나오면 안 된다."""

    primary = build_provider(request, settings.primary, vertex)
    if settings.secondary is None:
        return primary, None
    try:
        return primary, build_provider(request, settings.secondary, vertex)
    except Exception:  # noqa: BLE001 - 2차 구성 실패는 1차 경로를 막지 않는다.
        return primary, None
