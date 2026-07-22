from __future__ import annotations

import hashlib
from datetime import datetime

from app.data.calendar.confidence import confidence_bps
from app.data.calendar.models import (
    CalendarConflict,
    CanonicalTradingSession,
    KASIReason,
    KISHolidayObservation,
    PriorCanonicalSession,
    XKRXSession,
)
from app.data.calendar.normalizer import canonical_hash
from app.data.calendar.privacy import assert_sanitized_payload


def merge_trading_session(
    xkrx: XKRXSession,
    *,
    kis: KISHolidayObservation | None,
    kasi_reasons: list[KASIReason],
    prior: PriorCanonicalSession | None,
    now: datetime,
    kis_failure_code: str | None = None,
) -> CanonicalTradingSession:
    """S1.6 field authority를 먼저 적용하고 lower-tier 불일치는 conflict로만 기록한다."""
    if kis is not None and kis.day != xkrx.session_date:
        raise ValueError("KIS observation date does not match XKRX session date")
    if any(reason.day != xkrx.session_date for reason in kasi_reasons):
        raise ValueError("KASI reason date does not match session date")

    conflicts: list[CalendarConflict] = []
    agreeing_origins: set[str] = set()
    degraded = False
    fallback_reason: str | None = None

    if kis is not None:
        is_open = kis.is_open
        chosen_source_id = kis.source_id
        chosen_tier = kis.tier
        open_at = xkrx.open_at
        close_at = xkrx.close_at
        timezone = xkrx.timezone
        if xkrx.is_open == is_open:
            agreeing_origins.add(xkrx.origin_group)
        else:
            conflicts.append(
                CalendarConflict(
                    field_name="is_open",
                    chosen_value=is_open,
                    competing_value=xkrx.is_open,
                    chosen_source_id=kis.source_id,
                    competing_source_id=xkrx.source_id,
                    resolution_rule="KIS_OPND_YN_OVER_XKRX_BASE",
                )
            )
    elif _usable_prior(prior, xkrx=xkrx, now=now):
        assert prior is not None
        is_open = prior.session.is_open
        chosen_source_id = prior.session.chosen_source_id
        chosen_tier = _tier_for_source(chosen_source_id)
        open_at = prior.session.open_at
        close_at = prior.session.close_at
        timezone = prior.session.timezone
        degraded = True
        fallback_reason = f"KIS_{_failure_code(kis_failure_code)}_PRIOR_CANONICAL"
    else:
        is_open = xkrx.is_open
        chosen_source_id = xkrx.source_id
        chosen_tier = xkrx.tier
        open_at = xkrx.open_at
        close_at = xkrx.close_at
        timezone = xkrx.timezone
        degraded = kis_failure_code is not None
        if degraded:
            fallback_reason = f"KIS_{_failure_code(kis_failure_code)}_XKRX_BASE"

    if not is_open:
        # KIS 또는 fresh prior가 휴장을 확정하면 XKRX regular-session 시각을 canonical에 남기지 않는다.
        open_at = None
        close_at = None

    reasons = sorted({item.reason.strip() for item in kasi_reasons if item.reason.strip()})
    if reasons:
        reason = " / ".join(reasons)
    elif prior is not None and _usable_prior(prior, xkrx=xkrx, now=now) and prior.session.reason:
        reason = prior.session.reason
    else:
        reason = "REGULAR_SESSION" if is_open else "CLOSED_SESSION"

    has_conflict = bool(conflicts)
    confidence = confidence_bps(
        tier=chosen_tier,
        agreeing_origin_groups=agreeing_origins,
        has_conflict=has_conflict,
    )
    source_ids = {xkrx.source_id}
    if kis is not None:
        source_ids.add(kis.source_id)
    source_ids.update("kasi-rest-de-info" for _ in reasons)
    source_refs = tuple(sorted(_opaque_source_ref(source_id, xkrx.session_date.isoformat()) for source_id in source_ids))
    conflict_rows = tuple(sorted(conflicts, key=lambda item: (item.field_name, item.competing_source_id)))
    projection = {
        "exchange_mic": "XKRX",
        "session_date": xkrx.session_date,
        "is_open": is_open,
        "open_at": open_at,
        "close_at": close_at,
        "timezone": timezone,
        "reason": reason,
        "chosen_source_id": chosen_source_id,
        "degraded": degraded,
        "fallback_reason": fallback_reason,
        "as_of": now,
        "confidence_bps": confidence,
        "has_conflict": has_conflict,
        "conflicts": [
            {
                "field_name": conflict.field_name,
                "chosen_value": conflict.chosen_value,
                "competing_value": conflict.competing_value,
                "chosen_source_id": conflict.chosen_source_id,
                "competing_source_id": conflict.competing_source_id,
                "resolution_rule": conflict.resolution_rule,
            }
            for conflict in conflict_rows
        ],
        "source_refs": source_refs,
        "canonical_rule_version": "s1.6-session-v1",
        "confidence_rule_version": "s1.6-confidence-v1",
    }
    # 외부 reason과 source metadata는 raw/secret marker가 hash에 들어가기 전에 차단한다.
    assert_sanitized_payload(projection)
    return CanonicalTradingSession(
        exchange_mic="XKRX",
        session_date=xkrx.session_date,
        is_open=is_open,
        open_at=open_at,
        close_at=close_at,
        timezone=timezone,
        reason=reason,
        chosen_source_id=chosen_source_id,
        degraded=degraded,
        fallback_reason=fallback_reason,
        as_of=now,
        confidence_bps=confidence,
        has_conflict=has_conflict,
        conflicts=conflict_rows,
        source_refs=source_refs,
        canonical_hash=canonical_hash(projection),
    )


def _usable_prior(
    prior: PriorCanonicalSession | None,
    *,
    xkrx: XKRXSession,
    now: datetime,
) -> bool:
    return bool(
        prior is not None
        and prior.session.exchange_mic == "XKRX"
        and prior.session.session_date == xkrx.session_date
        and prior.healthy
        and not prior.session.has_conflict
        and now <= prior.expires_at
    )


def _failure_code(value: str | None) -> str:
    if value and value.replace("_", "").isalnum():
        return value.upper()
    return "UNAVAILABLE"


def _tier_for_source(source_id: str) -> int:
    if source_id.startswith("kis-"):
        return 1
    if source_id.startswith("xkrx-"):
        return 2
    return 4


def _opaque_source_ref(source_id: str, stable_key: str) -> str:
    return hashlib.sha256(f"s1.6-source-ref\0{source_id}\0{stable_key}".encode()).hexdigest()
