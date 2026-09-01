from __future__ import annotations

from datetime import date
from pathlib import Path
import shutil

import pandas as pd
import pytest

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.calendar.xkrx_policy import corrected_calendar
from app.p1_owner.after_hours_replay import (
    AfterHoursReplayError,
    ReplayBar,
    build_replay_report,
    validate_observed_anchors,
)

_ROOT = Path(__file__).resolve().parents[5]
_ANCHORS = _ROOT / "artifacts/decision-platform/live-rehearsal"
_ANCHOR_MANIFEST = _ROOT / "contracts/catalogs/p1-after-hours-observed-anchors.v1.json"


def _rows(symbol_count: int = 31) -> tuple[ReplayBar, ...]:
    calendar = corrected_calendar()
    end = calendar.date_to_session(pd.Timestamp("2026-08-26"), direction="none")
    sessions = tuple(item.date() for item in calendar.sessions_window(end, -23))
    return tuple(
        ReplayBar(f"{symbol:06d}", session, 70_000, 71_000, 69_000, 70_000, 100)
        for symbol in range(1, symbol_count + 1)
        for session in sessions
    )


def test_full_row_accounting_exact31_and_repeat_are_byte_identical() -> None:
    rows = _rows()
    first = build_replay_report(rows, manifest_sha256="a" * 64, today=date(2026, 8, 31))
    second = build_replay_report(rows, manifest_sha256="a" * 64, today=date(2026, 8, 31))
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert first["inputRowCount"] == first["acceptedRowCount"] + first["rejectedRowCount"]
    assert first["unexplainedRows"] == 0
    assert first["historicalExact31Status"] == "PASS"
    assert first["historicalUnion270Status"] == "BLOCKED_INPUT_MISSING"
    assert first["syntheticMatrixStatus"] == "PASS"


def test_invalid_and_conflicting_rows_have_typed_rejection_reasons() -> None:
    valid = _rows(1)[0]
    conflict = ReplayBar(
        valid.symbol,
        valid.session_date,
        valid.open_price_krw,
        valid.high_price_krw,
        valid.low_price_krw,
        valid.close_price_krw + 1,
        valid.volume,
    )
    future = ReplayBar("000002", date(2026, 9, 1), 100, 110, 90, 100, 1)
    report = build_replay_report(
        (valid, conflict, future),
        manifest_sha256="b" * 64,
        today=date(2026, 8, 31),
    )
    assert report["acceptedRowCount"] == 0
    assert report["rejectedRowCount"] == 3
    assert report["rejectedByReason"] == {"DUPLICATE_CONFLICT": 2, "FUTURE_BAR": 1}


def test_middle_gap_rejects_the_entire_symbol_but_edge_trim_is_allowed() -> None:
    rows = _rows(1)
    middle_gap = rows[:10] + rows[11:]
    rejected = build_replay_report(
        middle_gap,
        manifest_sha256="c" * 64,
        today=date(2026, 8, 31),
    )
    assert rejected["acceptedRowCount"] == 0
    assert rejected["rejectedByReason"] == {"MIDDLE_SESSION_GAP": len(middle_gap)}

    edge_trim = rows[5:-5]
    accepted = build_replay_report(
        edge_trim,
        manifest_sha256="d" * 64,
        today=date(2026, 8, 31),
    )
    assert accepted["acceptedRowCount"] == len(edge_trim)
    assert accepted["rejectedRowCount"] == 0


def test_krx_alphanumeric_issue_code_is_accepted() -> None:
    rows = tuple(
        ReplayBar(
            "0126Z0",
            row.session_date,
            row.open_price_krw,
            row.high_price_krw,
            row.low_price_krw,
            row.close_price_krw,
            row.volume,
        )
        for row in _rows(1)
    )

    report = build_replay_report(
        rows,
        manifest_sha256="1" * 64,
        today=date(2026, 8, 31),
    )

    assert report["acceptedRowCount"] == len(rows)
    assert report["rejectedRowCount"] == 0
    assert report["symbolCount"] == 1


def test_union270_needs_the_sealed_exact_1072_session_axis() -> None:
    report = build_replay_report(
        _rows(270),
        manifest_sha256="e" * 64,
        today=date(2026, 8, 31),
    )

    assert report["symbolCount"] == 270
    assert report["sessionCount"] == 23
    assert report["historicalUnion270Status"] == "BLOCKED_INPUT_MISSING"


def test_all_eight_observed_anchors_are_hash_bound_without_exposing_payloads() -> None:
    result = validate_observed_anchors(_ANCHORS, _ANCHOR_MANIFEST)

    assert result["observedAnchorStatus"] == "PASS"
    assert result["observedAnchorCount"] == 8
    assert len(result["observedAnchorCategories"]) == 8
    assert set(result) == {
        "observedAnchorCategories",
        "observedAnchorCount",
        "observedAnchorManifestSha256",
        "observedAnchorSetSha256",
        "observedAnchorStatus",
    }


def test_observed_anchor_hash_drift_fails_closed(tmp_path: Path) -> None:
    copied = tmp_path / "anchors"
    shutil.copytree(_ANCHORS, copied)
    target = copied / "roundtrip-000660.json"
    target.write_bytes(target.read_bytes() + b"\n")

    with pytest.raises(AfterHoursReplayError, match="OBSERVED_ANCHOR_HASH_MISMATCH"):
        validate_observed_anchors(copied, _ANCHOR_MANIFEST)


def test_all_intrinsic_adversarial_rows_receive_typed_reasons() -> None:
    rows = (
        ReplayBar("BAD", date(2026, 8, 26), 100, 110, 90, 100, 1),
        ReplayBar("000001", date(2026, 8, 23), 100, 110, 90, 100, 1),
        ReplayBar("000002", date(2026, 8, 26), 100, 99, 90, 100, 1),
        ReplayBar("000003", date(2026, 8, 26), 100, 110, 90, 100, -1),
        ReplayBar("000004", date(2026, 9, 1), 100, 110, 90, 100, 1),
    )
    report = build_replay_report(
        rows,
        manifest_sha256="f" * 64,
        today=date(2026, 8, 31),
    )

    assert report["acceptedRowCount"] == 0
    assert report["unexplainedRows"] == 0
    assert report["rejectedByReason"] == {
        "FUTURE_BAR": 1,
        "INVALID_OHLC": 1,
        "INVALID_SYMBOL": 1,
        "INVALID_VOLUME": 1,
        "INVALID_XKRX_SESSION": 1,
    }
