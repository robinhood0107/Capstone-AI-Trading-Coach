"""공시 근거 코퍼스 투영 회귀.

무엇을 지키려는 테스트인가. 이 투영이 거부권의 근거를 만든다. 근거가 하나라도 잘못 만들어지면
모델이 없는 사실을 인용하거나, 반대로 근거가 0개가 되어 모든 세션이 조용히 ABSTAIN 한다.
후자가 정확히 2026-09-04 에 거부권을 자문으로 격하시킨 상태다.

  1. 활성 매핑에 라벨이 있는 코드만 근거가 된다 - `blocked` 코드로 인용을 만들면 "그 코드는
     안정적으로 분류되지 않는다"는 레포의 판단을 우회한다.
  2. URI 는 14자리 접수번호로만 만든다 - 등록 domain 판정을 통과해야 하고, 접수번호가
     아닌 값으로 주소를 만들면 존재하지 않는 문서를 가리킨다.
  3. 인용문은 공식 구조화 값만 담고 인용 경계 안에 든다 - 실측에서 공시 제목 58개 중
     45개가 20자 미만이었다. 그래서 제목이 아니라 라벨·접수번호·접수일을 쓴다.
  4. 최신 공시가 먼저 온다 - 근거 조립기가 domain 하나당 한 건만 쓰므로 그 한 건이 가장
     최근 사건이어야 한다.

DB 도 network 도 쓰지 않는다. loader 는 Protocol 이고 여기서 가짜를 넣는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.data._shared.repository_root import repository_artifact
from app.data.opendart.risk_mapping import load_default_risk_mapping
from app.p1_owner.disclosure_corpus import (
    DisclosureEventCorpusDocumentSource,
    disclosure_event_passage,
    disclosure_event_uri,
)
from app.p1_owner.vertex_corpus_evidence import build_public_evidence
from app.p1_owner import vertex_source_registry
from app.p1_owner.vertex_source_registry import registered_source_for_uri

_SESSION = date(2026, 9, 7)


@dataclass(frozen=True)
class _Event:
    event_code: str
    receipt_no: str
    occurred_on: date


@dataclass(frozen=True)
class _Batch:
    events: tuple[_Event, ...]


@dataclass(frozen=True)
class _Loader:
    batch: _Batch
    calls: list[tuple[str, date, date]]

    def load(
        self,
        *,
        symbol: str,
        corp_code: str | None,
        window_from: date,
        window_to: date,
    ) -> _Batch:
        del corp_code
        self.calls.append((symbol, window_from, window_to))
        return self.batch


def _source(*events: _Event) -> tuple[DisclosureEventCorpusDocumentSource, list[object]]:
    calls: list[tuple[str, date, date]] = []
    return DisclosureEventCorpusDocumentSource(_Loader(_Batch(events), calls)), calls


def test_uri_is_built_only_from_a_14_digit_receipt_number() -> None:
    assert disclosure_event_uri("20260907000123") == (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260907000123"
    )
    assert disclosure_event_uri("2026090700012") is None
    assert disclosure_event_uri("2026090700012a") is None
    assert disclosure_event_uri("") is None


def test_the_built_uri_is_a_registered_official_primary_source() -> None:
    """이 판정이 실패하면 근거가 조용히 0개가 된다."""

    uri = disclosure_event_uri("20260907000123")
    assert uri is not None

    assert registered_source_for_uri(uri) == ("src_official_dart", "OFFICIAL_PRIMARY")


def test_every_active_label_produces_a_quote_inside_the_bounds() -> None:
    """가장 짧은 라벨도 하한 20자를 넘고 가장 긴 라벨도 상한 240자 안에 든다."""

    for entry in load_default_risk_mapping().active_by_code.values():
        passage = disclosure_event_passage(
            label=entry.label, receipt_no="20260907000123", occurred_on=_SESSION
        )
        assert 20 <= len(passage) <= 240, (entry.label, len(passage))


def test_only_codes_with_an_active_label_become_evidence() -> None:
    source, _ = _source(
        _Event("OPENDART:cvbdIsDecsn", "20260907000001", _SESSION),
        # blocked 코드. 라벨은 있지만 활성이 아니다.
        _Event("OPENDART:KRX_UNFAITHFUL_DISCLOSURE", "20260907000002", _SESSION),
        # 매핑에 아예 없는 코드.
        _Event("OPENDART:unknownEndpoint", "20260907000003", _SESSION),
    )

    documents = source.documents(symbol="000660", session_date=_SESSION)

    assert len(documents) == 1
    assert "20260907000001" in documents[0].uri
    assert "전환사채권 발행결정" in documents[0].passage


def test_the_newest_disclosure_comes_first() -> None:
    source, _ = _source(
        _Event("OPENDART:dfOcr", "20260901000001", date(2026, 9, 1)),
        _Event("OPENDART:lwstLg", "20260904000001", date(2026, 9, 4)),
        _Event("OPENDART:crDecsn", "20260907000001", _SESSION),
    )

    documents = source.documents(symbol="000660", session_date=_SESSION)

    assert [document.published_on for document in documents] == [
        date(2026, 9, 7),
        date(2026, 9, 4),
        date(2026, 9, 1),
    ]


def test_the_read_window_matches_the_freshness_window() -> None:
    source, calls = _source(_Event("OPENDART:dfOcr", "20260907000001", _SESSION))

    source.documents(symbol="000660", session_date=_SESSION)

    assert calls == [("000660", date(2026, 8, 31), _SESSION)]


def test_a_projected_document_survives_the_evidence_builder_end_to_end() -> None:
    """투영과 근거 조립기가 실제로 이어지는지 본다.

    둘 중 하나만 맞아도 근거는 0개다. 등록 판정·신선도·인용 경계를 모두 통과해
    `publicEvidence` 한 항목이 되는 것까지 확인한다.
    """

    source, _ = _source(_Event("OPENDART:cmpMgDecsn", "20260904000007", date(2026, 9, 4)))

    evidence = build_public_evidence(source, symbol="000660", session_date=_SESSION)

    assert len(evidence) == 1
    assert evidence[0]["sourceId"] == "src_official_dart"
    assert evidence[0]["sourceType"] == "OFFICIAL_PRIMARY"
    assert evidence[0]["sourceEventDate"] == "2026-09-04"
    assert "회사합병 결정" in str(evidence[0]["boundedQuote"])


def test_a_disclosure_outside_the_freshness_window_yields_no_evidence() -> None:
    """읽기 창을 넓혀도 조립기가 버린다. 두 층이 같은 값을 쓰는지 고정한다."""

    source, _ = _source(_Event("OPENDART:dfOcr", "20260820000001", date(2026, 8, 20)))

    assert build_public_evidence(source, symbol="000660", session_date=_SESSION) == []


def test_no_events_means_no_documents() -> None:
    source, _ = _source()

    assert source.documents(symbol="000660", session_date=_SESSION) == ()


def test_the_registered_source_catalog_resolves_from_any_layout(tmp_path: Path) -> None:
    """등록 출처 카탈로그를 깊이 상수로 찾지 않는다.

    실측: 컨테이너에서 `repository_root(__file__, 5)` 가 `/app/app/p1_owner` 를 가리켜
    카탈로그를 찾지 못하고 `VertexSourceRegistryError` 를 던졌다. 근거가 항상 0개였던 동안
    그 줄이 실행되지 않아 잠들어 있던 버그다 - 코퍼스를 구현하는 순간 발화한다.

    같은 부류를 공용 해석기로 닫았으므로, 그 해석기가 리포 체크아웃 밖의 배치에서도
    산출물을 찾고 없으면 None 을 돌려주는지 본다.
    """

    relative = "contracts/catalogs/p1-vertex-news-sources.v1.json"
    # 리포 체크아웃에서는 찾는다.
    assert repository_artifact(vertex_source_registry.__file__, relative) is not None
    # 이미지처럼 깊이가 다른 배치에서도 찾는다.
    (tmp_path / "app/pkg").mkdir(parents=True)
    (tmp_path / "contracts/catalogs").mkdir(parents=True)
    (tmp_path / relative).write_text("{}", encoding="utf-8")
    module = tmp_path / "app/pkg/module.py"
    module.write_text("", encoding="utf-8")
    assert repository_artifact(str(module), relative) == tmp_path / relative
    # 없으면 잘못된 경로를 만들지 않고 None 을 돌려준다.
    assert repository_artifact(str(module), "contracts/catalogs/absent.json") is None


def test_the_registry_reads_the_catalog_and_knows_the_dart_domain() -> None:
    """해석기를 지난 뒤 실제로 카탈로그가 읽히고 DART 가 등록 출처로 나오는지 본다."""

    assert registered_source_for_uri(
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260907000001"
    ) == (
        "src_official_dart",
        "OFFICIAL_PRIMARY",
    )
