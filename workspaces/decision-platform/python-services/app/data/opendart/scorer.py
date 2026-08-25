from __future__ import annotations

import logging
from datetime import date, timedelta

from app.data.opendart.models import (
    DisclosureRiskEvent,
    DisclosureRiskScoreResult,
    DisclosureRiskWarning,
)
from app.data.opendart.risk_mapping import (
    DisclosureRiskMapping,
    RiskMappingEntry,
    load_default_risk_mapping,
)

logger = logging.getLogger(__name__)
MAX_EVENTS_PER_SCORE = 10_000
MAX_DEFAULT_WINDOW_DAYS = 3_650


def score_disclosure_risk(
    symbol: str,
    events: list[DisclosureRiskEvent],
    *,
    as_of: date,
    mapping: DisclosureRiskMapping | None = None,
    window_days: int = 30,
) -> DisclosureRiskScoreResult:
    """구조화 공시 이벤트를 YAML mapping으로 재현 가능하게 점수화한다.

    window은 이벤트 유형별로 다르다. 부도·회생·비적정 감사의견 같은 상태 지속형은 `effective_window_days`가 길고
    (30일 뒤 조용히 0이 되지 않게), 증자·CB·소송 같은 공시효과형은 짧다. mapping에 없는 유형은 관측성용 warning만 남긴다.
    """
    if not 1 <= window_days <= MAX_DEFAULT_WINDOW_DAYS:
        raise ValueError("window_days must be between 1 and 3650")
    if len(events) > MAX_EVENTS_PER_SCORE:
        raise ValueError("OpenDART disclosure event limit exceeded")
    risk_mapping = mapping or load_default_risk_mapping()
    mapping_windows = [
        entry.effective_window_days
        for entry in risk_mapping.active_by_code.values()
        if entry.effective_window_days is not None
    ]
    if any(not 1 <= value <= MAX_DEFAULT_WINDOW_DAYS for value in mapping_windows):
        raise ValueError("effective_window_days must be between 1 and 3650")
    max_window_days = max([window_days, *mapping_windows])
    try:
        earliest_window_from = as_of - timedelta(days=max_window_days)
    except OverflowError:
        raise ValueError("as_of is too early for the configured disclosure windows") from None
    # 미매핑/blocked 관측성 warning은 유형별 유효기간을 알 수 없으므로 기본 window로 판정한다.
    default_window_from = earliest_window_from + timedelta(days=max_window_days - window_days)
    # 결과 envelope의 window_from은 실제로 고려될 수 있는 가장 오래된 날짜(=최대 유효기간)로 두어 소비자가 오해하지 않게 한다.
    max_effective_days = max(
        [
            entry.effective_window_days or window_days
            for entry in risk_mapping.active_by_code.values()
        ]
        + [window_days]
    )
    contributing: list[tuple[float, DisclosureRiskEvent]] = []
    warnings: list[DisclosureRiskWarning] = []
    seen_events: set[tuple[object, ...]] = set()

    for event in events:
        identity = (
            event.symbol,
            event.corp_code,
            event.event_code,
            event.receipt_no,
            event.occurred_on,
            tuple(sorted(event.attributes.items())),
        )
        if identity in seen_events:
            continue
        seen_events.add(identity)
        if event.symbol != symbol or event.occurred_on > as_of:
            continue
        entry = risk_mapping.active_by_code.get(event.event_code)
        if entry is None:
            if event.occurred_on >= default_window_from:
                warning = _unknown_warning(
                    event, blocked=event.event_code in risk_mapping.blocked_by_code
                )
                warnings.append(warning)
                _log_warning(warning, event)
            continue
        effective_from = as_of - timedelta(days=entry.effective_window_days or window_days)
        if event.occurred_on < effective_from:
            continue
        if entry.condition_field and not _normalize(
            event.attributes.get(entry.condition_field, "")
        ):
            warning = _missing_condition_warning(event)
            warnings.append(warning)
            _log_warning(warning, event)
            continue
        score = _event_score(entry, event)
        if score > 0:
            contributing.append((score, event))

    contributing.sort(
        key=lambda item: (-item[0], item[1].occurred_on, item[1].event_code, item[1].receipt_no)
    )
    return DisclosureRiskScoreResult(
        symbol=symbol,
        as_of=as_of,
        window_from=as_of - timedelta(days=max_effective_days),
        window_to=as_of,
        score=max((score for score, _ in contributing), default=0.0),
        events=[event for _, event in contributing],
        warnings=warnings,
        mapping_version=risk_mapping.version,
    )


def _event_score(entry: RiskMappingEntry, event: DisclosureRiskEvent) -> float:
    if not entry.condition_field:
        return float(entry.score or 0.0)
    value = _normalize(event.attributes.get(entry.condition_field, ""))
    if not value:
        return 0.0
    if any(_normalize(expected) in value for expected in entry.condition_values):
        # 감사의견은 구조화 필드만 본다. 공시 제목/report_nm은 scorer 입력으로 사용하지 않는다.
        return float(entry.score or 0.0)
    return 0.0


def _unknown_warning(event: DisclosureRiskEvent, *, blocked: bool) -> DisclosureRiskWarning:
    if blocked:
        return DisclosureRiskWarning(
            code="BLOCKED_DISCLOSURE_RISK_CODE",
            event_code=event.event_code,
            receipt_no=event.receipt_no,
            message="Disclosure risk mapping is blocked until an official structured source is adopted.",
        )
    return DisclosureRiskWarning(
        code="UNMAPPED_DISCLOSURE_RISK_CODE",
        event_code=event.event_code,
        receipt_no=event.receipt_no,
        message="Disclosure risk event code is not mapped to an active score.",
    )


def _missing_condition_warning(event: DisclosureRiskEvent) -> DisclosureRiskWarning:
    return DisclosureRiskWarning(
        code="INVALID_DISCLOSURE_RISK_CONDITION",
        event_code=event.event_code,
        receipt_no=event.receipt_no,
        message="Disclosure risk condition data is missing; zero score must not be treated as clearance.",
    )


def _log_warning(warning: DisclosureRiskWarning, event: DisclosureRiskEvent) -> None:
    logger.warning(
        warning.code.lower(),
        extra={
            "symbol": event.symbol,
            "corp_code": event.corp_code,
            "event_code": event.event_code,
            "receipt_no": event.receipt_no,
            "occurred_on": event.occurred_on.isoformat(),
        },
    )


def _normalize(value: str) -> str:
    return value.replace(" ", "").strip()
