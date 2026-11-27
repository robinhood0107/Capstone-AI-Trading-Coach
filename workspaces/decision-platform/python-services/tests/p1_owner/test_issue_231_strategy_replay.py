"""Controlled, provider-free strategy replay for issue #231."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import NamedTuple

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.calendar.xkrx_policy import (
    XKRX_CALENDAR_CORRECTION_SET_SHA256,
    XKRX_CALENDAR_POLICY_VERSION,
    corrected_calendar,
)
from app.p1_owner.scenario_materializer import (
    _INITIAL_CAPITAL,
    _ROUND_TRIP_COST,
    Trade,
    _strict_replay,
)

_REFERENCE_CAP = 300_000
_QUANTITY = 10
_COMMON_LIMITS = {
    "max_position_per_asset": 0.20,
    "max_gold_etf_etn_weight": 0.20,
    "daily_loss_guard": -1.0,
    "mdd_guard": -1.0,
    "max_daily_orders": 6,
}


class Case(NamedTuple):
    symbol: str
    start_close: int
    change_per_session: int
    rule_buy: bool
    lstm: str
    adverse_news: bool
    expected_violation: bool


_CASES = (
    Case("SYN01", 20_000, 7, True, "BUY", True, False),
    Case("SYN02", 27_000, -3, True, "SELL", False, False),
    Case("SYN03", 30_000, -4, True, "HOLD", False, False),
    Case("SYN04", 32_500, 5, True, "BUY", False, True),
    Case("SYN05", 37_500, -6, True, "SELL", True, True),
    Case("SYN06", 22_000, 2, False, "BUY", False, False),
)


def _arm(
    sessions: list[date],
    bars: dict[str, list[dict[str, object]]],
    trades: list[Trade],
    violations: set[Trade],
    cap: int,
    reference_candidates: set[Trade],
    reference_fills: set[Trade] | None = None,
) -> tuple[dict[str, object], set[Trade]]:
    rules = [
        {"ruleId": name, "enabled": True, "threshold": value}
        for name, value in {**_COMMON_LIMITS, "max_single_order_amount": cap}.items()
    ]
    result = _strict_replay(sessions, bars, trades, rules)
    by_prices = {(trade.entry_price, trade.exit_price): trade for trade in trades}
    # shortcut: unique price pairs identify fixture trades, add trace IDs for general inputs.
    if len(by_prices) != len(trades):
        raise ValueError("Synthetic fixture contains ambiguous entry/exit price pairs")
    fills = {by_prices[pair] for pair in result.trades}
    if len(fills) != len(result.trades) or result.violation_count != len(trades) - len(fills):
        raise ValueError("Replay did not reconcile all candidate outcomes")
    candidates = set(trades)
    expected_blocks = candidates & violations
    expected_normal = candidates - expected_blocks
    if reference_fills is None:
        reference_fills = fills
    return {
        "capKrw": cap,
        "candidates": len(candidates),
        "candidateAdded": sorted(
            (t.entry_session.isoformat(), t.symbol) for t in candidates - reference_candidates
        ),
        "candidateRemoved": sorted(
            (t.entry_session.isoformat(), t.symbol) for t in reference_candidates - candidates
        ),
        "expectedViolations": len(expected_blocks),
        "expectedNormal": len(expected_normal),
        "missedBlocks": len(expected_blocks & fills),
        "excessiveBlocks": len(expected_normal - fills),
        "blockedCandidates": sorted(
            (t.entry_session.isoformat(), t.symbol) for t in candidates - fills
        ),
        "orderLegs": 2 * len(fills),
        "orderLegsAdded": _legs(fills - reference_fills),
        "orderLegsRemoved": _legs(reference_fills - fills),
        "costKrw": round(
            sum(_QUANTITY * (t.entry_price + t.exit_price) for t in fills) * _ROUND_TRIP_COST / 2,
            2,
        ),
        "netPnlKrw": round(result.curve[-1][1] - _INITIAL_CAPITAL, 2),
    }, fills


def _legs(trades: set[Trade]) -> list[tuple[str, str, str, int]]:
    return sorted(
        leg
        for trade in trades
        for leg in (
            (trade.entry_session.isoformat(), trade.symbol, "BUY", trade.quantity),
            (trade.exit_session.isoformat(), trade.symbol, "SELL", trade.quantity),
        )
    )


def replay() -> dict[str, object]:
    dates = corrected_calendar().sessions_in_range("2026-08-14", "2026-09-03")
    prior_close, *sessions = [day.date() for day in dates]
    if len(sessions) != 13:
        raise ValueError("Expected exactly 13 corrected XKRX sessions")

    bars: dict[str, list[dict[str, object]]] = {}
    signals: dict[str, list[Trade]] = {
        "rule": [],
        "lstm": [],
        "rule_news": [],
        "rule_lstm_veto": [],
    }
    violations: set[Trade] = set()
    for case in _CASES:
        closes = [case.start_close + case.change_per_session * i for i in range(len(sessions))]
        bars[case.symbol] = [
            {"sessionDate": session.isoformat(), "close": close}
            for session, close in zip(sessions, closes, strict=True)
        ]
        for i, (entry, exit_) in enumerate(zip(sessions, sessions[1:])):
            trade = Trade(case.symbol, entry, exit_, _QUANTITY, closes[i], closes[i + 1])
            if case.expected_violation:
                violations.add(trade)
            selection = (
                case.rule_buy,
                case.lstm == "BUY",
                case.rule_buy and not case.adverse_news,
                case.rule_buy and case.lstm != "SELL",
            )
            for arm, selected in zip(signals, selection, strict=True):
                if selected:
                    signals[arm].append(trade)

    candidates = signals["rule"]
    reference, fills = _arm(sessions, bars, candidates, violations, _REFERENCE_CAP, set(candidates))
    inputs = {
        "calendar": XKRX_CALENDAR_POLICY_VERSION,
        "calendarCorrectionSha256": XKRX_CALENDAR_CORRECTION_SET_SHA256,
        "sessions": [session.isoformat() for session in sessions],
        "entrySnapshotDates": [day.isoformat() for day in [prior_close, *sessions[:-2]]],
        "priceBasis": "SYNTHETIC_RAW_CLOSE_NO_CORPORATE_ACTIONS",
        "costBpsRoundTrip": _ROUND_TRIP_COST * 10_000,
        "initialCapitalKrw": _INITIAL_CAPITAL,
        "quantity": _QUANTITY,
        "eligibleOpportunities": (len(sessions) - 1) * len(_CASES),
        "commonLimits": _COMMON_LIMITS,
        "universe": [case.symbol for case in _CASES],
    }
    return {
        "protocol": "p1-issue-231-synthetic-replay.v1",
        "inputs": inputs,
        "inputSha256": hashlib.sha256(
            canonical_json_bytes([inputs, [case._asdict() for case in _CASES], bars])
        ).hexdigest(),
        "reference": reference,
        "policyComparisons": {
            name: _arm(sessions, bars, candidates, violations, cap, set(candidates), fills)[0]
            for name, cap in (("looser", 360_000), ("tighter", 240_000))
        },
        "signalComparisons": {
            name: _arm(
                sessions, bars, signals[name], violations, _REFERENCE_CAP, set(candidates), fills
            )[0]
            for name in ("lstm", "rule_news", "rule_lstm_veto")
        },
    }


def test_controlled_replay_matches_frozen_oracle() -> None:
    report = replay()
    assert json.dumps(report, sort_keys=True) == json.dumps(replay(), sort_keys=True)
    inputs = report["inputs"]
    assert isinstance(inputs, dict)
    assert inputs["eligibleOpportunities"] == 72
    assert inputs["sessions"][0] == "2026-08-18"
    assert inputs["sessions"][-1] == "2026-09-03"
    assert inputs["entrySnapshotDates"] == ["2026-08-14", *inputs["sessions"][:-2]]

    reference = report["reference"]
    policies = report["policyComparisons"]
    signals = report["signalComparisons"]
    assert isinstance(reference, dict) and isinstance(policies, dict) and isinstance(signals, dict)
    assert ("2026-08-18", "SYN03") not in reference["blockedCandidates"]
    assert ("2026-08-18", "SYN03") in policies["tighter"]["blockedCandidates"]
    keys = (
        "candidates",
        "expectedViolations",
        "expectedNormal",
        "missedBlocks",
        "excessiveBlocks",
        "orderLegs",
    )
    counts = {
        "reference": (60, 24, 36, 0, 0, 72),
        "looser": (60, 24, 36, 12, 0, 96),
        "tighter": (60, 24, 36, 0, 24, 24),
        "lstm": (36, 12, 24, 0, 0, 48),
        "rule_news": (36, 12, 24, 0, 0, 48),
        "rule_lstm_veto": (36, 12, 24, 0, 0, 48),
    }
    deltas = {
        "reference": (0, 0, 0, 0),
        "looser": (0, 0, 24, 0),
        "tighter": (0, 0, 0, 48),
        "lstm": (12, 36, 24, 48),
        "rule_news": (0, 24, 0, 24),
        "rule_lstm_veto": (0, 24, 0, 24),
    }
    delta_keys = ("candidateAdded", "candidateRemoved", "orderLegsAdded", "orderLegsRemoved")
    for name, arm in {"reference": reference, **policies, **signals}.items():
        assert tuple(arm[key] for key in keys) == counts[name]
        assert tuple(len(arm[key]) for key in delta_keys) == deltas[name]
    assert {leg[1] for leg in signals["rule_news"]["orderLegsRemoved"]} == {"SYN01"}
    assert {leg[1] for leg in signals["rule_lstm_veto"]["orderLegsRemoved"]} == {"SYN02"}


if __name__ == "__main__":
    print(json.dumps(replay(), indent=2, sort_keys=True))
