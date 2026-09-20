"""저장된 세계 뉴스를 Vertex 거부권의 근거 문서로 투영한다. network 호출 0.

`CorpusDocument` 계약은 "uri와 발행일은 수집 시점에 host가 기록한 사실" 을 요구한다. 그래서
발행일은 원문이 주장하는 날짜가 아니라 **우리가 기록한 값**을 쓴다 - 수집기가 원문 발행일을
`VERIFIED` 로 확인했으면 그 날짜, 아니면 우리가 그 문서를 처음 본 시각(`first_seen_at`)이다.
둘 다 host 가 적은 사실이고 모델이 바꿀 수 없다.

인가가 약해지지 않는 이유는 네 가지다. 셋은 이미 있던 것이다.

1. 등록 도메인만 근거가 된다. 조립기가 `registered_source_for_uri` 로 한 번 더 거르므로
   여기서 통과시켜도 미등록 도메인은 근거가 되지 못한다. 그래도 여기서 먼저 걸러 둔다 -
   쓸 수 없는 문서를 상한 안으로 올려 보내면 쓸 수 있는 문서를 밀어낸다.
2. 거부권은 매수를 막을 수만 있고 무엇도 사게 하지 못한다.
3. 근거 0개는 ABSTAIN 이고, ABSTAIN 은 통과다.
4. 종목 매칭은 정식명 정확 일치만 인정한다(아래).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

from app.p1_owner.vertex_corpus_evidence import CorpusDocument
from app.p1_owner.vertex_source_registry import registered_source_for_uri

#: 근거로 볼 수집 창. 공시 코퍼스와 같은 폭을 쓴다 - 한쪽만 넓히면 어느 쪽 근거가
#: 더 많은지가 창 길이로 정해져 버린다.
_WINDOW_CALENDAR_DAYS = 30

#: 한 종목에 올릴 최대 문서 수. 조립기가 도메인당 한 건만 쓰므로 이보다 적게 남는다.
_MAX_DOCUMENTS_PER_SYMBOL = 8

#: 이름 뒤에 붙어도 여전히 같은 이름인 글자. 한국어 조사의 첫 음절만 좁게 받는다.
#: 여기에 없는 한글이 이어지면 더 긴 다른 이름으로 보고 매칭하지 않는다.
_PARTICLE_HEADS = frozenset("가이은는을를의에와과도로만부까보")

_HANGUL = re.compile(r"[가-힣]")
_BOUNDARY = re.compile(r"[0-9A-Za-z가-힣]")


@dataclass(frozen=True, slots=True)
class StoredWorldNewsArticle:
    """저장된 기사 한 건의 투영. 표 구조를 이 모듈 밖으로 새지 않게 한다."""

    canonical_url: str
    title: str
    bounded_quote: str | None
    bounded_passage: str | None
    published_at: datetime | None
    publication_status: str
    first_seen_at: datetime


class StoredWorldNewsLoader(Protocol):
    """수집 창 안의 기사를 최신 먼저 돌려준다. network 호출은 구현의 몫이 아니다."""

    def load(
        self, *, window_from: date, window_to: date, limit: int
    ) -> tuple[StoredWorldNewsArticle, ...]: ...


class InstrumentNameLoader(Protocol):
    """종목의 정식 표시명. 없으면 None - 그 종목은 뉴스 근거를 갖지 않는다."""

    def official_name(self, *, symbol: str) -> str | None: ...


def recorded_publication_date(article: StoredWorldNewsArticle) -> date:
    """발행일은 **수집일**이다. 우리가 그 문서를 처음 본 날.

    원문이 주장하는 날짜를 쓰지 않는 이유는 두 가지다. 첫째, 계약이 요구하는 것은
    'host 가 수집 시점에 기록한 사실' 이고  이 정확히 그것이다 - 우리가
    적었고 모델이 바꿀 수 없다. 둘째, 원문 발행일은 대부분 / 라
    그것을 기준으로 삼으면 확인된 소수만 근거가 되고 나머지가 통째로 빠진다.

    한 기준으로 통일해야 정렬도 뜻을 갖는다. 문서마다 날짜 기준이 다르면 '최신 먼저' 가
    수집 순서인지 발행 순서인지 알 수 없는 뒤섞인 목록이 된다.
    """

    return article.first_seen_at.date()


def mentions_instrument(text: str, official_name: str) -> bool:
    """정식명이 통째로 나타날 때만 그 종목의 기사로 본다.

    부분 일치를 허용하면 'SK' 가 SK하이닉스·SK이노베이션·SK텔레콤에 전부 붙는다. 그런데
    한글에는 낱말 경계가 없고 조사가 이름에 바로 붙는다('SK하이닉스가', '신한지주의').
    그래서 앞은 막고, 뒤는 **조사만** 허용한다 - 조사가 아닌 한글이 이어지면 그것은 더 긴
    다른 이름이다('삼성전자우', '카카오뱅크', '한국전력기술').
    """

    if not official_name:
        return False
    for match in re.finditer(re.escape(official_name), text):
        start, end = match.start(), match.end()
        if start > 0 and _BOUNDARY.match(text[start - 1]):
            continue
        if end < len(text):
            following = text[end]
            if _HANGUL.match(following) and following not in _PARTICLE_HEADS:
                continue
            if following.isalnum() and not _HANGUL.match(following):
                continue
        return True
    return False


@dataclass(frozen=True, slots=True)
class WorldNewsCorpusDocumentSource:
    """저장된 세계 뉴스를 근거 문서로 투영한다."""

    loader: StoredWorldNewsLoader
    names: InstrumentNameLoader

    def documents(self, *, symbol: str, session_date: date) -> tuple[CorpusDocument, ...]:
        official_name = self.names.official_name(symbol=symbol)
        if not official_name:
            return ()
        articles = self.loader.load(
            window_from=session_date - timedelta(days=_WINDOW_CALENDAR_DAYS),
            window_to=session_date,
            # 정식명 일치로 다시 줄어들므로 넉넉히 읽고 여기서 자른다.
            limit=_MAX_DOCUMENTS_PER_SYMBOL * 40,
        )
        documents: list[CorpusDocument] = []
        for article in articles:
            if registered_source_for_uri(article.canonical_url) is None:
                continue
            passage = article.bounded_quote or article.bounded_passage
            if not passage:
                continue
            haystack = article.title + chr(10) + passage
            if not mentions_instrument(haystack, official_name):
                continue
            documents.append(
                CorpusDocument(
                    uri=article.canonical_url,
                    published_on=recorded_publication_date(article),
                    passage=passage,
                )
            )
            if len(documents) >= _MAX_DOCUMENTS_PER_SYMBOL:
                break
        return tuple(documents)


@dataclass(frozen=True, slots=True)
class MergedCorpusDocumentSource:
    """여러 원천을 합쳐 최신 먼저로 돌려준다.

    조립기가 도메인당 한 건만 쓰므로 공시(dart.fss)와 언론 기사가 서로를 밀어내지 않는다.
    한 원천이 실패해도 나머지 근거는 살린다 - 근거가 줄면 ABSTAIN 쪽으로 기울 뿐이고,
    그것은 매매를 멈추지 않는다.
    """

    sources: tuple[object, ...]

    def documents(self, *, symbol: str, session_date: date) -> tuple[CorpusDocument, ...]:
        merged: list[CorpusDocument] = []
        for source in self.sources:
            documents = source.documents(symbol=symbol, session_date=session_date)  # type: ignore[attr-defined]
            merged.extend(documents)
        merged.sort(key=lambda item: item.published_on, reverse=True)
        return tuple(merged)
