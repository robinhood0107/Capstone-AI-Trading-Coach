"""Real Vertex probe for the RAG explain path: every question must produce an answer.

시연 핵심 기능의 성질은 하나다 - 어떤 질문이 들어와도 설명이 나온다. 근거는 있으면 인용으로
붙고 없으면 붙지 않을 뿐, 답을 막는 조건이 아니다. 그 성질은 fixture로는 증명되지 않는다.
구조화 출력이 실제 provider에서 얼마나 안정적으로 오는지, 근거가 없을 때 모델이 정말
`MODEL_KNOWLEDGE`로 답하는지는 실제 호출만 말할 수 있다.

프로브는 계좌·잔고·보유·주문 의존이 없고 브로커리지를 건드리지 않는다. 질문 하나당 provider
호출은 최대 1회(Google 없음) 또는 2회(discovery + final)로 호출 이전에 상한이 걸려 있으며
재시도는 없다. 출력은 프로젝트 프롬프트가 낸 결과의 host 투영뿐이다.

실행:
    STRONG_LLM_VERTEX_SERVICE_ACCOUNT_JSON=<0600 절대경로> \
      uv run --frozen python -m tests.rehearsal.rag_explain_answer_probe

`--google` 을 주면 각 질문에 discovery + final 2단계를 태운다. 기본은 tool 없는 단일 호출이다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime

from app.strong_llm.models import Evidence, RunRequest, StrongLlmAnswer
from app.strong_llm.runtime import BoundedStrongLlmGraph
from app.strong_llm.vertex_provider import LangChainVertexProvider, VertexProviderSettings

# 근거가 있는 질문에 넣을 공개 문헌 한 조각. corpus를 띄우지 않고도 "근거가 있을 때"와
# "없을 때"를 같은 실행에서 비교하기 위해 프로브가 직접 들고 있는다.
_ROLLOVER_TEXT = (
    "선물 ETF는 만기가 있는 선물 계약을 보유하므로 만기 전에 다음 월물로 교체하는 롤오버를 "
    "수행한다. 원월물 가격이 근월물보다 높은 콘탱고에서는 교체할 때마다 더 비싼 계약을 사게 "
    "되어 롤오버 비용이 발생하고, 이 비용은 기초자산 가격이 그대로여도 ETF 수익률을 낮춘다."
)


def _evidence(text: str) -> Evidence:
    return Evidence(
        ordinal=1,
        citation_id="cit_1",
        chunk_revision_id="rag_v2_chk_" + "a" * 32,
        canonical_text=text,
        canonical_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        owner_private=False,
    )


# (라벨, 질문, 근거, 기대 성질)
_CASES: tuple[tuple[str, str, tuple[Evidence, ...], str], ...] = (
    (
        "GROUNDED",
        "금 선물 ETF의 롤오버 위험은 무엇인가요?",
        (_evidence(_ROLLOVER_TEXT),),
        "근거가 있으므로 인용이 붙어야 한다",
    ),
    (
        "UNGROUNDED_CONCEPT",
        "분산투자가 위험을 줄이는 이유를 설명해 주세요.",
        (),
        "근거가 없어도 설명은 나와야 한다",
    ),
    (
        "ORDINARY_VOCABULARY",
        "보유 종목의 주문 체결이라는 말은 무슨 뜻인가요?",
        (),
        "일반 금융 어휘가 거절 사유가 되지 않아야 한다",
    ),
    (
        "ADVICE_SHAPED",
        "지금 삼성전자를 사도 될까요?",
        (),
        "조언성 질문에도 개념과 위험 설명이 나와야 한다",
    ),
)


class _Permit:
    """호출 상한과 허용 phase를 호출 이전에 강제한다. 재시도는 없다."""

    def __init__(self, allowed_phases: frozenset[str], *, cap: int) -> None:
        self._allowed_phases = allowed_phases
        self._cap = cap
        self.calls = 0
        self.phases: list[str] = []

    def __call__(self, call_id: str, phase: str, google_attached: bool) -> None:
        del call_id, google_attached
        if self.calls >= self._cap or phase not in self._allowed_phases:
            raise RuntimeError(f"RAG_EXPLAIN_PROBE_CAP_EXCEEDED phase={phase} calls={self.calls}")
        self.calls += 1
        self.phases.append(phase)


def _no_tool(name: str, call_id: str, arguments: dict[str, object]) -> str:
    del name, call_id, arguments
    raise RuntimeError("RAG_EXPLAIN_PROBE_TOOL_FORBIDDEN")


def _request(
    *,
    run_suffix: str,
    model: str,
    question: str,
    evidence: tuple[Evidence, ...],
    google: bool,
) -> RunRequest:
    return RunRequest(
        run_id="s49_run_" + run_suffix * 32,
        model_id=model,
        question=question,
        answer_mode="DETAILED",
        related_symbols=(),
        topics=("FINANCIAL_ENGINEERING", "RISK", "METHODOLOGY", "PRODUCT_RISK"),
        public_evidence=evidence,
        owner_evidence=(),
        google_search_enabled=google,
        max_tool_rounds=0,
        current_time=datetime.now(UTC).isoformat(),
        timezone="Asia/Seoul",
        language="ko",
        mode="EXPLAIN",
        candidates=(),
        thinking_level="low",
        grounding_discovery_only=False,
    )


def _run_case(
    *,
    index: int,
    model: str,
    settings: VertexProviderSettings,
    question: str,
    evidence: tuple[Evidence, ...],
    google: bool,
) -> tuple[StrongLlmAnswer, int]:
    request = _request(
        run_suffix=str(index),
        model=model,
        question=question,
        evidence=evidence,
        google=google,
    )
    # tool 없는 경로는 FINAL 한 번, Google 경로는 discovery + grounded final 두 번이다.
    phases = frozenset({"GOOGLE_DISCOVERY", "GROUNDED_FINAL"} if google else {"FINAL"})
    permit = _Permit(phases, cap=2 if google else 1)
    result = BoundedStrongLlmGraph().run(
        request,
        LangChainVertexProvider(request, settings),
        permit,
        _no_tool,
    )
    return StrongLlmAnswer.model_validate_json(
        result.answer_json
    ), result.vertex_generate_call_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--google",
        action="store_true",
        help="각 질문에 Google discovery + final 2단계를 태운다.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="구조화 출력 안정성을 보기 위해 전체 집합을 이 횟수만큼 반복한다.",
    )
    args = parser.parse_args()
    if not 1 <= args.repeat <= 5:
        print("RAG_EXPLAIN_PROBE_REPEAT_INVALID", file=sys.stderr)
        return 2

    model = (
        os.environ.get("VERTEX_MODEL_ID")
        or os.environ.get("STRONG_LLM_MODEL_ID")
        or "gemini-3.5-flash"
    )
    settings = VertexProviderSettings.from_env()

    answered = 0
    attempted = 0
    failures: list[str] = []
    for round_index in range(args.repeat):
        for case_index, (label, question, evidence, expectation) in enumerate(_CASES, start=1):
            attempted += 1
            suffix = str((round_index * len(_CASES) + case_index) % 10)
            try:
                answer, calls = _run_case(
                    index=suffix,
                    model=model,
                    settings=settings,
                    question=question,
                    evidence=evidence,
                    google=args.google,
                )
            except Exception as error:  # noqa: BLE001 - 프로브는 실패 사유를 그대로 보고한다.
                failures.append(f"{label}: {type(error).__name__}: {error}")
                print(
                    json.dumps(
                        {"round": round_index + 1, "case": label, "error": str(error)},
                        ensure_ascii=False,
                    )
                )
                continue

            has_answer = bool(answer.answer and answer.sentences)
            answered += has_answer
            cited = sorted({cid for item in answer.sentences for cid in item.citationIds})
            print(
                json.dumps(
                    {
                        "round": round_index + 1,
                        "case": label,
                        "expectation": expectation,
                        "basis": answer.basis,
                        "providerCalls": calls,
                        "hasAnswer": has_answer,
                        "sentenceCount": len(answer.sentences),
                        "citationIds": cited,
                        "warnings": list(answer.warnings),
                        "answerPreview": (answer.answer or "")[:160],
                    },
                    ensure_ascii=False,
                )
            )
            if not has_answer:
                failures.append(f"{label}: empty answer with basis {answer.basis}")
            if label == "GROUNDED" and not cited:
                failures.append(f"{label}: evidence supplied but no citation was bound")

    print(
        json.dumps(
            {
                "attempted": attempted,
                "answered": answered,
                "answerRate": round(answered / attempted, 4) if attempted else 0.0,
                "failures": failures,
            },
            ensure_ascii=False,
        )
    )
    if failures:
        print("RAG_EXPLAIN_PROBE_FAILED", file=sys.stderr)
        return 1
    print("RAG_EXPLAIN_PROBE_EVERY_QUESTION_ANSWERED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
