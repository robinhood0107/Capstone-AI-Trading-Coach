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


class CandidateVerdict(BaseModel):
    """후보 하나에 대한 모델의 판단이다. 수량이나 주문은 여기에 없다.

    모델은 점수와 차단 여부, 그 사유만 낸다. 그 숫자로 순위를 바꿀지 얼마나 살지는
    `app/p1_owner/automation.py`가 결정론적으로 계산한다. 모델이 배분을 직접 내면 같은
    입력에 같은 결과라는 성질을 잃고 정책 상한을 검증으로만 막아야 한다.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    symbol: str = Field(pattern=r"^[0-9A-Z._:-]{1,20}$")
    score: float = Field(ge=0.0, le=1.0)
    veto: bool
    reason: str = Field(min_length=1, max_length=512)


class StrongLlmJudgement(BaseModel):
    """JUDGE 모드의 structured output. 주어진 후보 집합에 대해서만 답한다."""

    model_config = ConfigDict(extra="forbid", strict=True)

    candidates: list[CandidateVerdict] = Field(max_length=32)
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1, max_length=2048)


@dataclass(frozen=True, slots=True)
class JudgementCandidate:
    """모델에게 보여줄 후보 하나. 계좌·잔고·보유량은 들어가지 않는다."""

    symbol: str
    expected_return: float
    model_confidence: float
    lstm_signal: str
    baseline_signal: str


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
    # 프롬프트가 "Answer in the user's language"라고 말하면서 정작 그 값을 넘기지 않았다.
    # 모델이 질문 언어로 추측할 뿐이었고, 그래서 다국어 지원을 약속할 수 없었다.
    language: str = "ko"
    # EXPLAIN은 근거로 설명하고, JUDGE는 후보를 평가한다. 같은 템플릿이 이 값으로 갈린다.
    mode: str = "EXPLAIN"
    # JUDGE에서만 채운다. 후보 집합의 소유자는 Return Engine이고 모델은 이 안에서만 답한다.
    candidates: tuple[JudgementCandidate, ...] = ()


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
    # 실제로 답한 provider. 1차가 실패해 2차가 답한 run을 사후에 구분할 수 있어야
    # "AI가 참여했는가"와 "무엇이 참여했는가"를 함께 말할 수 있다.
    provider_id: str = ""
    grounding_roots: tuple[GroundingRoot, ...] = ()
    grounding_supports: tuple[GroundingSupport, ...] = ()
    web_search_queries: tuple[str, ...] = ()


# 같은 템플릿이 `mode`로 갈리듯 출력 계약도 `mode`로 갈린다. 검증하는 쪽이 EXPLAIN을 하드코딩하면
# JUDGE 응답이 스키마 위반으로 죽으므로, 모드에서 모델을 얻는 자리를 하나만 둔다.
_ANSWER_MODEL: dict[str, type[BaseModel]] = {
    "EXPLAIN": StrongLlmAnswer,
    "JUDGE": StrongLlmJudgement,
}


def answer_model(mode: str) -> type[BaseModel]:
    model = _ANSWER_MODEL.get(mode)
    if model is None:
        raise ValueError("STRONG_LLM_MODE_UNSUPPORTED")
    return model
