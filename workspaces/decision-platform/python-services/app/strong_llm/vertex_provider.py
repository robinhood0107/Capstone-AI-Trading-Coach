from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any, cast

from google.oauth2 import service_account
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.strong_llm.models import RunRequest, StrongLlmAnswer
from app.strong_llm.prompt import render_discovery_prompt, render_prompt
from app.strong_llm.runtime import ProviderResult


_VERTEX_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class VertexProviderSettings:
    """서비스계정 JSON은 explicit 0600 regular file만 허용하고 API key·ADC fallback을 만들지 않는다."""

    def __init__(
        self,
        *,
        service_account_path: Path,
        location: str = "global",
        timeout_seconds: float = 50.0,
    ) -> None:
        path = service_account_path
        info = path.lstat()
        if not path.is_absolute() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("STRONG_LLM_CREDENTIAL_FILE_INVALID")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("STRONG_LLM_CREDENTIAL_MODE_INVALID")
        if location != "global":
            raise ValueError("STRONG_LLM_VERTEX_LOCATION_INVALID")
        if not 10.0 <= timeout_seconds <= 55.0:
            raise ValueError("STRONG_LLM_VERTEX_TIMEOUT_INVALID")
        self.service_account_path = path
        self.location = location
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "VertexProviderSettings":
        if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
            raise ValueError("STRONG_LLM_API_KEY_FALLBACK_FORBIDDEN")
        path = Path(os.environ.get("STRONG_LLM_VERTEX_SERVICE_ACCOUNT_JSON", ""))
        raw_timeout = os.environ.get("STRONG_LLM_VERTEX_TIMEOUT_SECONDS", "50")
        try:
            timeout_seconds = float(raw_timeout)
        except ValueError as error:
            raise ValueError("STRONG_LLM_VERTEX_TIMEOUT_INVALID") from error
        return cls(service_account_path=path, timeout_seconds=timeout_seconds)


class LangChainVertexProvider:
    """LangChain은 provider message와 native schema를 관리하며 permit·budget은 host가 강제한다."""

    def __init__(self, request: RunRequest, settings: VertexProviderSettings) -> None:
        credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
            str(settings.service_account_path), scopes=[_VERTEX_SCOPE]
        )
        project = credentials.project_id
        if not project:
            raise ValueError("STRONG_LLM_VERTEX_PROJECT_INVALID")
        common: dict[str, Any] = {
            "model": request.model_id,
            "vertexai": True,
            "project": project,
            "location": settings.location,
            "credentials": credentials,
            "max_retries": 0,
            # Google grounding은 검색 왕복을 포함하므로 host 60초 deadline 안에서 최대 55초만 기다린다.
            "timeout": settings.timeout_seconds,
            "max_output_tokens": 4096,
            "temperature": None,
        }
        base_model = ChatGoogleGenerativeAI(**common)
        structured = {
            "response_mime_type": "application/json",
            "response_schema": _vertex_response_schema(),
        }
        self._structured = base_model.bind(**structured)
        # Google Search와 native JSON schema의 단일 호출 결합은 LangChain 공식 bind 계약을 따른다.
        self._google = base_model.bind(tools=[{"google_search": {}}], **structured)
        self._tool_model = base_model
        self._request = request
        self._discovery: ProviderResult | None = None

    def invoke_google(self, request: RunRequest, *, include_owner: bool) -> ProviderResult:
        if include_owner:
            if not request.owner_evidence or self._discovery is None:
                raise ValueError("STRONG_LLM_OWNER_FINAL_STATE_INVALID")
            prompt = render_prompt(request, request.public_evidence + request.owner_evidence)
            _assign_grounding_citation_ids(
                self._discovery["grounding_roots"],
                {item.citation_id for item in request.public_evidence + request.owner_evidence},
            )
            grounding = _grounding_evidence(self._discovery)
            messages: list[BaseMessage] = [
                SystemMessage(content=prompt.system),
                HumanMessage(
                    content=prompt.user
                    + "\n\nVerified Google grounding support:\n"
                    + grounding
                    + "\nWhen using Google support, cite its assigned citation_id and copy the exact support text into evidenceSpans.quote.",
                ),
            ]
            message = self._structured.invoke(messages)
            result = _provider_result(message)
            result["answer_json"] = _normalize_grounded_answer(
                result["answer_json"],
                self._discovery["grounding_roots"],
                self._discovery["grounding_supports"],
            )
            return result

        prompt = (
            render_discovery_prompt(request)
            if request.owner_evidence
            else render_prompt(request, request.public_evidence)
        )
        message = self._google.invoke(
            [SystemMessage(content=prompt.system), HumanMessage(content=prompt.user)]
        )
        result = _provider_result(message)
        if request.owner_evidence:
            self._discovery = result
        return result

    def invoke_fallback(
        self,
        request: RunRequest,
        messages: list[BaseMessage],
        *,
        tools_enabled: bool,
    ) -> ProviderResult:
        prompt = render_prompt(request, request.public_evidence + request.owner_evidence)
        history = messages or [SystemMessage(content=prompt.system), HumanMessage(content=prompt.user)]
        runnable = self._tool_model.bind_tools(_fallback_tools()) if tools_enabled else self._structured
        return _provider_result(runnable.invoke(history))

    def tool_calls(self, message: AIMessage) -> list[dict[str, object]]:
        return [dict(call) for call in message.tool_calls]

    def append_tool_result(
        self,
        messages: list[BaseMessage],
        message: AIMessage,
        call: dict[str, object],
        result_json: str,
    ) -> list[BaseMessage]:
        call_id = str(call.get("id", ""))
        if not call_id:
            raise ValueError("STRONG_LLM_TOOL_CALL_ID_INVALID")
        return [*messages, message, ToolMessage(content=result_json, tool_call_id=call_id)]


def _vertex_response_schema() -> dict[str, object]:
    """Vertex 지원 subset만 보내고 길이·pattern은 Pydantic과 Kotlin에서 재검증한다."""

    citation_ids: dict[str, object] = {"type": "array", "items": {"type": "string"}}
    evidence_span: dict[str, object] = {
        "type": "object",
        "properties": {"citationId": {"type": "string"}, "quote": {"type": "string"}},
        "required": ["citationId", "quote"],
    }
    numeric_span: dict[str, object] = {
        "type": "object",
        "properties": {"value": {"type": "string"}, "citationIds": citation_ids},
        "required": ["value", "citationIds"],
    }
    sentence: dict[str, object] = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "citationIds": citation_ids,
            "evidenceSpans": {"type": "array", "items": evidence_span},
            "numericSpans": {"type": "array", "items": numeric_span},
        },
        "required": ["text", "citationIds", "evidenceSpans", "numericSpans"],
    }
    return {
        "type": "object",
        "properties": {
            "basis": {
                "type": "string",
                "enum": ["EVIDENCE", "MODEL_KNOWLEDGE", "INSUFFICIENT_EVIDENCE"],
            },
            "answer": {"type": "string", "nullable": True},
            "sentences": {"type": "array", "items": sentence},
            "warnings": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "SINGLE_SOURCE",
                        "STALE_SOURCE",
                        "CONFLICTING_SOURCES",
                        "LOW_RELEVANCE",
                        "SECONDARY_SOURCE",
                        "GOOGLE_GROUNDING_ONLY",
                    ],
                },
            },
        },
        "required": ["basis", "answer", "sentences", "warnings"],
    }


def _provider_result(message: AIMessage) -> ProviderResult:
    text = _canonical_answer_json(_message_text(message))
    grounding = message.response_metadata.get("grounding_metadata") or {}
    if not isinstance(grounding, dict):
        grounding = {}
    roots = []
    for index, chunk in enumerate(grounding.get("grounding_chunks") or []):
        if not isinstance(chunk, dict) or not isinstance(chunk.get("web"), dict):
            continue
        web = chunk["web"]
        uri = str(web.get("uri", ""))
        title = str(web.get("title", ""))
        domain = str(web.get("domain", ""))
        if not uri.startswith("https://") or not title:
            continue
        roots.append(
            {
                "result_id": f"google_{index + 1}",
                "title": title[:500],
                "uri": uri[:2048],
                "domain": domain[:253],
                "chunk_index": index,
                "citation_id": "",
            }
        )
    supports = []
    for support in grounding.get("grounding_supports") or []:
        if not isinstance(support, dict) or not isinstance(support.get("segment"), dict):
            continue
        segment = support["segment"]
        support_text = str(segment.get("text", ""))
        indices = support.get("grounding_chunk_indices") or []
        if support_text and isinstance(indices, list) and all(isinstance(value, int) for value in indices):
            supports.append(
                {
                    "start_index": _metadata_index(segment.get("start_index")),
                    "end_index": _metadata_index(segment.get("end_index")),
                    "text": support_text[:2048],
                    "chunk_indices": tuple(indices),
                }
            )
    usage: dict[str, Any] = dict(message.usage_metadata or {})
    normalized = _normalize_grounded_answer(text, roots, supports)
    return {
        "message": message,
        "answer_json": normalized,
        "prompt_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
        "google_queries": [str(value) for value in grounding.get("web_search_queries") or []],
        "grounding_roots": roots,
        "grounding_supports": supports,
    }


def _metadata_index(value: object) -> int:
    # Google grounding의 optional offset은 SDK에 따라 null일 수 있으며 support text 결속에는 필수가 아니다.
    return value if isinstance(value, int) and value >= 0 else 0


def _message_text(message: AIMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    parts = []
    for part in message.content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type") == "text":
            parts.append(str(part.get("text", "")))
    return "".join(parts)


def _canonical_answer_json(value: str) -> str:
    """Native schema 본문만 canonicalize하고 설명문·복수 JSON·비객체 root는 허용하지 않는다."""

    candidate = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", candidate, flags=re.IGNORECASE | re.DOTALL)
    if fenced is not None:
        candidate = fenced.group(1).strip()
    if not candidate:
        raise ValueError("STRONG_LLM_PROVIDER_TEXT_MISSING")
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise ValueError("STRONG_LLM_PROVIDER_JSON_INVALID") from error
    if not isinstance(payload, dict):
        raise ValueError("STRONG_LLM_PROVIDER_JSON_ROOT_INVALID")
    StrongLlmAnswer.model_validate(payload)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _grounding_evidence(result: ProviderResult) -> str:
    payload = {
        "queries": result["google_queries"],
        "sources": result["grounding_roots"],
        "supports": result["grounding_supports"],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _normalize_grounded_answer(
    answer_json: str,
    roots: list[dict[str, object]],
    supports: list[dict[str, object]],
) -> str:
    """Provider support와 실제 문장 구간이 겹치는 Google 근거만 남는 citation ID에 결속한다."""

    if not roots or not supports:
        return answer_json
    answer = StrongLlmAnswer.model_validate_json(answer_json)
    used_local_ids = list(
        dict.fromkeys(citation_id for sentence in answer.sentences for citation_id in sentence.citationIds)
    )
    valid_ids = {f"cit_{index}" for index in range(1, 6)}
    preassigned = {
        _chunk_index(root): str(root.get("citation_id", ""))
        for root in roots
        if str(root.get("citation_id", "")) in valid_ids
    }
    unavailable = set(used_local_ids) | set(preassigned.values())
    available_ids = [f"cit_{index}" for index in range(1, 6) if f"cit_{index}" not in unavailable]
    supported_root_indices = list(
        dict.fromkeys(
            int(index)
            for support in supports
            for index in _chunk_indices(support)
            if any(_chunk_index(root) == int(index) for root in roots)
        )
    )
    root_by_index: dict[int, str] = dict(preassigned)
    unassigned_indices = [index for index in supported_root_indices if index not in root_by_index]
    for chunk_index, citation_id in zip(unassigned_indices, available_ids, strict=False):
        root = next(item for item in roots if _chunk_index(item) == chunk_index)
        root["citation_id"] = citation_id
        root_by_index[chunk_index] = citation_id
    if not root_by_index:
        return answer_json
    normalized_sentences = []
    used_google = False
    for sentence in answer.sentences:
        supporting = [
            support
            for support in supports
            if str(support["text"]).strip()
            and str(support["text"]).strip() in sentence.text
            and any(int(index) in root_by_index for index in _chunk_indices(support))
        ]
        spans = [span.model_dump() for span in sentence.evidenceSpans]
        selected_ids = list(sentence.citationIds)
        for support in supports:
            for index in _chunk_indices(support):
                mapped_id = root_by_index.get(int(index))
                if mapped_id is None:
                    continue
                if any(
                    span["citationId"] == mapped_id and span["quote"] == str(support["text"])
                    for span in spans
                ):
                    used_google = True
                    if mapped_id not in selected_ids:
                        selected_ids.append(mapped_id)
        for support in supporting:
            for index in _chunk_indices(support):
                mapped_id = root_by_index.get(int(index))
                if mapped_id is None:
                    continue
                if mapped_id not in selected_ids:
                    selected_ids.append(mapped_id)
                spans.append({"citationId": mapped_id, "quote": str(support["text"])})
                used_google = True
        spans = list({(span["citationId"], span["quote"]): span for span in spans}.values())
        numeric = [
            {
                "value": item.value,
                "citationIds": list(
                    dict.fromkeys(
                        [
                            *item.citationIds,
                            *[
                                span["citationId"]
                                for span in spans
                                if item.value in span["quote"]
                            ],
                        ]
                    )
                ),
            }
            for item in sentence.numericSpans
        ]
        normalized_sentences.append(
            {
                "text": sentence.text,
                "citationIds": selected_ids,
                "evidenceSpans": spans[:12],
                "numericSpans": numeric,
            }
        )
    payload = answer.model_dump()
    if used_google:
        payload["basis"] = "EVIDENCE"
    payload["sentences"] = normalized_sentences
    if used_google:
        payload["warnings"] = list(dict.fromkeys([*answer.warnings, "GOOGLE_GROUNDING_ONLY"]))
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _assign_grounding_citation_ids(
    roots: list[dict[str, object]],
    unavailable_ids: set[str],
) -> None:
    available = [f"cit_{index}" for index in range(1, 6) if f"cit_{index}" not in unavailable_ids]
    for root, citation_id in zip(roots, available, strict=False):
        root["citation_id"] = citation_id


def _chunk_indices(value: dict[str, object]) -> tuple[int, ...]:
    raw = value.get("chunk_indices")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(int(cast(int, item)) for item in raw)


def _chunk_index(value: dict[str, object]) -> int:
    return int(cast(int, value["chunk_index"]))


def _fallback_tools() -> list[dict[str, object]]:
    return [
        {
            "name": "capstone_web_search",
            "description": "Search the bounded public SearXNG index.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "capstone_web_read",
            "description": "Read a previously registered public resultId.",
            "parameters": {
                "type": "object",
                "properties": {"resultId": {"type": "string"}},
                "required": ["resultId"],
                "additionalProperties": False,
            },
        },
    ]
