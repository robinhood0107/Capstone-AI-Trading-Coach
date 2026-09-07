"""저장된 공시 구조화 이벤트를 뉴스 거부권의 근거 코퍼스로 투영한다.

`vertex_corpus_evidence.CorpusDocumentSource` 의 구현체다. 그 Protocol 만 있고 구현이 없어
기본값이 `EmptyCorpusDocumentSource` 였고, 그래서 근거가 항상 0개였고, 그래서 모든 세션이
`VERTEX_NO_REGISTERED_EVIDENCE` 로 닫혔고, 그래서 2026-09-04 에 거부권이 자문으로 격하됐다.
빠진 조각이 이것이다.

## 왜 공시목록 제목이 아니라 구조화 이벤트인가

세 가지가 그 방향을 정했다.

1. **런 안에서 provider 를 부를 여유가 없다.** `V100` 이 `p_provider_call_count` 를
   0~16 으로 못박는다. 근거를 세션 중에 가져오면 후보 몇 개만으로 상한을 넘는다.
   `vertex_corpus_evidence` 모듈 주석도 "이미 수집·색인한 코퍼스"를 전제한다 - 수집은
   런 밖의 일이다. 이 구현체는 DB 만 읽고 network 를 부르지 않는다.
2. **제목 문자열은 이 레포가 이미 거절했다.** `disclosure_risk_mapping.yaml` 이
   "공시검색 제목 문자열 매칭 없이는 공식 구조화 코드가 부족함"이라고 적고 그 부류를
   `blocked` 로 둔다. `client.disclosure_list` 의 docstring 도 제목을 점수 근거로 쓰지
   않는다고 적었다. 그래서 인용을 자유 텍스트가 아니라 **공식 구조화 값**으로 만든다 -
   커밋된 매핑의 활성 코드 라벨, 14자리 접수번호, 접수일.
3. **host 가 아는 발행일이 필요하다.** 근거 계약은 `sourceEventDate` 를 요구하고 Google
   grounding 은 발행일을 주지 않는다. 공시의 접수일(`occurred_on`)은 공식이고 우리가 수집
   시점에 기록한 사실이다.

## URI 를 왜 만들어 쓰나

투영의 `source_ref` 는 불투명한 64-hex 해시다(설계된 불투명성). 근거는 등록 domain 의 URI 를
요구하므로, 공식 접수번호로 DART 공개 열람 주소를 **결정적으로** 만든다. 새 provider 호출도
없고 불투명 참조가 새지도 않는다. `dart.fss.or.kr` 은 이미 등록된 `OFFICIAL_PRIMARY` 다.

## 판정하지 않는다

이 모듈은 위험 점수도 매기지 않고 매수·매도를 정하지도 않는다. 인용 후보를 만들 뿐이고,
등록 여부·7일 신선도·인용 경계는 `build_public_evidence` 가, 최종 판정은 `vertex_veto` 의
9단계 검증이 한다. 결정적 위험 점수 경로(`disclosure_risk_state_transitions`)와는 권한이
다르므로 여기서 만든 문장이 그 경로로 흘러가지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from app.data.opendart.risk_mapping import load_default_risk_mapping
from app.p1_owner.vertex_corpus_evidence import CorpusDocument

# 근거 신선도 창과 같은 값이다. 더 넓게 읽어도 `build_public_evidence` 가 버리므로 읽는
# 쪽에서 좁혀 질의 비용을 줄인다.
_WINDOW_CALENDAR_DAYS = 7
# 종목당 읽어 올 이벤트 수. 근거 조립기가 domain 하나당 한 건만 쓰므로 많이 필요하지 않고,
# 상한을 두어 한 종목의 공시 폭주가 응답을 키우지 못하게 한다.
_MAX_EVENTS_PER_SYMBOL = 8
_DART_VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="


class DisclosureEventLike(Protocol):
    """`app.disclosure_rpc.StoredDisclosureEvent` 가 만족하는 좁은 모양."""

    @property
    def event_code(self) -> str: ...

    @property
    def receipt_no(self) -> str: ...

    @property
    def occurred_on(self) -> date: ...


class DisclosureBatchLike(Protocol):
    @property
    def events(self) -> tuple[DisclosureEventLike, ...]: ...


class StoredDisclosureLoader(Protocol):
    """저장된 공시를 읽는 포트. 실제 구현은 `PostgresStoredDisclosureRepository` 다.

    여기서 Protocol 로 좁히는 이유는 이 모듈이 DSN·pool·psycopg 를 알 필요가 없고, 테스트가
    DB 없이 투영 규칙만 확인할 수 있어야 하기 때문이다.
    """

    def load(
        self,
        *,
        symbol: str,
        corp_code: str | None,
        window_from: date,
        window_to: date,
    ) -> DisclosureBatchLike: ...


def disclosure_event_uri(receipt_no: str) -> str | None:
    """접수번호로 DART 공개 열람 주소를 만든다. 14자리 숫자가 아니면 근거가 아니다."""

    if len(receipt_no) != 14 or not receipt_no.isdigit():
        return None
    return f"{_DART_VIEWER}{receipt_no}"


def disclosure_event_passage(*, label: str, receipt_no: str, occurred_on: date) -> str:
    """공식 구조화 값만으로 인용문을 만든다. 자유 텍스트를 넣지 않는다."""

    return f"OPENDART 공식 공시 {label}. 접수번호 {receipt_no}, 접수일 {occurred_on.isoformat()}."


@dataclass(frozen=True, slots=True)
class DisclosureEventCorpusDocumentSource:
    """저장된 공시 이벤트를 근거 문서로 투영한다. network 호출 0."""

    loader: StoredDisclosureLoader

    def documents(self, *, symbol: str, session_date: date) -> tuple[CorpusDocument, ...]:
        labels = {
            code: entry.label for code, entry in load_default_risk_mapping().active_by_code.items()
        }
        batch = self.loader.load(
            symbol=symbol,
            corp_code=None,
            window_from=session_date - timedelta(days=_WINDOW_CALENDAR_DAYS),
            window_to=session_date,
        )
        documents: list[CorpusDocument] = []
        for event in batch.events:
            # 활성 매핑에 라벨이 없는 코드는 쓰지 않는다. `blocked` 코드로 인용을 만들면
            # 그 코드가 안정적으로 분류되지 않는다는 레포의 판단을 우회하는 것이 된다.
            label = labels.get(event.event_code)
            if label is None:
                continue
            uri = disclosure_event_uri(event.receipt_no)
            if uri is None:
                continue
            documents.append(
                CorpusDocument(
                    uri=uri,
                    published_on=event.occurred_on,
                    passage=disclosure_event_passage(
                        label=label,
                        receipt_no=event.receipt_no,
                        occurred_on=event.occurred_on,
                    ),
                )
            )
            if len(documents) == _MAX_EVENTS_PER_SYMBOL:
                break
        # 최신 공시가 먼저 오게 한다. 근거 조립기가 domain 하나당 한 건만 쓰므로 그 한 건이
        # 가장 최근 사건이어야 한다.
        documents.sort(key=lambda item: item.published_on, reverse=True)
        return tuple(documents)
