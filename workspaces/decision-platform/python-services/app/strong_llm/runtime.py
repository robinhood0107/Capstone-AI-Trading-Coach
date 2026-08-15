from __future__ import annotations

import json
from collections.abc import Callable
from typing import NotRequired, Protocol, TypedDict, cast

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import END, START, StateGraph

from app.strong_llm.models import RunRequest, RunResult, StrongLlmAnswer


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


class AgentState(TypedDict):
    request: RunRequest
    provider: StrongLlmProvider
    permit: Permit
    execute_tool: ToolExecutor
    result: NotRequired[RunResult]


class BoundedStrongLlmGraph:
    """LangGraph는 provider 대화 상태만 소유하며 retry·DB·network 정책은 소유하지 않는다."""

    def __init__(self) -> None:
        graph = StateGraph(AgentState)
        graph.add_node("google", self._google)
        graph.add_node("fallback", self._fallback)
        graph.add_conditional_edges(START, self._route, {"google": "google", "fallback": "fallback"})
        graph.add_edge("google", END)
        graph.add_edge("fallback", END)
        self._graph = graph.compile()

    def run(
        self,
        request: RunRequest,
        provider: StrongLlmProvider,
        permit: Permit,
        execute_tool: ToolExecutor,
    ) -> RunResult:
        state = self._graph.invoke(
            {
                "request": request,
                "provider": provider,
                "permit": permit,
                "execute_tool": execute_tool,
            }
        )
        return cast(RunResult, state["result"])

    @staticmethod
    def _route(state: AgentState) -> str:
        return "google" if state["request"].google_search_enabled else "fallback"

    @staticmethod
    def _google(state: AgentState) -> dict[str, RunResult]:
        request = state["request"]
        provider = state["provider"]
        call_count = 0
        state["permit"]("google_discovery", "GOOGLE_DISCOVERY", True)
        discovered = provider.invoke_google(request, include_owner=False)
        call_count += 1
        result = discovered
        if request.owner_evidence:
            state["permit"]("owner_final", "OWNER_FINAL", False)
            result = provider.invoke_google(request, include_owner=True)
            call_count += 1
        return {
            "result": _run_result(
                result,
                vertex_calls=call_count,
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
        provider = state["provider"]
        messages: list[BaseMessage] = []
        prompt_tokens = 0
        output_tokens = 0
        vertex_calls = 0
        for round_index in range(request.max_tool_rounds + 1):
            tools_enabled = round_index < request.max_tool_rounds
            call_id = f"fallback_{round_index + 1}"
            state["permit"](call_id, "SEARXNG_TOOL" if tools_enabled else "FINAL", False)
            turn = provider.invoke_fallback(request, messages, tools_enabled=tools_enabled)
            vertex_calls += 1
            prompt_tokens += turn["prompt_tokens"]
            output_tokens += turn["output_tokens"]
            calls = provider.tool_calls(turn["message"])
            if not calls:
                StrongLlmAnswer.model_validate_json(turn["answer_json"])
                turn["prompt_tokens"] = prompt_tokens
                turn["output_tokens"] = output_tokens
                return {"result": _run_result(turn, vertex_calls=vertex_calls, backend="SEARXNG")}
            if not tools_enabled or len(calls) != 1:
                raise ValueError("STRONG_LLM_TOOL_ROUND_INVALID")
            call = calls[0]
            name = str(call.get("name", ""))
            arguments = call.get("args")
            if name not in {"capstone_web_search", "capstone_web_read"} or not isinstance(arguments, dict):
                raise ValueError("STRONG_LLM_TOOL_CALL_INVALID")
            result_json = state["execute_tool"](str(call.get("id", "")), name, arguments)
            messages = provider.append_tool_result(messages, turn["message"], call, result_json)
        raise ValueError("STRONG_LLM_TOOL_BUDGET_EXHAUSTED")


def _run_result(
    result: ProviderResult,
    *,
    vertex_calls: int,
    backend: str,
    grounding_source: ProviderResult | None = None,
) -> RunResult:
    from app.strong_llm.models import GroundingRoot, GroundingSupport

    StrongLlmAnswer.model_validate_json(result["answer_json"])
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
        answer_json=json.dumps(json.loads(result["answer_json"]), ensure_ascii=False, separators=(",", ":")),
        prompt_token_count=result["prompt_tokens"],
        output_token_count=result["output_tokens"],
        vertex_generate_call_count=vertex_calls,
        google_grounding_query_count=source.get("google_query_count", len(source["google_queries"])),
        search_backend=backend,
        evidence_validation_mode="GOOGLE_GROUNDING" if roots else "CANONICAL_EXACT",
        grounding_roots=roots,
        grounding_supports=supports,
        web_search_queries=tuple(source["google_queries"]),
    )
