"""세계 뉴스를 근거로 쓸 때의 경계를 고정한다.

거래에 닿는 유일한 뉴스 경로다. 여기서 느슨해지면 등록되지 않은 도메인이나 엉뚱한 종목의
기사가 거부권 판단에 들어간다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.p1_owner.world_news_corpus import (
    MergedCorpusDocumentSource,
    StoredWorldNewsArticle,
    WorldNewsCorpusDocumentSource,
    mentions_instrument,
    recorded_publication_date,
)
from app.p1_owner.vertex_corpus_evidence import CorpusDocument

_SESSION = date(2026, 9, 16)
NEWLINE = chr(10)


def _article(
    *,
    url: str = "https://biz.chosun.com/a",
    title: str = "신한지주 실적 발표",
    quote: str | None = "신한지주가 분기 실적을 공시했다.",
    passage: str | None = None,
    published_at: datetime | None = None,
    status: str = "MISSING",
    first_seen: datetime = datetime(2026, 9, 15, 3, 0, tzinfo=UTC),
) -> StoredWorldNewsArticle:
    return StoredWorldNewsArticle(
        canonical_url=url,
        title=title,
        bounded_quote=quote,
        bounded_passage=passage,
        published_at=published_at,
        publication_status=status,
        first_seen_at=first_seen,
    )


class _Loader:
    def __init__(self, *articles: StoredWorldNewsArticle) -> None:
        self._articles = articles
        self.calls: list[tuple[date, date, int]] = []

    def load(self, *, window_from, window_to, limit):
        self.calls.append((window_from, window_to, limit))
        return self._articles


class _Names:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def official_name(self, *, symbol: str) -> str | None:
        return self._mapping.get(symbol)


_NAMES = _Names({"055550": "신한지주", "000660": "SK하이닉스"})


def test_the_publication_date_is_always_the_collection_date() -> None:
    """발행일은 수집일 하나로 통일한다.

    원문 발행일은 대부분 MISSING/CONFLICT 라 그것을 기준으로 삼으면 확인된 소수만
    근거가 되고 나머지가 통째로 빠진다. 그리고 문서마다 기준이 다르면 '최신 먼저' 정렬이
    수집 순서인지 발행 순서인지 알 수 없는 뒤섞인 목록이 된다.
    """

    for status in ("VERIFIED", "MISSING", "CONFLICT"):
        article = _article(status=status, published_at=datetime(2026, 9, 10, 1, 0, tzinfo=UTC))
        # 원문이 09-10 을 주장해도 우리가 본 날은 09-15 다.
        assert recorded_publication_date(article) == date(2026, 9, 15)


def test_only_a_whole_official_name_counts_as_a_mention() -> None:
    """부분 일치를 허용하면 "SK" 가 SK 계열 전부에 붙는다."""

    assert mentions_instrument("SK하이닉스가 증설한다", "SK하이닉스") is True
    assert mentions_instrument("SK이노베이션 실적", "SK하이닉스") is False
    # 조사는 이름에 바로 붙는다. 이것까지 막으면 한국어 기사 대부분이 걸러진다.
    assert mentions_instrument("신한지주의 실적", "신한지주") is True
    assert mentions_instrument("삼성전자는 발표했다", "삼성전자") is True
    # 조사가 아닌 한글이 이어지면 더 긴 다른 이름이다.
    assert mentions_instrument("카카오뱅크 출범", "카카오") is False
    assert mentions_instrument("한국전력기술 수주", "한국전력") is False
    # 한글에는 낱말 경계가 없으므로 앞뒤 글자가 붙으면 다른 이름이다.
    assert mentions_instrument("삼성전자우 배당", "삼성전자") is False
    assert mentions_instrument("삼성전자, 실적 발표", "삼성전자") is True


def test_an_unregistered_domain_never_becomes_evidence() -> None:
    """등록 도메인 목록 밖의 기사는 상한을 차지하지도 못한다."""

    source = WorldNewsCorpusDocumentSource(
        loader=_Loader(_article(url="https://example-blog.test/post")), names=_NAMES
    )

    assert source.documents(symbol="055550", session_date=_SESSION) == ()


def test_an_article_about_another_company_is_not_this_symbols_evidence() -> None:
    source = WorldNewsCorpusDocumentSource(
        loader=_Loader(_article(title="카카오 실적", quote="카카오가 발표했다.")),
        names=_NAMES,
    )

    assert source.documents(symbol="055550", session_date=_SESSION) == ()


def test_a_symbol_without_an_official_name_reads_nothing_at_all() -> None:
    """이름을 모르면 정확 일치를 판정할 수 없다. 그때는 조회조차 하지 않는다."""

    loader = _Loader(_article())
    source = WorldNewsCorpusDocumentSource(loader=loader, names=_NAMES)

    assert source.documents(symbol="999999", session_date=_SESSION) == ()
    assert loader.calls == []


def test_a_registered_article_about_this_symbol_becomes_one_document() -> None:
    source = WorldNewsCorpusDocumentSource(loader=_Loader(_article()), names=_NAMES)

    documents = source.documents(symbol="055550", session_date=_SESSION)

    assert documents == (
        CorpusDocument(
            uri="https://biz.chosun.com/a",
            published_on=date(2026, 9, 15),
            passage="신한지주가 분기 실적을 공시했다.",
        ),
    )


def test_a_document_without_any_passage_is_skipped() -> None:
    """인용할 문장이 없으면 근거가 될 수 없다."""

    source = WorldNewsCorpusDocumentSource(
        loader=_Loader(_article(quote=None, passage=None)), names=_NAMES
    )

    assert source.documents(symbol="055550", session_date=_SESSION) == ()


def test_the_merged_source_returns_the_newest_first() -> None:
    """공시와 언론을 합쳐도 최신 먼저다. 조립기가 도메인당 한 건만 쓰므로 서로 밀어내지 않는다."""

    class _Fixed:
        def __init__(self, *documents: CorpusDocument) -> None:
            self._documents = documents

        def documents(self, *, symbol: str, session_date: date):
            del symbol, session_date
            return self._documents

    older = CorpusDocument(
        uri="https://dart.fss.or.kr/x", published_on=date(2026, 9, 1), passage="a"
    )
    newer = CorpusDocument(
        uri="https://biz.chosun.com/y", published_on=date(2026, 9, 14), passage="b"
    )

    merged = MergedCorpusDocumentSource(sources=(_Fixed(older), _Fixed(newer)))

    assert merged.documents(symbol="055550", session_date=_SESSION) == (newer, older)


def test_the_evidence_read_uses_the_rag_flag_not_the_lookup_flag() -> None:
    """근거 읽기는 화면 조회와 다른 깃발을 본다.

    기존 `read_world_news_documents_v2` 는 조회용이고 그 경로의 권한 표기는
    decision/signal/order 모두 NONE 이다. 근거로 쓰려면 스키마가 이미 구분해 둔
    `rag_retrieval_allowed` 를 봐야 한다. 두 경로가 한 함수를 공유하면 한쪽을 넓힐 때
    다른 쪽이 조용히 따라 넓어진다.
    """

    from app.data._shared.repository_root import repository_root

    migration = (
        repository_root(__file__, 5)
        / "workspaces/decision-platform/spring-api/src/main/resources/db/migration"
        / "V177__world_news_evidence_read_scope.sql"
    ).read_text(encoding="utf-8")

    # 주석은 두 경로를 대조하느라 조회용 깃발 이름을 언급한다. 검사 대상은 실행되는 본문이다.
    body = NEWLINE.join(
        line for line in migration.split(NEWLINE) if not line.lstrip().startswith("--")
    )
    assert "version.rag_retrieval_allowed" in body
    assert "lookup_allowed" not in body
    # 근거 읽기는 공시 코퍼스가 쓰는 읽기 전용 역할에만 열고, 앱과 런타임에서는 회수한다.
    assert "GRANT EXECUTE ON FUNCTION public.p1_read_world_news_evidence_v1" in body
    assert "TO decision_disclosure_reader;" in body
    assert "FROM PUBLIC, decision_app, decision_automation_runtime;" in body


def test_only_the_disclosure_evidence_remains_when_news_cannot_be_read() -> None:
    """뉴스를 못 읽으면 공시 근거만 남는다.

    근거가 줄면 ABSTAIN 쪽으로 기울 뿐이고 ABSTAIN 은 통과다 - 뉴스를 못 읽는다고 해서
    매매가 멈추지는 않는다.
    """

    class _Disclosure:
        def documents(self, *, symbol: str, session_date: date):
            del symbol, session_date
            return (
                CorpusDocument(
                    uri="https://dart.fss.or.kr/x",
                    published_on=date(2026, 9, 1),
                    passage="a",
                ),
            )

    merged = MergedCorpusDocumentSource(sources=(_Disclosure(),))

    assert len(merged.documents(symbol="055550", session_date=_SESSION)) == 1
