"""근거는 등록 코퍼스에서만 나오고, 판정할 수 없는 문서는 근거가 되지 못한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from app.p1_owner.vertex_corpus_evidence import (
    CorpusDocument,
    CorpusEvidenceError,
    EmptyCorpusDocumentSource,
    build_public_evidence,
)

_SESSION = date(2026, 8, 28)
_QUOTE = "금융감독원은 해당 법인에 대한 회계감리 결과 제재 절차에 착수했다고 밝혔다."


@dataclass(frozen=True, slots=True)
class FakeCorpus:
    items: tuple[CorpusDocument, ...]

    def documents(self, *, symbol: str, session_date: date) -> tuple[CorpusDocument, ...]:
        del symbol, session_date
        return self.items


def _document(uri: str, published_on: date, passage: str = _QUOTE) -> CorpusDocument:
    return CorpusDocument(uri=uri, published_on=published_on, passage=passage)


def test_registered_documents_become_evidence_with_host_known_dates() -> None:
    corpus = FakeCorpus(
        (
            _document("https://dart.fss.or.kr/x", date(2026, 8, 27)),
            _document("https://reuters.com/y", date(2026, 8, 26)),
        )
    )

    evidence = build_public_evidence(corpus, symbol="005930", session_date=_SESSION)

    assert [item["sourceId"] for item in evidence] == ["src_official_dart", "src_press_reuters"]
    assert evidence[0]["sourceType"] == "OFFICIAL_PRIMARY"
    assert evidence[1]["sourceType"] == "REGISTERED_INDEPENDENT"
    assert evidence[0]["sourceEventDate"] == "2026-08-27"


@pytest.mark.parametrize(
    ("uri", "published_on", "passage", "why"),
    [
        # 등록되지 않은 domain
        ("https://example.com/x", date(2026, 8, 27), _QUOTE, "unregistered"),
        # 신선도 창 밖 (8 캘린더일)
        ("https://reuters.com/x", date(2026, 8, 20), _QUOTE, "stale"),
        # 세션 이후 날짜
        ("https://reuters.com/x", date(2026, 8, 29), _QUOTE, "future"),
        # 인용 경계 미달
        ("https://reuters.com/x", date(2026, 8, 27), "짧다", "short"),
    ],
)
def test_documents_the_host_cannot_vouch_for_are_dropped(
    uri: str, published_on: date, passage: str, why: str
) -> None:
    corpus = FakeCorpus((_document(uri, published_on, passage),))

    assert build_public_evidence(corpus, symbol="005930", session_date=_SESSION) == []


def test_one_evidence_item_per_source_and_at_most_eight() -> None:
    corpus = FakeCorpus(
        tuple(_document("https://reuters.com/%d" % index, date(2026, 8, 27)) for index in range(5))
    )

    evidence = build_public_evidence(corpus, symbol="005930", session_date=_SESSION)

    assert len(evidence) == 1


def test_no_corpus_means_no_evidence_which_the_verifier_treats_as_abstain() -> None:
    assert (
        build_public_evidence(EmptyCorpusDocumentSource(), symbol="005930", session_date=_SESSION)
        == []
    )


def test_symbol_must_be_a_six_digit_code() -> None:
    with pytest.raises(CorpusEvidenceError):
        build_public_evidence(EmptyCorpusDocumentSource(), symbol="AAPL", session_date=_SESSION)
