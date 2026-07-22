from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.data.calendar.merger import merge_trading_session
from app.data.calendar.models import KASIReason, KISHolidayObservation, PriorCanonicalSession, XKRXSession


NOW = datetime(2026, 7, 22, 1, 0, tzinfo=UTC)


def test_kis_opnd_yn_wins_and_lower_authority_disagreement_becomes_conflict() -> None:
    canonical = merge_trading_session(
        _xkrx(is_open=True),
        kis=_kis(is_open=False),
        kasi_reasons=[],
        prior=None,
        now=NOW,
    )

    assert canonical.is_open is False
    assert canonical.chosen_source_id == "kis-holiday-ctca0903r"
    assert canonical.has_conflict is True
    assert canonical.confidence_bps == 7000
    assert canonical.degraded is False
    assert canonical.open_at is None
    assert canonical.close_at is None


def test_fresh_prior_closed_session_never_keeps_open_market_timestamps() -> None:
    prior = PriorCanonicalSession(
        session=_canonical_prior(is_open=False),
        expires_at=NOW + timedelta(hours=1),
    )

    canonical = merge_trading_session(
        _xkrx(is_open=True),
        kis=None,
        kasi_reasons=[],
        prior=prior,
        now=NOW,
        kis_failure_code="TRANSPORT_UNAVAILABLE",
    )

    assert canonical.is_open is False
    assert canonical.open_at is None
    assert canonical.close_at is None


def test_kasi_changes_reason_only_and_never_market_status() -> None:
    canonical = merge_trading_session(
        _xkrx(is_open=True),
        kis=_kis(is_open=True),
        kasi_reasons=[KASIReason(day=date(2026, 7, 22), reason="fixture reason")],
        prior=None,
        now=NOW,
    )

    assert canonical.is_open is True
    assert canonical.reason == "fixture reason"
    assert canonical.open_at == _xkrx().open_at
    assert canonical.close_at == _xkrx().close_at


def test_kis_failure_prefers_fresh_nonconflicted_prior_then_xkrx() -> None:
    prior = PriorCanonicalSession(
        session=_canonical_prior(is_open=False),
        expires_at=NOW + timedelta(hours=1),
    )
    from_prior = merge_trading_session(
        _xkrx(is_open=True),
        kis=None,
        kasi_reasons=[],
        prior=prior,
        now=NOW,
        kis_failure_code="TRANSPORT_UNAVAILABLE",
    )
    assert from_prior.is_open is False
    assert from_prior.degraded is True
    assert from_prior.fallback_reason == "KIS_TRANSPORT_UNAVAILABLE_PRIOR_CANONICAL"

    expired = PriorCanonicalSession(session=prior.session, expires_at=NOW - timedelta(seconds=1))
    from_xkrx = merge_trading_session(
        _xkrx(is_open=True),
        kis=None,
        kasi_reasons=[],
        prior=expired,
        now=NOW,
        kis_failure_code="TRANSPORT_UNAVAILABLE",
    )
    assert from_xkrx.is_open is True
    assert from_xkrx.degraded is True
    assert from_xkrx.fallback_reason == "KIS_TRANSPORT_UNAVAILABLE_XKRX_BASE"


def test_merge_is_order_independent_and_hash_deterministic() -> None:
    first = merge_trading_session(
        _xkrx(),
        kis=_kis(),
        kasi_reasons=[
            KASIReason(date(2026, 7, 22), "B"),
            KASIReason(date(2026, 7, 22), "A"),
        ],
        prior=None,
        now=NOW,
    )
    second = merge_trading_session(
        _xkrx(),
        kis=_kis(),
        kasi_reasons=[
            KASIReason(date(2026, 7, 22), "A"),
            KASIReason(date(2026, 7, 22), "B"),
        ],
        prior=None,
        now=NOW,
    )
    assert first == second
    assert len(first.canonical_hash) == 64


def _xkrx(*, is_open: bool = True) -> XKRXSession:
    return XKRXSession.fixture(
        session_date=date(2026, 7, 22),
        is_open=is_open,
        open_at=datetime(2026, 7, 22, 0, 0, tzinfo=UTC) if is_open else None,
        close_at=datetime(2026, 7, 22, 6, 30, tzinfo=UTC) if is_open else None,
    )


def _kis(*, is_open: bool = True) -> KISHolidayObservation:
    return KISHolidayObservation(
        day=date(2026, 7, 22),
        is_open=is_open,
        business_day_flag=not is_open,
        trading_day_flag=not is_open,
        settlement_day_flag=True,
        source_id="kis-holiday-ctca0903r",
        origin_group="kis",
        tier=1,
        tr_id="CTCA0903R",
    )


def _canonical_prior(*, is_open: bool) -> object:
    return merge_trading_session(
        _xkrx(is_open=is_open),
        kis=_kis(is_open=is_open),
        kasi_reasons=[],
        prior=None,
        now=NOW - timedelta(hours=1),
    )
