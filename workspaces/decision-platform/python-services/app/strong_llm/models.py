from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    citationId: str = Field(pattern=r"^cit_[1-5]$")
    quote: str = Field(min_length=1, max_length=2048)


class NumericSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    value: str = Field(min_length=1, max_length=64)
    citationIds: list[str] = Field(max_length=5)


class AnswerSentence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(min_length=1, max_length=2048)
    citationIds: list[str] = Field(max_length=5)
    evidenceSpans: list[EvidenceSpan] = Field(max_length=12)
    numericSpans: list[NumericSpan] = Field(max_length=64)


class StrongLlmAnswer(BaseModel):
    """Kotlin validator가 다시 검증하는 provider-neutral structured output이다."""

    model_config = ConfigDict(extra="forbid", strict=True)

    basis: Literal["EVIDENCE", "MODEL_KNOWLEDGE", "INSUFFICIENT_EVIDENCE"]
    answer: str | None = Field(default=None, max_length=8192)
    sentences: list[AnswerSentence] = Field(max_length=24)
    warnings: list[
        Literal[
            "SINGLE_SOURCE",
            "STALE_SOURCE",
            "CONFLICTING_SOURCES",
            "LOW_RELEVANCE",
            "SECONDARY_SOURCE",
            "GOOGLE_GROUNDING_ONLY",
        ]
    ] = Field(max_length=6)


@dataclass(frozen=True, slots=True)
class Evidence:
    ordinal: int
    citation_id: str
    chunk_revision_id: str
    canonical_text: str
    canonical_text_sha256: str
    owner_private: bool = False


@dataclass(frozen=True, slots=True)
class RunRequest:
    run_id: str
    model_id: str
    question: str
    answer_mode: str
    related_symbols: tuple[str, ...]
    topics: tuple[str, ...]
    public_evidence: tuple[Evidence, ...]
    owner_evidence: tuple[Evidence, ...]
    google_search_enabled: bool
    max_tool_rounds: int
    current_time: str
    timezone: str


@dataclass(frozen=True, slots=True)
class GroundingRoot:
    result_id: str
    title: str
    uri: str
    domain: str
    chunk_index: int
    citation_id: str


@dataclass(frozen=True, slots=True)
class GroundingSupport:
    start_index: int
    end_index: int
    text: str
    chunk_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RunResult:
    answer_json: str
    prompt_token_count: int
    output_token_count: int
    vertex_generate_call_count: int
    google_grounding_query_count: int
    search_backend: str
    evidence_validation_mode: str
    grounding_roots: tuple[GroundingRoot, ...] = ()
    grounding_supports: tuple[GroundingSupport, ...] = ()
    web_search_queries: tuple[str, ...] = ()
