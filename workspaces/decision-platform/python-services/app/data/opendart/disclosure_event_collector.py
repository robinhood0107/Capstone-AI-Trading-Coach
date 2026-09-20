"""OpenDART 구조화 공시 이벤트를 canonical calendar 이벤트로 적재한다.

## 왜 이것이 없었나

S1.6 이 이 레인을 전부 만들어 뒀다 - 표·투영·역할·SELECT 권한, 위험 매핑 16개 활성 코드와
한국어 라벨, 정규화기(`normalize_opendart_structured_event`), quota 예약·collector lock·
degradation·페이지 원자 게시(`publish_page`), 그리고 endpoint 17개 우선순위 허용목록까지.
빠진 것은 그 사이를 잇는 호출부 하나다. 실측: `normalize_opendart_structured_event` 의
호출부가 0건이고 `calendar_events` 가 0행이다.

그 결과가 뉴스 거부권이 죽은 이유다. 근거 코퍼스가 이 표에서 오는데 표가 비어 있어
`build_public_evidence` 가 항상 0개를 냈고, 모든 세션이 `VERTEX_NO_REGISTERED_EVIDENCE` 로
닫혔고, 2026-09-04 에 거부권이 자문으로 격하됐다.

## 무엇을 새로 만들지 않았나

새 표, 새 역할, 새 정규화기, 새 quota 회계, 새 스케줄러를 만들지 않았다. 이 모듈은 위의
기존 조각을 순서대로 부르는 얇은 층이다. 순수 함수와 부작용을 나눠 뒀으므로 task 조립과
commit 조립은 DB·network 없이 검증된다.

## 호출 예산

종목당 endpoint 17개다(활성 매핑 16개 + 상태 종료 `bnkMngtPcsp`). 완결성 판정이
`disclosure_collection_status_projection` 에서 required operation 전부의 completed cursor 를
요구하므로 한 종목을 반만 훑으면 그 종목은 근거를 내지 못한다. 그래서 종목을 단위로 자르고
`max_symbols_per_run` 이 몇 종목까지 갈지 정한다 - 남은 종목은 다음 실행이 이어받는다.

provider 호출은 전부 런 밖이다. 자동운용 런 안의 provider 상한은 `V100` 이 0~16 으로
못박고 있으므로 근거를 세션 중에 가져올 수 없다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.data.calendar.adapters.opendart_events import normalize_opendart_structured_event
from app.data.calendar.models import (
    CalendarEventSource,
    CalendarEventWrite,
    CalendarObservation,
    CalendarPageCommit,
    CollectionCursor,
    RetentionRule,
    SourceHealthSnapshot,
)
from app.data.calendar.normalizer import (
    build_event_revision,
    canonical_hash,
    event_candidate_from_normalized,
)
from app.data.opendart.models import DisclosureRiskEvent
from app.data.opendart.risk_mapping import load_default_risk_mapping

SOURCE_ID = "opendart-structured-events"
ORIGIN_GROUP = "opendart"
MAPPING_VERSION = "s1.6-opendart-v1"
ADAPTER_VERSION = "opendart-structured-events-collector-v1"
REGISTRY_VERSION = "s1.6-calendar-registry-v1"
CAPABILITY = "DISCLOSURE_EVENT"
# 근거 신선도 창(7일)보다 넓게 훑는다. 창 밖 이벤트는 근거 조립기가 버리지만, 창이 하루
# 밀릴 때마다 다시 수집하지 않아도 되게 여유를 둔다.
WINDOW_CALENDAR_DAYS = 21
_RETENTION_DAYS = 400
_RETENTION_OWNER = "p1-owner"
_STALE_AFTER = timedelta(days=2)
# 상태 종료 endpoint 는 활성 위험 매핑에 라벨이 없지만 완결성 판정이 요구한다.
_STATE_CLOSE_OPERATION = "bnkMngtPcsp"


class DisclosureCollectionError(RuntimeError):
    """공시 이벤트 수집이 계약을 만족하지 못할 때 발생한다."""


@dataclass(frozen=True, slots=True)
class SymbolTarget:
    """한 종목의 수집 대상. corp_code 는 고유번호 조회에서 온 사실이다."""

    symbol: str
    corp_code: str


def collected_operations() -> tuple[str, ...]:
    """이 수집기가 실제로 조회하는 operation 집합.

    출발점은 커밋된 위험 매핑의 활성 `official_endpoint` 다 - 목록을 여기서 새로 만들면
    읽는 쪽(`disclosure_repository`)과 갈라진다. 여기에 상태 종료 operation 을 더한다.

    그리고 **온라인 허용목록과 교집합을 취한다.** `collector.priority_for_operation` 이
    source-controlled 허용목록 밖 endpoint 의 온라인 실행을 client 생성 전에 거부하기
    때문이다. 실측으로 걸러지는 것은 `accnutAdtorNmNdAdtOpinion` 하나다 - 감사의견은
    정기보고서의 구조화 필드이고 이벤트 스트림이 아니라서 허용목록에 없다.

    그래서 `StoredDisclosureBatch.complete` 는 거짓으로 남는다. 거부권에는 그것이 안전한
    방향이다 - 놓친 공시는 "거부하지 못함"(거짓 음성)으로 이어지고, 없는 공시로 매수를
    막는 일(거짓 양성)은 생기지 않는다. 근거가 0개면 검증기가 ABSTAIN 으로 닫는다.
    완결성이 필요한 결정적 위험 점수 경로는 이 수집기의 권한이 아니다.
    """

    from app.data.calendar.collector import priority_for_operation

    mapping = load_default_risk_mapping()
    candidates = {
        entry.official_endpoint
        for entry in mapping.active_by_code.values()
        if entry.official_endpoint is not None
    }
    candidates.add(_STATE_CLOSE_OPERATION)
    allowed: set[str] = set()
    for operation in candidates:
        try:
            priority_for_operation(operation)
        except ValueError:
            continue
        allowed.add(operation)
    if not allowed:
        raise DisclosureCollectionError("OPENDART_NO_ALLOWED_OPERATION")
    return tuple(sorted(allowed))


def collection_window(session_date: date) -> tuple[date, date]:
    return session_date - timedelta(days=WINDOW_CALENDAR_DAYS), session_date


def _observation_id(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def build_page_commit(
    *,
    target: SymbolTarget,
    operation: str,
    events: list[DisclosureRiskEvent],
    window_from: date,
    window_to: date,
    observed_at: datetime,
) -> CalendarPageCommit:
    """한 (종목, operation) 페이지의 관측·이벤트·source link·cursor 를 한 commit 으로 묶는다.

    이벤트가 0개여도 commit 한다. cursor 가 전진하지 않으면 완결성이 영구히 거짓이 되고,
    그러면 공시가 없는 종목이 "아직 안 봤다"와 구별되지 않는다. 그 둘은 다른 사실이다.
    """

    observation_id = _observation_id(
        SOURCE_ID, operation, target.corp_code, window_from, window_to, len(events)
    )
    # 관측 payload 에는 식별자와 개수만 넣는다. 제목·제출인 등 자유 텍스트는 privacy scan 이
    # 거절하고, 이 레인의 권한도 구조화 값까지다.
    sanitized_payload: dict[str, Any] = {
        "corp_code": target.corp_code,
        "endpoint_id": operation,
        "event_count": len(events),
        "symbol": target.symbol,
        "window_from": window_from.isoformat(),
        "window_to": window_to.isoformat(),
    }
    event_writes: list[CalendarEventWrite] = []
    source_links: list[CalendarEventSource] = []
    for event in events:
        if event.symbol != target.symbol:
            raise DisclosureCollectionError("OPENDART_EVENT_SYMBOL_MISMATCH")
        normalized = normalize_opendart_structured_event(
            operation,
            {
                "corp_code": event.corp_code,
                "rcept_no": event.receipt_no,
                "rcept_dt": event.occurred_on.strftime("%Y%m%d"),
            },
            symbol=event.symbol,
        )
        revision = build_event_revision(event_candidate_from_normalized(normalized))
        event_writes.append(
            # 9900 이 표의 상한이다(CHECK 0..9900). 공식 구조화 endpoint 이므로 최대치를 쓴다.
            CalendarEventWrite(revision=revision, confidence_bps=9_900, status="CONFIRMED")
        )
        source_links.append(
            CalendarEventSource(
                event_source_id=_observation_id("link", observation_id, revision.event_id),
                event_id=revision.event_id,
                exchange_mic=None,
                session_date=None,
                observation_id=observation_id,
                source_choice="SINGLE_SOURCE",
                resolution_reason="OFFICIAL_STRUCTURED_ENDPOINT",
                # 불투명 참조다. 원 응답도 URL 도 남기지 않는다.
                opaque_source_ref=_observation_id("ref", SOURCE_ID, operation, event.receipt_no),
            )
        )
    return CalendarPageCommit(
        observation=CalendarObservation(
            observation_id=observation_id,
            source_id=SOURCE_ID,
            origin_group=ORIGIN_GROUP,
            capability=CAPABILITY,
            effective_from=window_from,
            effective_to=window_to,
            observed_at=observed_at,
            ingested_at=observed_at,
            sanitized_payload=sanitized_payload,
            sanitized_payload_hash=canonical_hash(sanitized_payload),
            adapter_version=ADAPTER_VERSION,
            mapping_version=MAPPING_VERSION,
            registry_version=REGISTRY_VERSION,
        ),
        cursor=CollectionCursor(
            source_id=SOURCE_ID,
            operation=operation,
            subject=target.corp_code,
            window_from=window_from,
            window_to=window_to,
            mapping_version=MAPPING_VERSION,
            next_page=1,
            continuation=None,
            completed=True,
        ),
        source_health=SourceHealthSnapshot(
            source_id=SOURCE_ID,
            last_success_at=observed_at,
            last_failure_at=None,
            failure_count=0,
            stale_after=_STALE_AFTER,
            network_ready=True,
            # 표의 CHECK 는 `^[A-Z][A-Z0-9_]{0,63}$` 다. OpenDART 의 응답 코드 '000' 을 그대로
            # 넣으면 거부된다 - source health 는 provider 코드가 아니라 우리 판정이다.
            status_code="HEALTHY",
            error_code=None,
        ),
        persistence_mode="ONLINE_PERSISTENT",
        retention=RetentionRule(days=_RETENTION_DAYS, owner=_RETENTION_OWNER),
        event_writes=tuple(event_writes),
        source_links=tuple(source_links),
    )


@dataclass(frozen=True, slots=True)
class PlannedFetch:
    """한 (종목, operation) 조회. `send` 는 provider 를 부르는 호출부다."""

    target: SymbolTarget
    operation: str
    send: Callable[[], list[DisclosureRiskEvent]]


def plan_fetches(
    *,
    targets: list[SymbolTarget],
    fetch: Callable[[SymbolTarget, str], list[DisclosureRiskEvent]],
) -> list[PlannedFetch]:
    """종목 x operation 조회를 결정적 순서로 만든다.

    round-robin 과 종목 상한은 `CalendarCollector` 가 소유하므로 여기서 다시 자르지 않는다.
    """

    operations = collected_operations()
    plans: list[PlannedFetch] = []

    def bind(
        bound_target: SymbolTarget, bound_operation: str
    ) -> Callable[[], list[DisclosureRiskEvent]]:
        # 루프 변수를 닫아 두면 마지막 값만 남는다. 명시적으로 묶는다.
        return lambda: fetch(bound_target, bound_operation)

    for target in sorted(targets, key=lambda item: item.symbol):
        for operation in operations:
            plans.append(
                PlannedFetch(target=target, operation=operation, send=bind(target, operation))
            )
    return plans


def utc_now() -> datetime:
    return datetime.now(UTC)
