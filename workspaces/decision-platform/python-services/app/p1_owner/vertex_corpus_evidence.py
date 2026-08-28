"""뉴스 거부권이 근거로 쓸 문서를 등록 코퍼스에서만 고른다.

Google Search grounding metadata는 발행일을 주지 않아 host가 근거의 날짜를 독립적으로 알 수 없다.
그래서 grounding을 이미 수집·색인한 코퍼스로 옮겼다. 여기서 고른 항목만 요청 패킷의 publicEvidence로
들어가고, 모델은 그 밖의 문장을 근거로 쓸 수 없다.

이 모듈은 provider를 호출하지 않는다. 문서를 어디서 가져오는지는 CorpusDocumentSource 구현이 정하고,
등록 여부·신선도·인용 경계는 여기서 강제한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from app.p1_owner.vertex_source_registry import registered_source_for_uri

_FRESHNESS_CALENDAR_DAYS = 7
_MAX_EVIDENCE_ITEMS = 8
_MAX_QUOTE_CHARACTERS = 320
_MIN_QUOTE_CHARACTERS = 20


class CorpusEvidenceError(RuntimeError):
    """코퍼스 문서가 근거 계약을 만족하지 못할 때 발생한다."""


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    """색인된 문서 하나. uri와 발행일은 수집 시점에 host가 기록한 사실이다."""

    uri: str
    published_on: date
    passage: str


class CorpusDocumentSource(Protocol):
    """세션과 종목에 해당하는 색인 문서를 돌려준다. network 호출은 구현의 몫이 아니다."""

    def documents(self, *, symbol: str, session_date: date) -> tuple[CorpusDocument, ...]: ...


@dataclass(frozen=True, slots=True)
class EmptyCorpusDocumentSource:
    """뉴스 코퍼스가 아직 없을 때의 기본값. 근거 0개는 곧 ABSTAIN이다."""

    def documents(self, *, symbol: str, session_date: date) -> tuple[CorpusDocument, ...]:
        del symbol, session_date
        return ()


def _bounded_quote(passage: str) -> str | None:
    text = " ".join(passage.split())
    if len(text) < _MIN_QUOTE_CHARACTERS:
        return None
    return text[:_MAX_QUOTE_CHARACTERS]


def build_public_evidence(
    source: CorpusDocumentSource,
    *,
    symbol: str,
    session_date: date,
) -> list[dict[str, object]]:
    """요청 패킷에 넣을 publicEvidence를 만든다. 판정 불가한 문서는 조용히 버린다.

    버리는 기준은 셋이다. 등록되지 않은 domain, 신선도 창(7 캘린더일) 밖, 인용 경계 미달.
    남는 게 없으면 빈 목록이고, 그러면 검증기가 NO_GROUNDING으로 ABSTAIN한다.
    """

    if not symbol or len(symbol) != 6 or not symbol.isdigit():
        raise CorpusEvidenceError("vertex corpus evidence symbol is invalid")
    evidence: list[dict[str, object]] = []
    seen: set[str] = set()
    for document in source.documents(symbol=symbol, session_date=session_date):
        registered = registered_source_for_uri(document.uri)
        if registered is None:
            continue
        source_id, source_type = registered
        if source_id in seen:
            continue
        age = (session_date - document.published_on).days
        if age < 0 or age > _FRESHNESS_CALENDAR_DAYS:
            continue
        quote = _bounded_quote(document.passage)
        if quote is None:
            continue
        seen.add(source_id)
        evidence.append(
            {
                "boundedQuote": quote,
                "sourceEventDate": document.published_on.isoformat(),
                "sourceId": source_id,
                "sourceType": source_type,
            }
        )
        if len(evidence) == _MAX_EVIDENCE_ITEMS:
            break
    evidence.sort(key=lambda item: str(item["sourceId"]))
    return evidence
