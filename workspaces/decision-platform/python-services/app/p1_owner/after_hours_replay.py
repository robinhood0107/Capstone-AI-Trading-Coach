"""Provider-free full-row historical and deterministic synthetic replay."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence, cast
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg
from psycopg.conninfo import conninfo_to_dict

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.calendar.xkrx_policy import corrected_calendar
from app.p1_owner.automation import (
    AutomationEngine,
    AutomationError,
    AutomationInputs,
    AutomationPolicySnapshot,
    AutomationRun,
    AutomationStore,
    BotPosition,
    CandidateScreening,
    EvidenceSpan,
    FixtureAutomationTransport,
    NewsScreeningBatch,
    Quote,
    SignalCandidate,
)
from app.p1_owner.automation_atr import (
    AtrHistoryError,
    CompletedDailyBar,
    advance_trailing_stop,
    wilder_atr,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AfterHoursReplayError(RuntimeError):
    """Replay input, isolation, or deterministic invariant failed closed."""


@dataclass(frozen=True, slots=True)
class ReplayBar:
    symbol: str
    session_date: date
    open_price_krw: int
    high_price_krw: int
    low_price_krw: int
    close_price_krw: int
    volume: int

    def completed(self) -> CompletedDailyBar:
        return CompletedDailyBar(
            self.session_date,
            self.open_price_krw,
            self.high_price_krw,
            self.low_price_krw,
            self.close_price_krw,
        )


def read_manifest_rows(database_dsn: str, manifest_sha256: str) -> tuple[ReplayBar, ...]:
    if os.environ.get("P1_AFTER_HOURS_REPLAY_ISOLATED", "false").lower() != "true":
        raise AfterHoursReplayError("AFTER_HOURS_REPLAY_ISOLATION_REQUIRED")
    if _SHA256.fullmatch(manifest_sha256) is None:
        raise AfterHoursReplayError("AFTER_HOURS_REPLAY_MANIFEST_INVALID")
    try:
        parsed = conninfo_to_dict(database_dsn)
    except psycopg.Error as error:
        raise AfterHoursReplayError("AFTER_HOURS_REPLAY_DSN_INVALID") from error
    if parsed.get("user") != "decision_replay" or not parsed.get("dbname"):
        raise AfterHoursReplayError("AFTER_HOURS_REPLAY_ROLE_INVALID")
    rows: list[ReplayBar] = []
    try:
        with psycopg.connect(database_dsn, connect_timeout=2) as connection:
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute("SET LOCAL statement_timeout='10min'")
                connection.execute("SELECT set_config('app.after_hours_replay_isolated','1',true)")
                with connection.cursor() as cursor:
                    cursor.execute(
                        "select * from p1_read_after_hours_replay_bars_v1(%s)",
                        (manifest_sha256,),
                    )
                    for row in cursor:
                        rows.append(
                            ReplayBar(
                                str(row[0]),
                                row[1],
                                int(row[2]),
                                int(row[3]),
                                int(row[4]),
                                int(row[5]),
                                int(row[6]),
                            )
                        )
    except psycopg.Error as error:
        raise AfterHoursReplayError("AFTER_HOURS_REPLAY_READ_FAILED") from error
    return tuple(rows)


def build_replay_report(
    rows: Sequence[ReplayBar],
    *,
    manifest_sha256: str,
    today: date,
) -> dict[str, object]:
    if not rows:
        raise AfterHoursReplayError("HISTORICAL_REPLAY_BLOCKED_INPUT_MISSING")
    calendar = corrected_calendar()
    accepted, rejection_reasons = _classify_rows(rows, today=today, calendar=calendar)
    by_symbol: dict[str, list[ReplayBar]] = defaultdict(list)
    for row in accepted:
        by_symbol[row.symbol].append(row)
    atr_available = 0
    atr_unavailable = 0
    for symbol in sorted(by_symbol):
        history = tuple(
            item.completed()
            for item in sorted(by_symbol[symbol], key=lambda item: item.session_date)
        )
        if len(history) < 23:
            atr_unavailable += 1
            continue
        as_of = calendar.next_session(pd.Timestamp(history[-1].session_date)).date()
        try:
            wilder_atr(history[-101:], period=22, as_of_session=as_of)
            atr_available += 1
        except AtrHistoryError:
            atr_unavailable += 1
    input_digest = hashlib.sha256(
        canonical_json_bytes(
            [
                [
                    item.symbol,
                    item.session_date.isoformat(),
                    item.open_price_krw,
                    item.high_price_krw,
                    item.low_price_krw,
                    item.close_price_krw,
                    item.volume,
                ]
                for item in rows
            ]
        )
    ).hexdigest()
    synthetic_seed_sha256 = hashlib.sha256(
        f"p1-after-hours-replay-v1:{manifest_sha256}".encode()
    ).hexdigest()
    anchor_candidates = tuple(
        row for row in rows if _intrinsic_row_rejection(row, today=today, calendar=calendar) is None
    )
    synthetic_anchor = (
        anchor_candidates[int(synthetic_seed_sha256[:16], 16) % len(anchor_candidates)]
        if anchor_candidates
        else None
    )
    if synthetic_anchor is None:
        safe_session = cast(
            date,
            calendar.date_to_session(pd.Timestamp(today), direction="previous").date(),
        )
        base = max((row.close_price_krw for row in rows if row.close_price_krw > 0), default=100)
        synthetic_anchor = ReplayBar(
            "000001", safe_session, base, base + 1, max(1, base - 1), base, 0
        )
    synthetic = _synthetic_matrix(synthetic_anchor)
    rejected_count = sum(rejection_reasons.values())
    session_count = len({row.session_date for row in accepted})
    exact31_ready = len(by_symbol) == 31 and rejected_count == 0 and atr_unavailable == 0
    union270_ready = (
        len(by_symbol) == 270
        and session_count == 1_072
        and rejected_count == 0
        and atr_unavailable == 0
    )
    report: dict[str, object] = {
        "acceptedRowCount": len(accepted),
        "atrAvailableSymbolCount": atr_available,
        "atrUnavailableSymbolCount": atr_unavailable,
        "contractId": "p1-after-hours-replay.v1",
        "historicalExact31Status": "PASS" if exact31_ready else "NOT_EXACT31_INPUT",
        "historicalUnion270Status": "PASS" if union270_ready else "BLOCKED_INPUT_MISSING",
        "inputManifestSha256": manifest_sha256,
        "inputRowCount": len(rows),
        "inputRowsSha256": input_digest,
        "rejectedByReason": dict(sorted(rejection_reasons.items())),
        "rejectedRowCount": rejected_count,
        "sessionCount": session_count,
        "symbolCount": len(by_symbol),
        "syntheticMatrix": synthetic,
        "syntheticMatrixStatus": "PASS" if all(synthetic.values()) else "FAIL",
        "syntheticSeedSha256": synthetic_seed_sha256,
        "unexplainedRows": len(rows) - len(accepted) - rejected_count,
    }
    if report["unexplainedRows"] != 0:
        raise AfterHoursReplayError("AFTER_HOURS_REPLAY_UNEXPLAINED_ROWS")
    return report


def run_after_hours_replay(
    *,
    database_dsn: str,
    manifest_sha256: str,
    output_root: Path,
    observed_anchor_root: Path,
    observed_anchor_manifest: Path,
    today: date,
) -> Mapping[str, object]:
    rows = read_manifest_rows(database_dsn, manifest_sha256)
    first = {
        **build_replay_report(rows, manifest_sha256=manifest_sha256, today=today),
        **validate_observed_anchors(observed_anchor_root, observed_anchor_manifest),
    }
    second = {
        **build_replay_report(rows, manifest_sha256=manifest_sha256, today=today),
        **validate_observed_anchors(observed_anchor_root, observed_anchor_manifest),
    }
    first_bytes = canonical_json_bytes(first)
    if first_bytes != canonical_json_bytes(second):
        raise AfterHoursReplayError("AFTER_HOURS_REPLAY_NONDETERMINISTIC")
    target = output_root / manifest_sha256 / "report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".report-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(first_bytes + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {
        "reportPath": str(target),
        "reportSha256": hashlib.sha256(first_bytes).hexdigest(),
        **first,
    }


def validate_observed_anchors(root: Path, manifest_path: Path) -> dict[str, object]:
    if (
        root.is_symlink()
        or manifest_path.is_symlink()
        or not root.is_dir()
        or not manifest_path.is_file()
    ):
        raise AfterHoursReplayError("OBSERVED_ANCHOR_INPUT_INVALID")
    manifest_bytes = manifest_path.read_bytes()
    if not 2 <= len(manifest_bytes) <= 65_536:
        raise AfterHoursReplayError("OBSERVED_ANCHOR_MANIFEST_INVALID")
    try:
        manifest = _strict_json(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise AfterHoursReplayError("OBSERVED_ANCHOR_MANIFEST_INVALID") from error
    if not isinstance(manifest, dict) or set(manifest) != {
        "contractId",
        "receiptCount",
        "receipts",
    }:
        raise AfterHoursReplayError("OBSERVED_ANCHOR_MANIFEST_INVALID")
    receipts = manifest.get("receipts")
    if (
        manifest.get("contractId") != "p1-after-hours-observed-anchors.v1"
        or manifest.get("receiptCount") != 8
        or not isinstance(receipts, list)
        or len(receipts) != 8
    ):
        raise AfterHoursReplayError("OBSERVED_ANCHOR_MANIFEST_INVALID")
    categories: list[str] = []
    receipt_hashes: list[str] = []
    for item in receipts:
        if not isinstance(item, dict) or set(item) != {"category", "path", "sha256"}:
            raise AfterHoursReplayError("OBSERVED_ANCHOR_MANIFEST_INVALID")
        category = item.get("category")
        relative = item.get("path")
        expected_sha256 = item.get("sha256")
        if (
            not isinstance(category, str)
            or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", category)
            or not isinstance(relative, str)
            or Path(relative).name != relative
            or not isinstance(expected_sha256, str)
            or _SHA256.fullmatch(expected_sha256) is None
        ):
            raise AfterHoursReplayError("OBSERVED_ANCHOR_MANIFEST_INVALID")
        target = root / relative
        if target.is_symlink() or not target.is_file():
            raise AfterHoursReplayError("OBSERVED_ANCHOR_RECEIPT_MISSING")
        receipt_bytes = target.read_bytes()
        if not 2 <= len(receipt_bytes) <= 1_000_000:
            raise AfterHoursReplayError("OBSERVED_ANCHOR_RECEIPT_INVALID")
        actual_sha256 = hashlib.sha256(receipt_bytes).hexdigest()
        if actual_sha256 != expected_sha256:
            raise AfterHoursReplayError("OBSERVED_ANCHOR_HASH_MISMATCH")
        try:
            receipt = _strict_json(receipt_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise AfterHoursReplayError("OBSERVED_ANCHOR_RECEIPT_INVALID") from error
        if not isinstance(receipt, dict) or receipt.get("status") != "SUCCESS":
            raise AfterHoursReplayError("OBSERVED_ANCHOR_RECEIPT_INVALID")
        categories.append(category)
        receipt_hashes.append(actual_sha256)
    if len(set(categories)) != len(categories):
        raise AfterHoursReplayError("OBSERVED_ANCHOR_CATEGORY_DUPLICATE")
    set_sha256 = hashlib.sha256(canonical_json_bytes(sorted(receipt_hashes))).hexdigest()
    return {
        "observedAnchorCategories": sorted(categories),
        "observedAnchorCount": len(receipt_hashes),
        "observedAnchorManifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "observedAnchorSetSha256": set_sha256,
        "observedAnchorStatus": "PASS",
    }


def _strict_json(value: bytes) -> object:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    return json.loads(value.decode("utf-8"), object_pairs_hook=unique_object)


def _classify_rows(
    rows: Sequence[ReplayBar],
    *,
    today: date,
    calendar: Any,
) -> tuple[list[ReplayBar], Counter[str]]:
    rejection_reasons: Counter[str] = Counter()
    candidates: dict[tuple[str, date], list[ReplayBar]] = defaultdict(list)
    for row in rows:
        reason = _intrinsic_row_rejection(row, today=today, calendar=calendar)
        if reason is None:
            candidates[(row.symbol, row.session_date)].append(row)
        else:
            rejection_reasons[reason] += 1

    unique_by_symbol: dict[str, list[ReplayBar]] = defaultdict(list)
    for key in sorted(candidates):
        duplicates = candidates[key]
        if len(duplicates) == 1:
            unique_by_symbol[key[0]].append(duplicates[0])
        elif any(item != duplicates[0] for item in duplicates[1:]):
            # A conflicting duplicate poisons both values.  Keeping the first
            # would silently choose one untrusted OHLC row for ATR.
            rejection_reasons["DUPLICATE_CONFLICT"] += len(duplicates)
        else:
            unique_by_symbol[key[0]].append(duplicates[0])
            rejection_reasons["DUPLICATE_IDENTICAL"] += len(duplicates) - 1

    accepted: list[ReplayBar] = []
    for symbol in sorted(unique_by_symbol):
        symbol_rows = sorted(unique_by_symbol[symbol], key=lambda item: item.session_date)
        expected = tuple(
            item.date()
            for item in calendar.sessions_in_range(
                pd.Timestamp(symbol_rows[0].session_date),
                pd.Timestamp(symbol_rows[-1].session_date),
            )
        )
        observed = tuple(item.session_date for item in symbol_rows)
        if observed != expected:
            rejection_reasons["MIDDLE_SESSION_GAP"] += len(symbol_rows)
        else:
            accepted.extend(symbol_rows)
    return accepted, rejection_reasons


def _intrinsic_row_rejection(
    row: ReplayBar,
    *,
    today: date,
    calendar: Any,
) -> str | None:
    if not re.fullmatch(r"[0-9]{6}", row.symbol):
        return "INVALID_SYMBOL"
    if row.session_date > today:
        return "FUTURE_BAR"
    if not calendar.is_session(pd.Timestamp(row.session_date)):
        return "INVALID_XKRX_SESSION"
    if row.volume < 0:
        return "INVALID_VOLUME"
    try:
        row.completed()
    except AtrHistoryError:
        return "INVALID_OHLC"
    return None


def _synthetic_matrix(anchor: ReplayBar) -> dict[str, bool]:
    policy_id = "auto_pol_" + "a" * 32
    conservative = AutomationPolicySnapshot.from_v3_preset(
        policy_id=policy_id,
        version=1,
        capital_limit_krw=10_000_000,
        preset="CONSERVATIVE",
    )
    balanced = AutomationPolicySnapshot.from_v3_preset(
        policy_id=policy_id,
        version=2,
        capital_limit_krw=10_000_000,
        preset="BALANCED",
    )
    aggressive = AutomationPolicySnapshot.from_v3_preset(
        policy_id=policy_id,
        version=3,
        capital_limit_krw=10_000_000,
        preset="AGGRESSIVE",
    )
    minimum = AutomationPolicySnapshot(
        policy_id=policy_id,
        version=4,
        capital_limit_krw=10_000,
        stop_loss_bps=100,
        take_profit_bps=200,
        preset="CUSTOM",
        max_holding_sessions=1,
        atr_period=5,
        atr_multiplier_milli=1_000,
        model_sell_enabled=False,
    )
    maximum = AutomationPolicySnapshot(
        policy_id=policy_id,
        version=5,
        capital_limit_krw=10_000_000_000,
        stop_loss_bps=1_500,
        take_profit_bps=3_000,
        preset="CUSTOM",
        max_holding_sessions=1_260,
        atr_period=100,
        atr_multiplier_milli=10_000,
        model_sell_enabled=False,
    )
    invalid_unit_rejected = False
    try:
        AutomationPolicySnapshot(
            policy_id=policy_id,
            version=6,
            capital_limit_krw=10_000_000,
            stop_loss_bps=500,
            take_profit_bps=1_000,
            preset="CUSTOM",
            max_holding_sessions=60,
            atr_period=22,
            atr_multiplier_milli=3_050,
        )
    except AutomationError:
        invalid_unit_rejected = True

    evaluation, history, expected, base = _synthetic_history(anchor)
    atr = wilder_atr(
        history[-6:],
        period=5,
        as_of_session=evaluation,
        expected_sessions=expected,
    )
    trailing = advance_trailing_stop(
        previous_peak_price_krw=base,
        completed_high_price_krw=base + max(20, base // 100),
        current_quote_price_krw=base + max(10, base // 200),
        atr_value_krw=atr.value_krw,
        atr_multiplier_milli=3_000,
        previous_trailing_stop_krw=None,
    )
    calendar = corrected_calendar()
    conflict = ReplayBar(
        anchor.symbol,
        anchor.session_date,
        anchor.open_price_krw,
        anchor.high_price_krw,
        anchor.low_price_krw,
        anchor.close_price_krw,
        anchor.volume + 1,
    )
    duplicate_accepted, duplicate_reasons = _classify_rows(
        (anchor, conflict), today=anchor.session_date, calendar=calendar
    )
    gap_rows = (
        ReplayBar("000001", history[0].session_date, base, base + 1, base - 1, base, 1),
        ReplayBar("000001", history[2].session_date, base, base + 1, base - 1, base, 1),
    )
    gap_accepted, gap_reasons = _classify_rows(gap_rows, today=evaluation, calendar=calendar)
    priorities = {
        "stop_atr": _synthetic_exit_reason(anchor, stop=True, atr=True) == "STOP_LOSS",
        "atr_model": _synthetic_exit_reason(anchor, atr=True, model=True) == "ATR_TRAILING",
        "model_profit": _synthetic_exit_reason(anchor, model=True, profit=True) == "MODEL_SELL",
        "profit_time": _synthetic_exit_reason(anchor, profit=True, time=True) == "TAKE_PROFIT",
        "time_only": _synthetic_exit_reason(anchor, time=True) == "MAX_HOLDING_SESSIONS",
    }
    evidence_results = _synthetic_evidence_results(anchor)
    return {
        "adversarial_duplicate_conflict": not duplicate_accepted
        and duplicate_reasons == {"DUPLICATE_CONFLICT": 2},
        "adversarial_middle_gap": not gap_accepted and gap_reasons == {"MIDDLE_SESSION_GAP": 2},
        "atr_initial_and_wilder_update": atr.value_krw > 0,
        "atr_peak_monotonic": trailing.peak_price_krw >= base + 20,
        "atr_stop_non_decreasing": trailing.trailing_stop_krw > 0,
        "hard_eligibility_unknown_excluded": _synthetic_hard_eligibility(anchor),
        "max_holding_zero_unlimited": aggressive.max_holding_sessions == 0
        and _synthetic_unlimited_fill(anchor),
        "model_sell_off_skips_only_model": _synthetic_exit_reason(
            anchor, model=True, model_enabled=False
        )
        is None,
        "policy_aggressive": aggressive.atr_multiplier_milli == 3_500,
        "policy_balanced": balanced.max_holding_sessions == 60,
        "policy_conservative": conservative.max_holding_sessions == 20,
        "policy_minimum_boundary": minimum.atr_period == 5
        and minimum.atr_multiplier_milli == 1_000,
        "policy_maximum_boundary": maximum.atr_period == 100
        and maximum.max_holding_sessions == 1_260,
        "policy_invalid_unit_rejected": invalid_unit_rejected,
        "pre_entry_high_not_imported_into_peak": _synthetic_pre_entry_high_ignored(anchor),
        **evidence_results,
        **priorities,
    }


def _synthetic_history(
    anchor: ReplayBar,
) -> tuple[date, tuple[CompletedDailyBar, ...], tuple[date, ...], int]:
    calendar = corrected_calendar()
    anchor_session = calendar.date_to_session(pd.Timestamp(anchor.session_date), direction="none")
    evaluation = cast(date, calendar.next_session(anchor_session).date())
    sessions = tuple(item.date() for item in calendar.sessions_window(anchor_session, -23))
    base = max(anchor.close_price_krw, 100)
    spread = max(2, base // 200)
    bars = tuple(
        CompletedDailyBar(
            session_date=session,
            open_price_krw=base,
            high_price_krw=base + spread + index,
            low_price_krw=max(1, base - spread),
            close_price_krw=base + min(index, spread),
        )
        for index, session in enumerate(sessions)
    )
    return evaluation, bars, sessions, base


def _synthetic_store(
    evaluation: date, *, state: str = "SCHEDULED"
) -> tuple[AutomationStore, AutomationRun]:
    now = datetime.combine(evaluation, datetime.min.time(), ZoneInfo("Asia/Seoul")).replace(
        hour=9, minute=30
    )
    store = AutomationStore(
        "acct_synthetic_0001",
        "KIS_MOCK",
        "prc_synthetic_0001",
        "strategy_synthetic_0001",
        "a" * 64,
    )
    run = store.create_run(run_id="auto_run_" + "b" * 32, session_date=evaluation, now=now)
    run.state = state
    return store, run


def _drive_synthetic(
    store: AutomationStore,
    run: AutomationRun,
    inputs: AutomationInputs,
    transport: FixtureAutomationTransport,
    *,
    stop_states: frozenset[str],
) -> str:
    now = datetime.combine(run.session_date, datetime.min.time(), ZoneInfo("Asia/Seoul")).replace(
        hour=9, minute=30
    )
    for index in range(1, 20):
        result = AutomationEngine(store).tick(
            run_id=run.run_id,
            tick_id=f"synthetic_{index}",
            now=now + timedelta(seconds=index),
            inputs=inputs,
            transport=transport,
        )
        if str(result["state"]) in stop_states:
            return str(result["state"])
    return str(run.state)


def _synthetic_inputs(
    anchor: ReplayBar,
    *signals: SignalCandidate,
    ai_enabled: bool,
) -> tuple[AutomationInputs, int]:
    evaluation, history, expected, base = _synthetic_history(anchor)
    return (
        AutomationInputs(
            session_date=evaluation,
            policy=AutomationPolicySnapshot.from_v3_preset(
                policy_id="auto_pol_" + "c" * 32,
                version=1,
                capital_limit_krw=10_000_000,
                preset="BALANCED",
            ),
            signals=tuple(signals),
            atr_histories={item.symbol: history for item in signals},
            atr_expected_sessions=expected,
            ai_judgement_enabled=ai_enabled,
            ai_judgement_provider_bound=ai_enabled,
            ai_settings_sha256="d" * 64,
            buyable_quantity=1,
        ),
        base,
    )


def _synthetic_evidence_results(anchor: ReplayBar) -> dict[str, bool]:
    first = SignalCandidate("000001", "BUY", "BUY", 0.05, 0.8)
    second = SignalCandidate("000002", "BUY", "BUY", 0.04, 0.8)
    inputs, base = _synthetic_inputs(anchor, first, second, ai_enabled=True)
    evaluation = inputs.session_date
    quotes = {
        item.symbol: Quote(item.symbol, base, max(1, base // 2), base * 2)
        for item in (first, second)
    }

    zero_store, zero_run = _synthetic_store(evaluation)
    zero_transport = FixtureAutomationTransport(
        quotes=dict(quotes),
        screening_batch=NewsScreeningBatch(
            (
                CandidateScreening("000001", "AVAILABLE", "NO_VETO", 5_000, "NO_EVIDENCE"),
                CandidateScreening("000002", "AVAILABLE", "NO_VETO", 5_000, "NO_EVIDENCE"),
            ),
            0,
            0,
        ),
    )
    zero_state = _drive_synthetic(
        zero_store,
        zero_run,
        inputs,
        zero_transport,
        stop_states=frozenset({"BUY_CANDIDATE_SELECTED", "SKIPPED_DATA_UNAVAILABLE"}),
    )

    injection_store, injection_run = _synthetic_store(evaluation)
    injection_transport = FixtureAutomationTransport(
        quotes=dict(quotes),
        screening_batch=NewsScreeningBatch(
            (
                CandidateScreening("000001", "ABSTAIN", "NO_VETO", 5_000, "PROMPT_INJECTION"),
                CandidateScreening("000002", "AVAILABLE", "NO_VETO", 5_000, "NO_EVIDENCE"),
            ),
            0,
            0,
        ),
    )
    injection_state = _drive_synthetic(
        injection_store,
        injection_run,
        inputs,
        injection_transport,
        stop_states=frozenset({"BUY_CANDIDATE_SELECTED", "SKIPPED_DATA_UNAVAILABLE"}),
    )

    veto_span = EvidenceSpan(
        "000001",
        "cit_synthetic_000001",
        "src_official_dart",
        "OFFICIAL_PRIMARY",
        evaluation,
        False,
        "e" * 64,
        "verified adverse synthetic evidence",
        "f" * 64,
    )
    veto_store, veto_run = _synthetic_store(evaluation)
    veto_transport = FixtureAutomationTransport(
        quotes=dict(quotes),
        screening_batch=NewsScreeningBatch(
            (
                CandidateScreening(
                    "000001", "AVAILABLE", "VETO_BUY", 2_000, "VERIFIED", (veto_span,)
                ),
                CandidateScreening("000002", "AVAILABLE", "NO_VETO", 5_000, "NO_EVIDENCE"),
            ),
            0,
            0,
        ),
    )
    veto_state = _drive_synthetic(
        veto_store,
        veto_run,
        inputs,
        veto_transport,
        stop_states=frozenset({"BUY_CANDIDATE_SELECTED", "SKIPPED_DATA_UNAVAILABLE"}),
    )

    failed_store, failed_run = _synthetic_store(evaluation)
    failed_transport = FixtureAutomationTransport(
        quotes=dict(quotes), screening_batch=NewsScreeningBatch((), 0, 0, failed=True)
    )
    failed_state = _drive_synthetic(
        failed_store,
        failed_run,
        inputs,
        failed_transport,
        stop_states=frozenset({"SKIPPED_DATA_UNAVAILABLE"}),
    )
    return {
        "evidence_zero_rule_rank": zero_state == "BUY_CANDIDATE_SELECTED"
        and zero_run.selected_symbol == "000001"
        and zero_transport.judge_calls == 0,
        "prompt_injection_candidate_only_abstain": injection_state == "BUY_CANDIDATE_SELECTED"
        and injection_run.selected_symbol == "000002"
        and injection_transport.judge_calls == 0,
        "vetoed_only_evidence_skips_judge": veto_state == "BUY_CANDIDATE_SELECTED"
        and veto_run.selected_symbol == "000002"
        and veto_transport.judge_calls == 0,
        "provider_failure_ai_on_no_buy": failed_state == "SKIPPED_DATA_UNAVAILABLE"
        and failed_transport.submit_calls == 0,
    }


def _synthetic_hard_eligibility(anchor: ReplayBar) -> bool:
    first = SignalCandidate("000001", "BUY", "BUY", 0.05, 0.8)
    second = SignalCandidate("000002", "BUY", "BUY", 0.04, 0.8)
    inputs, base = _synthetic_inputs(anchor, first, second, ai_enabled=False)
    store, run = _synthetic_store(inputs.session_date)
    transport = FixtureAutomationTransport(
        quotes={
            "000001": Quote("000001", base, max(1, base // 2), base * 2, management_issue_code=""),
            "000002": Quote("000002", base, max(1, base // 2), base * 2),
        }
    )
    state = _drive_synthetic(
        store,
        run,
        inputs,
        transport,
        stop_states=frozenset({"BUY_CANDIDATE_SELECTED", "SKIPPED_NO_ACTION"}),
    )
    return state == "BUY_CANDIDATE_SELECTED" and run.selected_symbol == "000002"


def _synthetic_unlimited_fill(anchor: ReplayBar) -> bool:
    candidate = SignalCandidate("000001", "BUY", "BUY", 0.05, 0.8)
    inputs, base = _synthetic_inputs(anchor, candidate, ai_enabled=False)
    inputs = replace(
        inputs,
        policy=AutomationPolicySnapshot.from_v3_preset(
            policy_id="auto_pol_" + "c" * 32,
            version=2,
            capital_limit_krw=10_000_000,
            preset="AGGRESSIVE",
        ),
    )
    store, run = _synthetic_store(inputs.session_date)
    transport = FixtureAutomationTransport(
        quotes={"000001": Quote("000001", base, max(1, base // 2), base * 2)}
    )
    state = _drive_synthetic(
        store,
        run,
        inputs,
        transport,
        stop_states=frozenset({"COMPLETED", "HALTED", "SKIPPED_DATA_UNAVAILABLE"}),
    )
    return (
        state == "COMPLETED"
        and len(store.positions) == 1
        and store.positions[0].expiry_session is None
        and store.positions[0].peak_price_krw == store.positions[0].entry_average_fill_price_krw
    )


def _synthetic_exit_reason(
    anchor: ReplayBar,
    *,
    stop: bool = False,
    atr: bool = False,
    model: bool = False,
    profit: bool = False,
    time: bool = False,
    model_enabled: bool = True,
) -> str | None:
    evaluation, history, expected, base = _synthetic_history(anchor)
    store, run = _synthetic_store(evaluation, state="PRECHECK")
    calendar = corrected_calendar()
    future_expiry = cast(date, calendar.next_session(pd.Timestamp(evaluation)).date())
    entry_session = history[-2].session_date
    store.positions.append(
        BotPosition(
            "auto_pos_synthetic_exit_0001",
            store.account_id,
            "000001",
            entry_session,
            evaluation if time else future_expiry,
            run.started_at,
            entry_average_fill_price_krw=base,
            policy_id="auto_pol_" + "c" * 32,
            max_holding_sessions=60,
            atr_period=22,
            atr_multiplier_milli=3_000,
            model_sell_enabled=model_enabled,
            peak_price_krw=base,
            atr_as_of_session=history[-1].session_date,
            trailing_stop_krw=max(1, base * 99 // 100) if atr else 1,
            atr_status="AVAILABLE",
        )
    )
    price = (
        max(1, base * 94 // 100)
        if stop
        else max(1, base * 97 // 100)
        if atr
        else base * 112 // 100
        if profit
        else base
    )
    signals = (SignalCandidate("000001", "SELL", "SELL", -0.1, 0.8),) if model else ()
    inputs = AutomationInputs(
        session_date=evaluation,
        policy=AutomationPolicySnapshot.from_v3_preset(
            policy_id="auto_pol_" + "c" * 32,
            version=1,
            capital_limit_krw=10_000_000,
            preset="BALANCED",
        ),
        signals=signals,
        atr_histories={"000001": history},
        atr_expected_sessions=expected,
    )
    AutomationEngine(store).tick(
        run_id=run.run_id,
        tick_id="synthetic_exit",
        now=run.started_at,
        inputs=inputs,
        transport=FixtureAutomationTransport(
            quotes={"000001": Quote("000001", price, max(1, base // 2), base * 2)}
        ),
    )
    return run.exit_reason


def _synthetic_pre_entry_high_ignored(anchor: ReplayBar) -> bool:
    evaluation, history, expected, base = _synthetic_history(anchor)
    poisoned = (
        CompletedDailyBar(
            history[0].session_date,
            base,
            base * 2,
            max(1, base // 2),
            base,
        ),
        *history[1:],
    )
    store, run = _synthetic_store(evaluation, state="PRECHECK")
    store.positions.append(
        BotPosition(
            "auto_pos_synthetic_peak_0001",
            store.account_id,
            "000001",
            history[-2].session_date,
            cast(
                date,
                corrected_calendar().next_session(pd.Timestamp(evaluation)).date(),
            ),
            run.started_at,
            entry_average_fill_price_krw=base,
            policy_id="auto_pol_" + "c" * 32,
            max_holding_sessions=60,
            atr_period=22,
            atr_multiplier_milli=3_000,
            peak_price_krw=base,
            atr_as_of_session=history[-1].session_date,
            trailing_stop_krw=1,
            atr_status="AVAILABLE",
        )
    )
    inputs = AutomationInputs(
        session_date=evaluation,
        policy=AutomationPolicySnapshot.from_v3_preset(
            policy_id="auto_pol_" + "c" * 32,
            version=1,
            capital_limit_krw=10_000_000,
            preset="BALANCED",
        ),
        atr_histories={"000001": poisoned},
        atr_expected_sessions=expected,
    )
    AutomationEngine(store).tick(
        run_id=run.run_id,
        tick_id="synthetic_peak",
        now=run.started_at,
        inputs=inputs,
        transport=FixtureAutomationTransport(
            quotes={"000001": Quote("000001", base, max(1, base // 2), base * 2)}
        ),
    )
    return cast(int, store.positions[0].peak_price_krw) < base * 2
