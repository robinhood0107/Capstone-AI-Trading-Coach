from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from app.strong_llm.models import Evidence, RunRequest
from app.strong_llm.runtime import BoundedStrongLlmGraph, ProviderResult
from app.strong_llm.vertex_provider import (
    LangChainVertexProvider,
    VertexProviderSettings,
    _normalize_grounded_answer,
    _provider_result,
    _vertex_response_schema,
)


def _answer() -> str:
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


class FakeProvider:
    def __init__(self, *, google_queries: list[str] | None = None, tool_call: bool = False) -> None:
        self.google_queries = google_queries or []
        self.tool_call = tool_call
        self.invocations: list[tuple[str, bool]] = []
        self.tool_results: list[str] = []

    def invoke_google(self, request: RunRequest, *, include_owner: bool) -> ProviderResult:
        self.invocations.append(("google", include_owner))
        return _result(self.google_queries)

    def invoke_fallback(
        self,
        request: RunRequest,
        messages: list[BaseMessage],
        *,
        tools_enabled: bool,
    ) -> ProviderResult:
        self.invocations.append(("fallback", tools_enabled))
        if self.tool_call and not messages:
            message = AIMessage(
                content="",
                tool_calls=[{"name": "capstone_web_search", "args": {"query": "diversification"}, "id": "call_1"}],
            )
            result = _result([])
            result["message"] = message
            result["answer_json"] = ""
            return result
        return _result([])

    def tool_calls(self, message: AIMessage) -> list[dict[str, object]]:
        return [dict(item) for item in message.tool_calls]

    def append_tool_result(
        self,
        messages: list[BaseMessage],
        message: AIMessage,
        call: dict[str, object],
        result_json: str,
    ) -> list[BaseMessage]:
        self.tool_results.append(result_json)
        return [message, AIMessage(content=result_json)]


def test_google_path_requires_permit_before_provider_and_preserves_grounding() -> None:
    provider = FakeProvider(google_queries=["portfolio diversification"])
    events: list[tuple[str, str, bool]] = []
    result = BoundedStrongLlmGraph().run(
        _request(google=True),
        provider,
        lambda call_id, phase, attached: events.append((call_id, phase, attached)),
        lambda *_: pytest.fail("Google native path must not call host tools"),
    )

    assert events == [("google_discovery", "GOOGLE_DISCOVERY", True)]
    assert provider.invocations == [("google", False)]
    assert result.vertex_generate_call_count == 1
    assert result.google_grounding_query_count == 1
    assert result.search_backend == "VERTEX_GOOGLE"


def test_owner_google_path_has_discovery_then_tool_free_final() -> None:
    provider = FakeProvider(google_queries=["portfolio diversification"])
    permits: list[tuple[str, bool]] = []
    result = BoundedStrongLlmGraph().run(
        _request(google=True, owner=True),
        provider,
        lambda call_id, _phase, attached: permits.append((call_id, attached)),
        lambda *_: pytest.fail("Google native path must not call host tools"),
    )

    assert permits == [("google_discovery", True), ("owner_final", False)]
    assert provider.invocations == [("google", False), ("google", True)]
    assert result.vertex_generate_call_count == 2


def test_searxng_fallback_is_bounded_and_returns_tool_result_to_same_message() -> None:
    provider = FakeProvider(tool_call=True)
    calls: list[tuple[str, str, dict[str, object]]] = []
    result = BoundedStrongLlmGraph().run(
        _request(google=False),
        provider,
        lambda *_: None,
        lambda call_id, name, args: calls.append((call_id, name, args)) or '{"results":[]}',
    )

    assert calls == [("call_1", "capstone_web_search", {"query": "diversification"})]
    assert provider.tool_results == ['{"results":[]}']
    assert result.vertex_generate_call_count == 2
    assert result.search_backend == "SEARXNG"


def test_explicit_service_account_acl_rejects_non_0600(tmp_path: Path) -> None:
    credential = tmp_path / "service-account.json"
    credential.write_text("{}", encoding="utf-8")
    credential.chmod(0o644)

    with pytest.raises(ValueError, match="STRONG_LLM_CREDENTIAL_MODE_INVALID"):
        VertexProviderSettings(service_account_path=credential)


def test_google_search_and_native_schema_share_the_official_bind_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = tmp_path / "service-account.json"
    credential.write_text("{}", encoding="utf-8")
    credential.chmod(0o600)
    constructor_kwargs: dict[str, object] = {}
    bind_calls: list[dict[str, object]] = []

    class FakeCredentials:
        project_id = "project-id"

    class FakeModel:
        def __init__(self, **kwargs: object) -> None:
            constructor_kwargs.update(kwargs)

        def bind(self, **kwargs: object) -> "FakeModel":
            bind_calls.append(kwargs)
            return self

    monkeypatch.setattr(
        "app.strong_llm.vertex_provider.service_account.Credentials.from_service_account_file",
        lambda *_args, **_kwargs: FakeCredentials(),
    )
    monkeypatch.setattr("app.strong_llm.vertex_provider.ChatGoogleGenerativeAI", FakeModel)

    LangChainVertexProvider(
        _request(google=True),
        VertexProviderSettings(service_account_path=credential),
    )

    assert "response_mime_type" not in constructor_kwargs
    assert "response_schema" not in constructor_kwargs
    assert bind_calls[0]["response_mime_type"] == "application/json"
    assert "tools" not in bind_calls[0]
    assert bind_calls[1]["tools"] == [{"google_search": {}}]
    assert bind_calls[1]["response_mime_type"] == "application/json"
    assert bind_calls[0]["response_schema"] == bind_calls[1]["response_schema"]


def test_vertex_schema_uses_only_the_provider_supported_structural_subset() -> None:
    schema = _vertex_response_schema()
    serialized = json.dumps(schema, sort_keys=True)

    for unsupported in ("$defs", "$ref", "additionalProperties", "minLength", "maxLength", "pattern"):
        assert unsupported not in serialized
    assert schema["required"] == ["basis", "answer", "sentences", "warnings"]


def test_grounding_metadata_accepts_null_optional_segment_offsets() -> None:
    message = AIMessage(
        content=_answer(),
        response_metadata={
            "grounding_metadata": {
                "web_search_queries": ["portfolio diversification"],
                "grounding_chunks": [
                    {
                        "web": {
                            "uri": "https://www.investor.gov/diversification",
                            "title": "Diversification",
                            "domain": "investor.gov",
                        }
                    }
                ],
                "grounding_supports": [
                    {
                        "segment": {"start_index": None, "end_index": None, "text": "분산투자"},
                        "grounding_chunk_indices": [0],
                    }
                ],
            }
        },
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )

    result = _provider_result(message)

    assert result["grounding_supports"] == [
        {"start_index": 0, "end_index": 0, "text": "분산투자", "chunk_indices": (0,)}
    ]


def test_google_support_uses_an_unused_citation_id_without_discarding_local_evidence() -> None:
    answer = {
        "basis": "EVIDENCE",
        "answer": "낮은 상관관계는 위험 집중을 줄일 수 있습니다.",
        "sentences": [
            {
                "text": "낮은 상관관계는 위험 집중을 줄일 수 있습니다.",
                "citationIds": ["cit_1"],
                "evidenceSpans": [{"citationId": "cit_1", "quote": "위험 집중"}],
                "numericSpans": [],
            }
        ],
        "warnings": [],
    }
    roots: list[dict[str, object]] = [
        {
            "result_id": "google_1",
            "title": "Investor.gov",
            "uri": "https://www.investor.gov/introduction-investing/investing-basics/glossary/diversification",
            "domain": "investor.gov",
            "chunk_index": 0,
            "citation_id": "",
        }
    ]
    supports: list[dict[str, object]] = [
        {
            "start_index": 0,
            "end_index": 12,
            "text": "낮은 상관관계",
            "chunk_indices": (0,),
        }
    ]

    normalized = json.loads(_normalize_grounded_answer(json.dumps(answer, ensure_ascii=False), roots, supports))

    assert roots[0]["citation_id"] == "cit_2"
    assert normalized["sentences"][0]["citationIds"] == ["cit_1", "cit_2"]
    assert normalized["sentences"][0]["evidenceSpans"] == [
        {"citationId": "cit_1", "quote": "위험 집중"},
        {"citationId": "cit_2", "quote": "낮은 상관관계"},
    ]
    assert normalized["warnings"] == ["GOOGLE_GROUNDING_ONLY"]


def test_owner_final_accepts_exact_intermediate_support_quote_without_copying_it_into_sentence() -> None:
    answer = {
        "basis": "EVIDENCE",
        "answer": "분산투자는 자산 움직임의 차이를 이용해 전체 위험을 낮출 수 있습니다.",
        "sentences": [
            {
                "text": "분산투자는 자산 움직임의 차이를 이용해 전체 위험을 낮출 수 있습니다.",
                "citationIds": ["cit_2"],
                "evidenceSpans": [{"citationId": "cit_2", "quote": "Diversification can reduce risk."}],
                "numericSpans": [],
            }
        ],
        "warnings": [],
    }
    roots: list[dict[str, object]] = [
        {
            "result_id": "google_1",
            "title": "Investor.gov",
            "uri": "https://www.investor.gov/diversification",
            "domain": "investor.gov",
            "chunk_index": 0,
            "citation_id": "cit_2",
        }
    ]
    supports: list[dict[str, object]] = [
        {
            "start_index": 0,
            "end_index": 32,
            "text": "Diversification can reduce risk.",
            "chunk_indices": (0,),
        }
    ]

    normalized = json.loads(_normalize_grounded_answer(json.dumps(answer, ensure_ascii=False), roots, supports))

    assert normalized["basis"] == "EVIDENCE"
    assert normalized["warnings"] == ["GOOGLE_GROUNDING_ONLY"]
    assert normalized["sentences"][0]["citationIds"] == ["cit_2"]


def _request(*, google: bool, owner: bool = False) -> RunRequest:
    public = Evidence(1, "cit_1", "rag_v2_chk_" + "a" * 32, "public evidence", "a" * 64)
    private = Evidence(2, "cit_2", "rag_v2_chk_" + "b" * 32, "private evidence", "b" * 64, True)
    return RunRequest(
        run_id="s49_run_" + "1" * 32,
        model_id="gemini-3.5-flash",
        question="분산투자를 설명해 주세요.",
        answer_mode="DETAILED",
        related_symbols=(),
        topics=("RISK",),
        public_evidence=(public,),
        owner_evidence=(private,) if owner else (),
        google_search_enabled=google,
        max_tool_rounds=3,
        current_time="2026-08-15T00:00:00Z",
        timezone="Asia/Seoul",
    )


def _result(queries: list[str]) -> ProviderResult:
    return cast(
        ProviderResult,
        {
            "message": AIMessage(content=_answer()),
            "answer_json": _answer(),
            "prompt_tokens": 10,
            "output_tokens": 5,
            "google_queries": queries,
            "grounding_roots": [],
            "grounding_supports": [],
        },
    )
