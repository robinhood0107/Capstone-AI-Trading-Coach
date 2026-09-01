"""One-shot real Vertex grounding -> evidence-bound JUDGE probe.

The probe has no brokerage, account, balance, position, or order dependency.  It
prints only the exact project prompts, provider-observed public grounding, and
the host-projected JUDGE result.  Provider calls are capped before invocation:
one Google discovery call and one tool-free JUDGE call, with no retry.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.strong_llm.models import Evidence, JudgementCandidate, RunRequest, StrongLlmJudgement
from app.strong_llm.prompt import render_discovery_prompt, render_prompt, require_google_grounding
from app.strong_llm.runtime import BoundedStrongLlmGraph
from app.strong_llm.vertex_provider import LangChainVertexProvider, VertexProviderSettings

_REPOSITORY = Path(__file__).resolve().parents[5]
_SYMBOLS = ("005930", "000660")
_MAX_GOOGLE_QUERIES = 32


def _registry() -> dict[str, dict[str, str]]:
    payload = json.loads(
        (_REPOSITORY / "contracts/catalogs/p1-vertex-news-sources.v1.json").read_text(
            encoding="utf-8"
        )
    )
    return {str(item["domain"]).lower(): dict(item) for item in payload["sources"]}


def _registered_domain(raw: str, registry: dict[str, dict[str, str]]) -> str | None:
    domain = raw.strip().lower().removesuffix(".")
    return next(
        (
            item
            for item in sorted(registry, key=len, reverse=True)
            if domain == item or domain.endswith("." + item)
        ),
        None,
    )


def _contains_symbol(text: str, symbol: str) -> bool:
    return re.search(rf"(?<![0-9]){re.escape(symbol)}(?![0-9])", text) is not None


def _twenty_word_prefix(text: str) -> str:
    matches = list(re.finditer(r"\S+", text))
    if not matches:
        return ""
    return text[: matches[min(20, len(matches)) - 1].end()]


class _Permit:
    def __init__(self, allowed_phase: str, *, google_attached: bool) -> None:
        self.allowed_phase = allowed_phase
        self.google_attached = google_attached
        self.calls = 0

    def __call__(self, call_id: str, phase: str, google_attached: bool) -> None:
        del call_id
        if (
            self.calls != 0
            or phase != self.allowed_phase
            or google_attached is not self.google_attached
        ):
            raise RuntimeError("GROUNDED_JUDGE_PROBE_CAP_EXCEEDED")
        self.calls += 1


def _no_tool(name: str, call_id: str, arguments: dict[str, object]) -> str:
    del name, call_id, arguments
    raise RuntimeError("GROUNDED_JUDGE_PROBE_TOOL_FORBIDDEN")


def _request(
    *,
    run_suffix: str,
    model: str,
    question: str,
    mode: str,
    evidence: tuple[Evidence, ...] = (),
    candidates: tuple[JudgementCandidate, ...] = (),
    google: bool,
) -> RunRequest:
    return RunRequest(
        run_id="s49_run_" + run_suffix * 32,
        model_id=model,
        question=question,
        answer_mode="CONCISE",
        related_symbols=_SYMBOLS,
        topics=("AUTOMATION_NEWS_SCREEN", "PUBLIC_ADVERSE_EVIDENCE")
        if google
        else ("AUTOMATION_EVIDENCE_JUDGE",),
        public_evidence=evidence,
        owner_evidence=(),
        google_search_enabled=google,
        max_tool_rounds=0,
        current_time=datetime.now(UTC).isoformat(),
        timezone="Asia/Seoul",
        language="ko",
        mode=mode,
        candidates=candidates,
        thinking_level="low",
        grounding_discovery_only=google,
    )


def main() -> int:
    model = (
        os.environ.get("VERTEX_MODEL_ID")
        or os.environ.get("STRONG_LLM_MODEL_ID")
        or "gemini-3.5-flash"
    )
    settings = VertexProviderSettings.from_env()
    registry = _registry()
    screen_question = (
        "Google Search로 다음 한국 종목 후보 전체의 최근 공개 악재·공시를 조사하세요. "
        "각 근거 문장은 반드시 해당 6자리 symbol로 시작하고, 확인되지 않은 사실은 쓰지 마세요. "
        "등록 출처 도메인 안에서만 검색하세요. 후보: 005930,000660. 허용 도메인: "
        + ",".join(sorted(registry))
    )
    screen_request = _request(
        run_suffix="1",
        model=model,
        question=screen_question,
        mode="EXPLAIN",
        google=True,
    )
    screen_permit = _Permit("GOOGLE_DISCOVERY", google_attached=True)
    screen_result = BoundedStrongLlmGraph().run(
        screen_request,
        LangChainVertexProvider(screen_request, settings),
        screen_permit,
        _no_tool,
    )
    if (
        screen_result.vertex_generate_call_count != 1
        or not 1 <= screen_result.google_grounding_query_count <= _MAX_GOOGLE_QUERIES
        or not screen_result.grounding_roots
        or not screen_result.grounding_supports
    ):
        raise RuntimeError("GROUNDED_JUDGE_PROBE_DISCOVERY_FAILED")

    evidence_by_symbol: dict[str, list[dict[str, str]]] = {symbol: [] for symbol in _SYMBOLS}
    for root in screen_result.grounding_roots[:5]:
        domain = _registered_domain(root.domain, registry)
        if domain is None:
            continue
        support = next(
            (
                item
                for item in screen_result.grounding_supports
                if root.chunk_index in item.chunk_indices and item.text.strip()
            ),
            None,
        )
        if support is None:
            continue
        quote = _twenty_word_prefix(support.text.strip())
        for symbol in _SYMBOLS:
            if _contains_symbol(quote, symbol):
                evidence_by_symbol[symbol].append(
                    {
                        "citationId": root.citation_id,
                        "domain": domain,
                        "quote": quote,
                        "sourceId": registry[domain]["sourceId"],
                        "sourceType": registry[domain]["sourceType"],
                        "title": root.title,
                        "uri": root.uri,
                    }
                )

    unique: dict[str, dict[str, str]] = {}
    for symbol in _SYMBOLS:
        for item in evidence_by_symbol[symbol]:
            unique.setdefault(item["citationId"], item)
    canonical = tuple(unique.values())[:5]
    if not canonical:
        diagnostics = {
            "calls": {
                "googleQueries": screen_result.google_grounding_query_count,
                "retry": 0,
                "screenGenerate": screen_result.vertex_generate_call_count,
            },
            "groundingRoots": [
                {
                    "citationId": item.citation_id,
                    "domain": item.domain,
                    "title": item.title,
                    "uri": item.uri,
                }
                for item in screen_result.grounding_roots
            ],
            "groundingSupports": [
                {
                    "chunkIndices": list(item.chunk_indices),
                    "text": _twenty_word_prefix(item.text),
                }
                for item in screen_result.grounding_supports
            ],
            "reason": "GROUNDED_JUDGE_PROBE_REGISTERED_EVIDENCE_MISSING",
            "status": "PARTIAL",
            "webSearchQueries": list(screen_result.web_search_queries),
        }
        print(json.dumps(diagnostics, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    evidence = tuple(
        Evidence(
            ordinal=index,
            citation_id=item["citationId"],
            chunk_revision_id="rag_v2_chk_"
            + hashlib.sha256(item["quote"].encode()).hexdigest()[:32],
            canonical_text=item["quote"],
            canonical_text_sha256=hashlib.sha256(item["quote"].encode()).hexdigest(),
        )
        for index, item in enumerate(canonical, start=1)
    )
    candidates = (
        JudgementCandidate("005930", 0.0400, 0.51, "BUY", "BUY"),
        JudgementCandidate("000660", 0.0399, 0.93, "BUY", "BUY"),
    )
    judge_question = (
        "제공된 검증 근거만 사용해 각 후보를 평가하세요. score 또는 veto에 근거를 사용하면 "
        "evidenceSpans.quote에 해당 citation의 전체 문자열을 byte-for-byte 복사하세요."
    )
    judge_request = _request(
        run_suffix="2",
        model=model,
        question=judge_question,
        mode="JUDGE",
        evidence=evidence,
        candidates=candidates,
        google=False,
    )
    judge_permit = _Permit("FINAL", google_attached=False)
    judge_result = BoundedStrongLlmGraph().run(
        judge_request,
        LangChainVertexProvider(judge_request, settings),
        judge_permit,
        _no_tool,
    )
    judgement = StrongLlmJudgement.model_validate_json(judge_result.answer_json)
    if {item.symbol for item in judgement.candidates} != set(_SYMBOLS):
        raise RuntimeError("GROUNDED_JUDGE_PROBE_CANDIDATE_SET_DRIFT")

    projected: list[dict[str, Any]] = []
    for item in judgement.candidates:
        supported = {
            evidence_item["citationId"]: evidence_item["quote"]
            for evidence_item in evidence_by_symbol[item.symbol]
        }
        spans = [(span.citationId, span.quote) for span in item.evidenceSpans]
        spans_valid = (
            bool(spans)
            and len(spans) <= 5
            and all(supported.get(citation_id) == quote for citation_id, quote in spans)
        )
        projected.append(
            {
                "evidenceSpans": [
                    {"citationId": citation_id, "quote": quote} for citation_id, quote in spans
                ]
                if spans_valid
                else [],
                "reason": item.reason,
                "score": item.score if spans_valid else 0.5,
                "symbol": item.symbol,
                "veto": item.veto and spans_valid,
                "verified": spans_valid,
            }
        )

    discovery_prompt = require_google_grounding(render_discovery_prompt(screen_request))
    judge_prompt = render_prompt(judge_request, evidence)
    applied = any(item["verified"] for item in projected)
    score_difference = len({item["score"] for item in projected}) > 1
    verified_veto = any(item["veto"] for item in projected)
    output = {
        "calls": {
            "googleQueries": screen_result.google_grounding_query_count,
            "judgeGenerate": judge_result.vertex_generate_call_count,
            "retry": 0,
            "screenGenerate": screen_result.vertex_generate_call_count,
        },
        "evidence": evidence_by_symbol,
        "judge": {
            "applied": applied,
            "confidence": judgement.confidence,
            "projectedCandidates": projected,
            "rawSummary": judgement.summary,
            "scoreDifference": score_difference,
            "verifiedVeto": verified_veto,
        },
        "prompts": {
            "discovery": {
                "system": discovery_prompt.system,
                "user": discovery_prompt.user,
                "version": discovery_prompt.version,
            },
            "judge": {
                "system": judge_prompt.system,
                "user": judge_prompt.user,
                "version": judge_prompt.version,
            },
        },
        "status": "PASS" if applied and score_difference else "PARTIAL",
        "webSearchQueries": list(screen_result.web_search_queries),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if applied and score_difference else 2


if __name__ == "__main__":
    raise SystemExit(main())
