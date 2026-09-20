"""공시 구조화 이벤트 수집기 회귀.

무엇을 지키려는 테스트인가. 이 수집기가 뉴스 거부권의 근거를 만든다. 세 성질이 어긋나면
거부권은 조용히 죽는다 - 정확히 지금까지의 상태다.

  1. required operation 집합이 완결성 판정과 같은 곳에서 유도된다. 두 곳이 갈라지면
     `disclosure_collection_status_projection` 의 완결성이 영구히 거짓이 되고 근거가 0개다.
  2. 이벤트가 0개인 페이지도 cursor 를 전진시킨다. 그러지 않으면 "공시가 없는 종목"과
     "아직 안 본 종목"이 구별되지 않는다.
  3. commit 이 표의 CHECK 안에 든다. `confidence_bps` 상한 9900 과 `status` 열거값을
     넘기면 페이지가 통째로 롤백된다.

DB 도 network 도 쓰지 않는다. provider 응답은 파싱된 값 객체로 넣는다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.data.opendart.disclosure_event_collector import (
    DisclosureCollectionError,
    SymbolTarget,
    build_page_commit,
    collection_window,
    plan_fetches,
    collected_operations,
)
from app.data.opendart.models import DisclosureRiskEvent
from app.data.opendart.risk_mapping import load_default_risk_mapping

_TARGET = SymbolTarget(symbol="000660", corp_code="00164779")
_OBSERVED = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _event(receipt_no: str = "20260907000001") -> DisclosureRiskEvent:
    return DisclosureRiskEvent(
        symbol="000660",
        corp_code="00164779",
        event_code="OPENDART:cvbdIsDecsn",
        receipt_no=receipt_no,
        occurred_on=date(2026, 9, 7),
    )


def test_collected_operations_are_derived_from_the_committed_mapping() -> None:
    """읽는 쪽(`disclosure_repository`)과 같은 표현에서 출발한다.

    목록을 이 모듈이 새로 만들면 두 곳이 갈라지고 완결성이 영구히 거짓이 된다.
    """

    mapping = load_default_risk_mapping()
    candidates = {
        entry.official_endpoint
        for entry in mapping.active_by_code.values()
        if entry.official_endpoint is not None
    }
    # 상태 종료 operation 은 활성 라벨이 없지만 완결성 판정이 요구한다.
    candidates.add("bnkMngtPcsp")

    operations = collected_operations()

    assert set(operations) <= candidates
    assert operations == tuple(sorted(operations))
    assert len(operations) >= 16


def test_every_collected_operation_is_on_the_online_allowlist() -> None:
    """허용목록 밖 operation 은 client 생성 전에 거부되므로 실행이 통째로 죽는다."""

    from app.data.calendar.collector import priority_for_operation

    for operation in collected_operations():
        priority_for_operation(operation)


def test_the_audit_opinion_endpoint_is_the_only_one_left_out() -> None:
    """무엇이 빠지는지를 이름으로 고정한다.

    감사의견은 정기보고서의 구조화 필드이고 이벤트 스트림이 아니라서 온라인 허용목록에
    없다. 그래서 `StoredDisclosureBatch.complete` 는 거짓으로 남는다 - 거부권에는 그것이
    안전한 방향이다(놓친 공시는 거짓 음성이고, 없는 공시로 매수를 막는 일은 없다).
    다른 endpoint 가 조용히 빠지기 시작하면 여기서 막힌다.
    """

    mapping = load_default_risk_mapping()
    candidates = {
        entry.official_endpoint
        for entry in mapping.active_by_code.values()
        if entry.official_endpoint is not None
    }
    candidates.add("bnkMngtPcsp")

    assert candidates - set(collected_operations()) == {"accnutAdtorNmNdAdtOpinion"}


def test_the_read_window_covers_more_than_the_freshness_window() -> None:
    window_from, window_to = collection_window(date(2026, 9, 7))

    assert window_to == date(2026, 9, 7)
    assert (window_to - window_from).days >= 7


def test_a_page_without_events_still_advances_the_cursor() -> None:
    commit = build_page_commit(
        target=_TARGET,
        operation="cvbdIsDecsn",
        events=[],
        window_from=date(2026, 8, 17),
        window_to=date(2026, 9, 7),
        observed_at=_OBSERVED,
    )

    assert commit.cursor.completed is True
    assert commit.cursor.operation == "cvbdIsDecsn"
    assert commit.cursor.subject == "00164779"
    assert commit.event_writes == ()
    assert commit.source_links == ()
    assert commit.observation.sanitized_payload["event_count"] == 0


def test_a_page_with_an_event_writes_the_event_and_its_source_link() -> None:
    commit = build_page_commit(
        target=_TARGET,
        operation="cvbdIsDecsn",
        events=[_event()],
        window_from=date(2026, 8, 17),
        window_to=date(2026, 9, 7),
        observed_at=_OBSERVED,
    )

    (write,) = commit.event_writes
    (link,) = commit.source_links
    assert write.revision.candidate.symbol == "000660"
    assert write.revision.candidate.event_date == date(2026, 9, 7)
    assert write.revision.candidate.source_event_key == "20260907000001"
    # 표의 CHECK 안에 있어야 한다. 넘기면 페이지가 통째로 롤백된다.
    assert 0 <= write.confidence_bps <= 9_900
    assert write.status == "CONFIRMED"
    # source link 는 페이지 관측을 가리켜야 한다(`_validate_page_commit`).
    assert link.observation_id == commit.observation.observation_id
    assert link.event_id == write.revision.event_id
    # 불투명 참조는 64-hex 다. 투영이 그 형태를 요구한다.
    assert len(link.opaque_source_ref) == 64
    assert all(character in "0123456789abcdef" for character in link.opaque_source_ref)


def test_the_observation_payload_carries_no_free_text() -> None:
    """제목·제출인 같은 자유 텍스트는 이 레인의 권한 밖이고 privacy scan 이 거절한다."""

    commit = build_page_commit(
        target=_TARGET,
        operation="cvbdIsDecsn",
        events=[_event()],
        window_from=date(2026, 8, 17),
        window_to=date(2026, 9, 7),
        observed_at=_OBSERVED,
    )

    assert set(commit.observation.sanitized_payload) == {
        "corp_code",
        "endpoint_id",
        "event_count",
        "symbol",
        "window_from",
        "window_to",
    }


def test_an_event_for_another_symbol_is_refused() -> None:
    other = SymbolTarget(symbol="005930", corp_code="00126380")

    with pytest.raises(DisclosureCollectionError, match="SYMBOL_MISMATCH"):
        build_page_commit(
            target=other,
            operation="cvbdIsDecsn",
            events=[_event()],
            window_from=date(2026, 8, 17),
            window_to=date(2026, 9, 7),
            observed_at=_OBSERVED,
        )


def test_planned_fetches_bind_each_symbol_and_operation_exactly_once() -> None:
    """루프 변수를 닫아 두면 마지막 값만 남는다. 그 실수를 고정한다."""

    targets = [_TARGET, SymbolTarget(symbol="005930", corp_code="00126380")]
    seen: list[tuple[str, str]] = []

    def fetch(target: SymbolTarget, operation: str) -> list[DisclosureRiskEvent]:
        seen.append((target.symbol, operation))
        return []

    plans = plan_fetches(targets=targets, fetch=fetch)
    for plan in plans:
        plan.send()

    operations = collected_operations()
    assert len(plans) == len(targets) * len(operations)
    assert sorted(seen) == sorted(
        (target.symbol, operation) for target in targets for operation in operations
    )
    # 종목 순서는 결정적이다.
    assert [plan.target.symbol for plan in plans][: len(operations)] == ["000660"] * len(operations)
