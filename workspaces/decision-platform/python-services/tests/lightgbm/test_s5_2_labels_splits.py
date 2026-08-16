from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.features import PriceEvidence
from app.lightgbm.labels import (
    CLASS_ORDER,
    LabelRow,
    build_exact_labels,
    classify_forward_return,
    zero_fill_features,
)
from app.lightgbm.walk_forward import (
    UntouchedTestLoader,
    build_walk_forward_plan,
    split_visualization,
)


def _price(
    session: date,
    adjusted_open: float | None,
    *,
    instrument_id: str = "instrument-005930",
) -> PriceEvidence:
    return PriceEvidence(
        instrument_id=instrument_id,
        symbol="005930",
        session_date=session,
        adjusted_open=adjusted_open,
        adjusted_close=100.0,
        volume=1_000.0,
        available_at=datetime(2026, 8, 15, tzinfo=UTC),
        source_revision="r1",
        source_sha256="a" * 64,
    )


def test_exact_label_uses_t1_t6_and_tau_equality_is_hold() -> None:
    sessions = [date(2026, 1, 1) + timedelta(days=index) for index in range(8)]
    prices = [_price(session, 100.0) for session in sessions]
    prices[6] = _price(sessions[6], 101.0)
    labels = build_exact_labels(prices)

    assert labels[0].interval_start == sessions[1]
    assert labels[0].interval_end == sessions[6]
    assert labels[0].forward_return == pytest.approx(0.01)
    assert labels[0].label == CLASS_ORDER["BUY"]
    assert classify_forward_return(-0.006) == CLASS_ORDER["HOLD"]
    assert classify_forward_return(0.006) == CLASS_ORDER["HOLD"]
    assert classify_forward_return(np.nextafter(-0.006, -1.0)) == CLASS_ORDER["SELL"]
    assert classify_forward_return(np.nextafter(0.006, 1.0)) == CLASS_ORDER["BUY"]


def test_missing_t1_or_t6_open_drops_row_and_zero_fill_does_not_forward_read() -> None:
    sessions = [date(2026, 1, 1) + timedelta(days=index) for index in range(8)]
    t1_missing = [
        _price(session, None if index == 1 else 100.0) for index, session in enumerate(sessions)
    ]
    t6_missing = [
        _price(session, None if index == 6 else 100.0) for index, session in enumerate(sessions)
    ]
    assert all(row.session_date != sessions[0] for row in build_exact_labels(t1_missing))
    assert all(row.session_date != sessions[0] for row in build_exact_labels(t6_missing))
    assert zero_fill_features({"a": None, "b": 1.25}) == {
        "a": np.float32(0.0),
        "b": np.float32(1.25),
    }


def test_label_join_uses_permanent_identity_even_when_symbols_are_reused() -> None:
    first_sessions = [date(2026, 1, 1) + timedelta(days=index) for index in range(7)]
    second_sessions = [date(2026, 1, 8) + timedelta(days=index) for index in range(7)]
    first = [_price(session, 100.0, instrument_id="identity-a") for session in first_sessions]
    second = [_price(session, 200.0, instrument_id="identity-b") for session in second_sessions]

    labels = build_exact_labels([*first, *second])

    assert len(labels) == 2
    assert sorted(row.forward_return for row in labels) == [0.0, 0.0]


def _split_fixture() -> tuple[tuple[date, ...], tuple[LabelRow, ...]]:
    sessions = tuple(date(2020, 1, 1) + timedelta(days=index) for index in range(1_007))
    labels = tuple(
        LabelRow(
            symbol="005930",
            session_date=sessions[index],
            interval_start=sessions[index + 1],
            interval_end=sessions[index + 6],
            forward_return=0.0,
            label=CLASS_ORDER["HOLD"],
        )
        for index in range(1_001)
    )
    return sessions, labels


def test_expanding_walk_forward_exact_blocks_purge_then_embargo_and_overlap_zero() -> None:
    sessions, labels = _split_fixture()
    plan = build_walk_forward_plan(sessions, labels)

    assert len(plan.folds) == 3
    expected_history_ends = (504, 614, 724)
    for split, history_end in zip(plan.folds, expected_history_ends, strict=True):
        assert len(split.fit_sessions) == history_end - 1
        assert split.purged_sessions == (sessions[history_end - 1],)
        assert len(split.embargo_sessions) == 5
        assert len(split.early_sessions) == 21
        assert len(split.calibration_sessions) == 21
        assert len(split.evaluation_sessions) == 63
    assert len(plan.final.fit_sessions) == 833
    assert plan.final.purged_sessions == (sessions[833],)
    assert len(plan.final.embargo_sessions) == 5
    assert len(plan.final.early_sessions) == 21
    assert len(plan.final.calibration_sessions) == 21
    assert len(plan.final.evaluation_sessions) == 126
    assert plan.final.evaluation_sessions[-1] == sessions[-1]


def test_split_visualization_has_no_outcome_and_final_loader_is_exactly_once() -> None:
    sessions, labels = _split_fixture()
    visualization = split_visualization(build_walk_forward_plan(sessions, labels))
    serialized = repr(visualization).lower()
    assert "outcome" not in serialized
    assert "metric" not in serialized
    assert "[open(t+1),open(t+6)]" in serialized

    loader = UntouchedTestLoader(("final",))
    with pytest.raises(LightGbmContractError, match="tuning"):
        loader.read(phase="TUNING")
    assert loader.access_count == 0
    assert loader.read(phase="FINAL_REPORT") == ("final",)
    assert loader.access_count == 1
    with pytest.raises(LightGbmContractError, match="exactly once"):
        loader.read(phase="FINAL_REPORT")


def test_invalid_session_count_and_overlap_fixture_fail_closed() -> None:
    sessions, labels = _split_fixture()
    with pytest.raises(LightGbmContractError, match="1,007"):
        build_walk_forward_plan(sessions[:-1], labels)
