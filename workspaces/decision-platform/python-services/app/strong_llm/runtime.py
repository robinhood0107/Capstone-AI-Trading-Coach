from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import NotRequired, Protocol, TypedDict, cast

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.strong_llm.models import RunRequest, RunResult, answer_model


class ProviderResult(TypedDict):
    message: AIMessage
    answer_json: str
    prompt_tokens: int
    output_tokens: int
    google_queries: list[str]
    google_query_count: NotRequired[int]
    grounding_roots: list[dict[str, object]]
    grounding_supports: list[dict[str, object]]


class StrongLlmProvider(Protocol):
    def invoke_google(self, request: RunRequest, *, include_owner: bool) -> ProviderResult:
        """Gemini Google Search 또는 no-search 결정을 한 provider call에서 수행한다."""

    def invoke_fallback(
        self,
        request: RunRequest,
        messages: list[BaseMessage],
        *,
        tools_enabled: bool,
    ) -> ProviderResult:
        """SearXNG function-call 또는 tool-free structured final을 수행한다."""

    def tool_calls(self, message: AIMessage) -> list[dict[str, object]]:
        """원본 AIMessage에서 thought signature를 보존한 tool call만 투영한다."""

    def append_tool_result(
        self,
        messages: list[BaseMessage],
        message: AIMessage,
        call: dict[str, object],
        result_json: str,
    ) -> list[BaseMessage]:
        """원본 AIMessage와 exact call id를 다음 provider turn에 전달한다."""


Permit = Callable[[str, str, bool], None]
ToolExecutor = Callable[[str, str, dict[str, object]], str]
ProviderAction = Callable[[StrongLlmProvider], ProviderResult]


def _is_provider_failure(error: BaseException) -> bool:
    """2차로 넘길 자격이 있는 실패인지 가린다.

    provider가 못 답했거나 출력 계약을 어긴 것은 다른 provider면 다를 수 있다. 반면 leaf code를
    담은 `ValueError`는 우리가 세운 불변식(예: owner 근거를 공개 검색에 붙이지 않는다)이라
    provider를 바꿔도 결론이 같고, 재시도하면 같은 경계를 두 번 두드리는 셈이 된다.
    `ValidationError`가 `ValueError`의 하위형이라 검사 순서가 중요하다.
    """

    return isinstance(error, ValidationError) or not isinstance(error, ValueError)


class ProviderChain:
    """1차가 실패하면 2차로 넘어간다. 두 번째 시도도 host permit을 새로 받아 예산에 잡힌다.

    한 번 성공한 provider는 그 run 동안 고정된다. provider 객체가 discovery 결과와 대화 이력을
    들고 있어서 도중에 갈아타면 그 상태를 잃고, 그러면 tool 결과가 다른 대화에 붙는다.
    """

    def __init__(self, providers: Sequence[StrongLlmProvider]) -> None:
        if not providers:
            raise ValueError("STRONG_LLM_PROVIDER_CHAIN_EMPTY")
        self._providers = list(providers)

    @property
    def current(self) -> StrongLlmProvider:
        return self._providers[0]

    @property
    def provider_id(self) -> str:
        return str(getattr(self.current, "provider_id", ""))

    def attempt(
        self,
        permit: Permit,
        call_id: str,
        phase: str,
        google_attached: bool,
        action: ProviderAction,
    ) -> ProviderResult:
        last: BaseException | None = None
        for index, provider in enumerate(self._providers):
            permit(call_id if index == 0 else f"{call_id}_fallback{index}", phase, google_attached)
            try:
                result = action(provider)
            except Exception as error:
                if not _is_provider_failure(error) or index == len(self._providers) - 1:
                    raise
                last = error
                continue
            self._providers = [provider]
            return result
        raise cast(Exception, last)


class AgentState(TypedDict):
    request: RunRequest
    chain: ProviderChain
    permit: Permit
    execute_tool: ToolExecutor
    result: NotRequired[RunResult]


class BoundedStrongLlmGraph:
    """LangGraph는 provider 대화 상태만 소유하며 retry·DB·network 정책은 소유하지 않는다."""

    def __init__(self) -> None:
        graph = StateGraph(AgentState)
        graph.add_node("google", self._google)
        graph.add_node("fallback", self._fallback)
        graph.add_conditional_edges(
            START, self._route, {"google": "google", "fallback": "fallback"}
        )
        graph.add_edge("google", END)
        graph.add_edge("fallback", END)
        self._graph = graph.compile()

    def run(
        self,
        request: RunRequest,
        provider: StrongLlmProvider,
        permit: Permit,
        execute_tool: ToolExecutor,
        *,
        fallback_provider: StrongLlmProvider | None = None,
    ) -> RunResult:
        chain = [provider] if fallback_provider is None else [provider, fallback_provider]
        state = self._graph.invoke(
            {
                "request": request,
                "chain": ProviderChain(chain),
                "permit": permit,
                "execute_tool": execute_tool,
            }
        )
        return cast(RunResult, state["result"])

    @staticmethod
    def _route(state: AgentState) -> str:
        # Google grounding은 Vertex 경로의 기능이다. 1차가 그것을 못 하면 붙일 수 없다고 보고
        # 근거만으로 답하는 경로로 간다. 능력을 선언하지 않은 구현은 된다고 본다.
        supported = getattr(state["chain"].current, "supports_google_search", True)
        return "google" if state["request"].google_search_enabled and supported else "fallback"

    @staticmethod
    def _google(state: AgentState) -> dict[str, RunResult]:
        request = state["request"]
        chain = state["chain"]
        call_count = 0
        discovered = chain.attempt(
            state["permit"],
            "google_discovery",
            "GOOGLE_DISCOVERY",
            True,
            lambda provider: provider.invoke_google(request, include_owner=False),
        )
        call_count += 1
        result = discovered
        has_grounding = bool(discovered["grounding_roots"] and discovered["grounding_supports"])
        if not request.grounding_discovery_only and (
            request.owner_evidence or request.public_evidence or has_grounding
        ):
            state["permit"]("grounded_final", "GROUNDED_FINAL", False)
            result = chain.current.invoke_google(request, include_owner=True)
            result["prompt_tokens"] += discovered["prompt_tokens"]
            result["output_tokens"] += discovered["output_tokens"]
            call_count += 1
        return {
            "result": _run_result(
                result,
                vertex_calls=call_count,
                mode=request.mode,
                provider_id=chain.provider_id,
                backend=(
                    "VERTEX_GOOGLE"
                    if discovered.get("google_query_count", len(discovered["google_queries"])) > 0
                    else "NONE"
                ),
                grounding_source=discovered,
            )
        }

    @staticmethod
    def _fallback(state: AgentState) -> dict[str, RunResult]:
        request = state["request"]
        chain = state["chain"]
        if request.owner_evidence:
            # Owner-private evidence may be sent only to a tool-free final turn.  Supplying it
            # to a model with public-search tools attached would let the model derive a public
            # query from private text even if the host later rejected the tool response.
            turn = chain.attempt(
                state["permit"],
                "owner_final",
                "OWNER_FINAL",
                False,
                lambda provider: provider.invoke_fallback(request, [], tools_enabled=False),
            )
            if chain.current.tool_calls(turn["message"]):
                raise ValueError("STRONG_LLM_OWNER_PUBLIC_DISCOVERY_FORBIDDEN")
            answer_model(request.mode).model_validate_json(turn["answer_json"])
            return {
                "result": _run_result(
                    turn,
                    vertex_calls=1,
                    backend="NONE",
                    mode=request.mode,
                    provider_id=chain.provider_id,
                )
            }
        messages: list[BaseMessage] = []
        prompt_tokens = 0
        output_tokens = 0
        for round_index in range(request.max_tool_rounds + 1):
            tools_enabled = round_index < request.max_tool_rounds
            call_id = f"fallback_{round_index + 1}"
            history = messages
            enabled = tools_enabled

            def invoke_turn(provider: StrongLlmProvider) -> ProviderResult:
                return provider.invoke_fallback(request, history, tools_enabled=enabled)

            turn = chain.attempt(
                state["permit"],
                call_id,
                "SEARXNG_TOOL" if tools_enabled else "FINAL",
                False,
                invoke_turn,
            )
            vertex_calls = round_index + 1
            prompt_tokens += turn["prompt_tokens"]
            output_tokens += turn["output_tokens"]
            calls = chain.current.tool_calls(turn["message"])
            if not calls:
                answer_model(request.mode).model_validate_json(turn["answer_json"])
                turn["prompt_tokens"] = prompt_tokens
                turn["output_tokens"] = output_tokens
                return {
                    "result": _run_result(
                        turn,
                        vertex_calls=vertex_calls,
                        backend="SEARXNG",
                        mode=request.mode,
                        provider_id=chain.provider_id,
                    )
                }
            if not tools_enabled or len(calls) != 1:
                raise ValueError("STRONG_LLM_TOOL_ROUND_INVALID")
            call = calls[0]
            name = str(call.get("name", ""))
            arguments = call.get("args")
            if name not in {"capstone_web_search", "capstone_web_read"} or not isinstance(
                arguments, dict
            ):
                raise ValueError("STRONG_LLM_TOOL_CALL_INVALID")
            result_json = state["execute_tool"](str(call.get("id", "")), name, arguments)
            messages = chain.current.append_tool_result(
                messages, turn["message"], call, result_json
            )
        raise ValueError("STRONG_LLM_TOOL_BUDGET_EXHAUSTED")


def _run_result(
    result: ProviderResult,
    *,
    vertex_calls: int,
    backend: str,
    mode: str = "EXPLAIN",
    provider_id: str = "",
    grounding_source: ProviderResult | None = None,
) -> RunResult:
    from app.strong_llm.models import GroundingRoot, GroundingSupport

    answer_model(mode).model_validate_json(result["answer_json"])
    source = grounding_source or result
    roots = tuple(
        GroundingRoot(
            result_id=str(item["result_id"]),
            title=str(item["title"]),
            uri=str(item["uri"]),
            domain=str(item["domain"]),
            chunk_index=int(cast(int, item["chunk_index"])),
            citation_id=str(item["citation_id"]),
        )
        for item in source["grounding_roots"]
        if str(item.get("citation_id", ""))
    )
    supports = tuple(
        GroundingSupport(
            start_index=int(cast(int, item["start_index"])),
            end_index=int(cast(int, item["end_index"])),
            text=str(item["text"]),
            chunk_indices=tuple(cast(tuple[int, ...], item["chunk_indices"])),
        )
        for item in source["grounding_supports"]
    )
    return RunResult(
        answer_json=json.dumps(
            json.loads(result["answer_json"]), ensure_ascii=False, separators=(",", ":")
        ),
        prompt_token_count=result["prompt_tokens"],
        output_token_count=result["output_tokens"],
        vertex_generate_call_count=vertex_calls,
        google_grounding_query_count=source.get(
            "google_query_count", len(source["google_queries"])
        ),
        search_backend=backend,
        provider_id=provider_id,
        evidence_validation_mode="GOOGLE_GROUNDING" if roots else "CANONICAL_EXACT",
        grounding_roots=roots,
        grounding_supports=supports,
        web_search_queries=tuple(source["google_queries"]),
    )
