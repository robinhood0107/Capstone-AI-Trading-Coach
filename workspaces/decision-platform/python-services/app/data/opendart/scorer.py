from __future__ import annotations

import logging
from datetime import date, timedelta

from app.data.opendart.models import (
    DisclosureRiskEvent,
    DisclosureRiskScoreResult,
    DisclosureRiskWarning,
)
from app.data.opendart.risk_mapping import DisclosureRiskMapping, RiskMappingEntry, load_default_risk_mapping

logger = logging.getLogger(__name__)


def score_disclosure_risk(
    symbol: str,
    events: list[DisclosureRiskEvent],
    *,
    as_of: date,
    mapping: DisclosureRiskMapping | None = None,
    window_days: int = 30,
) -> DisclosureRiskScoreResult:
    """최근 window 안의 구조화 공시 이벤트를 YAML mapping으로 재현 가능하게 점수화한다."""
    risk_mapping = mapping or load_default_risk_mapping()
    window_from = as_of - timedelta(days=window_days)
    contributing: list[tuple[float, DisclosureRiskEvent]] = []
    warnings: list[DisclosureRiskWarning] = []

    for event in events:
        if event.symbol != symbol or not window_from <= event.occurred_on <= as_of:
            continue
        entry = risk_mapping.active_by_code.get(event.event_code)
        if entry is None:
            warning = _unknown_warning(event, blocked=event.event_code in risk_mapping.blocked_by_code)
            warnings.append(warning)
            _log_warning(warning, event)
            continue
        score = _event_score(entry, event)
        if score > 0:
            contributing.append((score, event))

    contributing.sort(key=lambda item: (-item[0], item[1].occurred_on, item[1].event_code, item[1].receipt_no))
    return DisclosureRiskScoreResult(
        symbol=symbol,
        as_of=as_of,
        window_from=window_from,
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
