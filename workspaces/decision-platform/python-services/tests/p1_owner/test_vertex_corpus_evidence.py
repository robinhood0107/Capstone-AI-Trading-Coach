"""근거는 등록 코퍼스에서만 나오고, 판정할 수 없는 문서는 근거가 되지 못한다."""

from __future__ import annotations

import json
import re

from dataclasses import dataclass
from datetime import date

import pytest

from app.data._shared.repository_root import repository_root
from app.p1_owner.vertex_corpus_evidence import (
    _MAX_QUOTE_CHARACTERS,
    CorpusDocument,
    CorpusEvidenceError,
    EmptyCorpusDocumentSource,
    build_public_evidence,
)

_ROOT = repository_root(__file__, 5)
_MIGRATIONS = _ROOT / "workspaces/decision-platform/spring-api/src/main/resources/db/migration"
_VETO_SCHEMA = _ROOT / "contracts/schemas/vertex-news-veto.v1.schema.json"
_VETO_MODULE = _ROOT / "workspaces/decision-platform/python-services/app/p1_owner/vertex_veto.py"

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


def _latest_bounded_quote_bound() -> int:
    """bounded_quote CHECK 를 마지막으로 쓴 마이그레이션의 상한.

    한 파일을 이름으로 붙들면 다음에 그 CHECK 를 옮겨 쓸 때 대조가 조용히 옛 정의를 본다.
    """

    pattern = re.compile(r"char_length\(bounded_quote\)\s+BETWEEN\s+1\s+AND\s+(\d+)")
    found: list[tuple[int, int]] = []
    for path in _MIGRATIONS.glob("V*__*.sql"):
        migration = path.read_text(encoding="utf-8")
        # 세계 뉴스처럼 다른 bounded_quote column은 이 자동운용 evidence 계약의 네 계층이 아니다.
        if "automation_candidate_evidence" not in migration:
            continue
        match = pattern.search(migration)
        if match is None:
            continue
        version = int(re.match(r"V(\d+)__", path.name).group(1))  # type: ignore[union-attr]
        found.append((version, int(match.group(1))))
    assert found, "bounded_quote CHECK migration is missing"
    return max(found, key=lambda item: item[0])[1]


def test_quote_bound_matches_every_layer_that_asserts_it() -> None:
    """인용 상한은 네 계층에 각각 적혀 있다. 하나만 달라도 근거가 조용히 버려진다.

    실제로 이 모듈만 320 이었고 나머지 넷은 240 이었다. 그 결과는 DB 오류가 아니라
    vertex_veto 의 SCHEMA_ERROR -> ABSTAIN 이다. 즉 거부권이 "판정 못 함"으로 보이고
    원인은 어디에도 안 나온다. 근거가 항상 0 개였던 동안에는 드러나지 않았다.
    """

    assert _MAX_QUOTE_CHARACTERS == _latest_bounded_quote_bound()

    schema = json.loads(_VETO_SCHEMA.read_text(encoding="utf-8"))
    quote_bounds = {
        node["maxLength"] for node in _bounded_quote_nodes(schema) if "maxLength" in node
    }
    assert quote_bounds == {_MAX_QUOTE_CHARACTERS}

    veto_source = _VETO_MODULE.read_text(encoding="utf-8")
    assert f"1 <= len(quote) <= {_MAX_QUOTE_CHARACTERS}" in veto_source


def _bounded_quote_nodes(node: object) -> list[dict[str, object]]:
    """스키마 어디에 있든 boundedQuote 속성 정의만 모은다."""

    if isinstance(node, dict):
        found: list[dict[str, object]] = []
        properties = node.get("properties")
        if isinstance(properties, dict) and isinstance(properties.get("boundedQuote"), dict):
            found.append(properties["boundedQuote"])
        for value in node.values():
            found.extend(_bounded_quote_nodes(value))
        return found
    if isinstance(node, list):
        return [item for value in node for item in _bounded_quote_nodes(value)]
    return []


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
