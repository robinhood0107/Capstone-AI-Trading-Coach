"""Deterministic provider-free P1 automation closed-loop fixture engine.

One tick advances at most one durable boundary.  An append-only store keeps
sanitized events, reservations, bot-owned lots, and processed tick identities
so process restart and duplicate delivery cannot repeat a quote, Vertex check,
submit, cancel, or reconciliation operation.  Live transports are deliberately
absent from this module.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time
from importlib.metadata import version
from typing import Any, Literal, Protocol, cast

import exchange_calendars as xcals
import pandas as pd

from app.data._shared.canonical_json import canonical_json_bytes

Signal = Literal["BUY", "HOLD", "SELL"]
Side = Literal["BUY", "SELL"]
NewsVerdict = Literal["VETO_BUY", "NO_VETO", "ABSTAIN"]
SubmitOutcome = Literal["FILLED", "UNFILLED", "AMBIGUOUS"]
ReconcileOutcome = Literal["FILLED", "UNFILLED", "UNRESOLVED"]

_ACTIVE_STATES = frozenset(
    {
        "SCHEDULED",
        "PRECHECK",
        "RECONCILING_PREVIOUS",
        "EXIT_SELECTED",
        "BUY_CANDIDATE_SELECTED",
        "NEWS_CHECKING",
        "RISK_CHECKING",
        "ORDER_SUBMITTING",
        "ORDER_SUBMITTED",
        "PENDING_RECONCILIATION",
    }
)
_TERMINAL_STATES = frozenset(
    {
        "NEWS_VETOED",
        "CANCELLED_UNFILLED",
        "COMPLETED",
        "SKIPPED_NO_ACTION",
        "SKIPPED_DATA_UNAVAILABLE",
        "SKIPPED_LATE_START",
        "HALTED",
    }
)
_KST_CLOSE_ORDER_TIME = time(9, 20)
_CANCEL_TIME = time(15, 20)


class AutomationError(RuntimeError):
    """Automation state or fixture transport violated a fail-closed invariant."""


@dataclass(frozen=True, slots=True)
class SignalCandidate:
    symbol: str
    lstm_signal: Signal
    baseline_signal: Signal
    expected_return: float
    confidence: float


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    price_krw: int
    lower_limit_krw: int
    upper_limit_krw: int
    fresh: bool = True
    is_etf_etn: bool = False


@dataclass(frozen=True, slots=True)
class AutomationInputs:
    session_date: date
    release_active: bool = True
    daily_shard_fresh_complete: bool = True
    principle_active_current: bool = True
    risk_allow: bool = True
    kill_switch_active: bool = False
    account_complete: bool = True
    account_digest_matches: bool = True
    buyable_quantity: int = 1
    no_open_order: bool = True
    unfinished_previous_order: bool = False
    manual_position_symbols: frozenset[str] = frozenset()
    signals: tuple[SignalCandidate, ...] = ()


@dataclass(slots=True)
class BotPosition:
    position_id: str
    account_id: str
    symbol: str
    entry_session: date
    expiry_session: date
    created_at: datetime
    status: str = "OPEN"
    closed_at: datetime | None = None

    def projection(self) -> dict[str, object]:
        return {
            "accountId": self.account_id,
            "botOwned": True,
            "closedAt": _iso(self.closed_at) if self.closed_at is not None else None,
            "contractId": "automation-position.v1",
            "createdAt": _iso(self.created_at),
            "entrySession": self.entry_session.isoformat(),
            "expirySession": self.expiry_session.isoformat(),
            "positionId": self.position_id,
            "quantity": 1,
            "shortAllowed": False,
            "status": self.status,
            "symbol": self.symbol,
        }


@dataclass(slots=True)
class OrderReservation:
    symbol: str
    side: Side
    quantity: int
    limit_price_krw: int
    quote_observed: bool = True


@dataclass(slots=True)
class AutomationRun:
    run_id: str
    session_date: date
    brokerage_mode: str
    started_at: datetime
    updated_at: datetime
    state: str = "SCHEDULED"
    selected_symbol: str | None = None
    selected_side: Side | None = None
    vertex_call_count: int = 0
    reservation: OrderReservation | None = None
    submit_outcome: SubmitOutcome | None = None
    logical_submit_count: int = 0
    physical_submit_count: int = 0
    provider_call_count: int = 0

    def projection(self) -> dict[str, object]:
        return {
            "brokerageMode": self.brokerage_mode,
            "contractId": "automation-run.v1",
            "physicalSubmitCount": self.physical_submit_count,
            "providerCalls": self.provider_call_count,
            "runId": self.run_id,
            "selectedSide": self.selected_side,
            "selectedSymbol": self.selected_symbol,
            "sessionDate": self.session_date.isoformat(),
            "startedAt": _iso(self.started_at),
            "state": self.state,
            "updatedAt": _iso(self.updated_at),
            "vertexCallCount": self.vertex_call_count,
        }


class AutomationFixtureTransportPort(Protocol):
    physical_calls: int
    physical_submit_calls: int
    quote_calls: int
    vertex_calls: int
    submit_calls: int
    reconcile_calls: int
    cancel_calls: int

    def quote(self, symbol: str) -> Quote: ...

    def vertex(self, symbol: str) -> NewsVerdict: ...

    def submit(self, reservation: OrderReservation) -> SubmitOutcome: ...

    def reconcile(self, reservation: OrderReservation | None) -> ReconcileOutcome: ...

    def cancel(self, reservation: OrderReservation) -> bool: ...


@dataclass(slots=True)
class FixtureAutomationTransport:
    """Configurable logical outcomes with physical call count fixed at zero."""

    quotes: dict[str, Quote]
    news_verdict: NewsVerdict = "NO_VETO"
    submit_outcome: SubmitOutcome = "FILLED"
    reconcile_outcomes: list[ReconcileOutcome] = field(default_factory=lambda: ["FILLED"])
    cancel_succeeds: bool = True
    physical_calls: int = 0
    physical_submit_calls: int = 0
    quote_calls: int = 0
    vertex_calls: int = 0
    submit_calls: int = 0
    reconcile_calls: int = 0
    cancel_calls: int = 0

    def quote(self, symbol: str) -> Quote:
        self.quote_calls += 1
        try:
            return self.quotes[symbol]
        except KeyError as error:
            raise AutomationError("fixture quote is unavailable") from error

    def vertex(self, symbol: str) -> NewsVerdict:
        del symbol
        self.vertex_calls += 1
        return self.news_verdict

    def submit(self, reservation: OrderReservation) -> SubmitOutcome:
        del reservation
        self.submit_calls += 1
        return self.submit_outcome

    def reconcile(self, reservation: OrderReservation | None) -> ReconcileOutcome:
        del reservation
        self.reconcile_calls += 1
        return self.reconcile_outcomes.pop(0) if self.reconcile_outcomes else "UNRESOLVED"

    def cancel(self, reservation: OrderReservation) -> bool:
        del reservation
        self.cancel_calls += 1
        return self.cancel_succeeds


@dataclass(slots=True)
class AutomationStore:
    """Append-only fixture store reconstructed by sharing it across engine restarts."""

    account_id: str
    brokerage_mode: str
    principle_id: str
    strategy_id: str
    baseline_account_digest: str
    control_state: str = "ARMED"
    version: int = 1
    certification_status: str = "VALID"
    runs: dict[str, AutomationRun] = field(default_factory=dict)
    positions: list[BotPosition] = field(default_factory=list)
    events: list[dict[str, object]] = field(default_factory=list)
    processed_ticks: dict[tuple[str, str], dict[str, object]] = field(default_factory=dict)
    session_submit_reservations: dict[date, str] = field(default_factory=dict)
    baseline_event_recorded: bool = False

    def create_run(self, *, run_id: str, session_date: date, now: datetime) -> AutomationRun:
        if run_id in self.runs:
            return self.runs[run_id]
        _validate_id(run_id, "auto_run_")
        run = AutomationRun(run_id, session_date, self.brokerage_mode, now, now)
        self.runs[run_id] = run
        if not self.baseline_event_recorded:
            self.append_event(
                run,
                "BASELINE_CAPTURED",
                {"baselineAccountDigest": self.baseline_account_digest},
                now,
            )
            self.baseline_event_recorded = True
        self.append_event(run, "RUN_TRANSITIONED", {"state": "SCHEDULED"}, now)
        return run

    def append_event(
        self,
        run: AutomationRun,
        event_type: str,
        payload: dict[str, object],
        now: datetime,
    ) -> None:
        sequence = 1 + sum(event["runId"] == run.run_id for event in self.events)
        payload_hash = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        event_seed = f"{run.run_id}:{sequence}:{event_type}:{payload_hash}".encode()
        event_id = f"auto_evt_{hashlib.sha256(event_seed).hexdigest()[:24]}"
        self.events.append(
            {
                "contractId": "automation-event.v1",
                "eventId": event_id,
                "eventType": event_type,
                "occurredAt": _iso(now),
                "orderSubmits": 0,
                "payloadHash": payload_hash,
                "providerCalls": 0,
                "runId": run.run_id,
                "sanitized": True,
                "sequence": sequence,
            }
        )

    def control_projection(self, *, kill_switch_active: bool) -> dict[str, object]:
        running = any(run.state in _ACTIVE_STATES for run in self.runs.values())
        projection = (
            "HALTED"
            if self.control_state == "HALTED"
            else "RUNNING"
            if running
            else self.control_state
        )
        return {
            "brokerageMode": self.brokerage_mode,
            "certificationStatus": self.certification_status,
            "contractId": "automation-control.v1",
            "controlState": self.control_state,
            "killSwitchActive": kill_switch_active,
            "principleId": self.principle_id,
            "projectionState": projection,
            "strategyId": self.strategy_id,
            "version": self.version,
        }


class AutomationEngine:
    def __init__(self, store: AutomationStore) -> None:
        self.store = store

    def tick(
        self,
        *,
        run_id: str,
        tick_id: str,
        now: datetime,
        inputs: AutomationInputs,
        transport: AutomationFixtureTransportPort,
    ) -> dict[str, object]:
        """Advance one durable boundary; duplicate tick identities are strict no-ops."""

        key = (run_id, tick_id)
        if key in self.store.processed_ticks:
            return self.store.processed_ticks[key]
        run = self.store.runs[run_id]
        self._advance(run, now, inputs, transport)
        if transport.physical_calls < run.provider_call_count:
            raise AutomationError("transport provider count moved backwards")
        if transport.physical_submit_calls < run.physical_submit_count:
            raise AutomationError("transport submit count moved backwards")
        run.provider_call_count = transport.physical_calls
        run.physical_submit_count = transport.physical_submit_calls
        if run.provider_call_count > 16 or run.physical_submit_count > 1:
            raise AutomationError("automation physical call cap was exceeded")
        if isinstance(transport, FixtureAutomationTransport) and transport.physical_calls != 0:
            raise AutomationError("fixture transport performed a physical call")
        result = run.projection()
        self.store.processed_ticks[key] = result
        return result

    def _advance(
        self,
        run: AutomationRun,
        now: datetime,
        inputs: AutomationInputs,
        transport: AutomationFixtureTransportPort,
    ) -> None:
        if run.state in _TERMINAL_STATES:
            return
        if run.session_date != inputs.session_date:
            self._halt(run, now, "SESSION_DRIFT")
            return
        if not inputs.account_digest_matches:
            self._halt(run, now, "ACCOUNT_DRIFT")
            return
        if inputs.kill_switch_active and run.state not in {
            "RECONCILING_PREVIOUS",
            "ORDER_SUBMITTED",
            "PENDING_RECONCILIATION",
        }:
            self._halt(run, now, "KILL_SWITCH")
            return
        if run.state == "SCHEDULED":
            self._scheduled(run, now, inputs)
        elif run.state == "PRECHECK":
            if inputs.unfinished_previous_order:
                self._transition(run, "RECONCILING_PREVIOUS", "RUN_TRANSITIONED", now)
            else:
                self._select(run, now, inputs)
        elif run.state == "RECONCILING_PREVIOUS":
            outcome = transport.reconcile(None)
            if outcome == "UNRESOLVED":
                self._transition(run, "PENDING_RECONCILIATION", "ACCOUNT_RECONCILED", now)
            else:
                self._select(run, now, inputs)
        elif run.state == "EXIT_SELECTED":
            self._transition(run, "RISK_CHECKING", "RISK_RESULT_RECORDED", now)
        elif run.state == "BUY_CANDIDATE_SELECTED":
            self._transition(run, "NEWS_CHECKING", "RUN_TRANSITIONED", now)
        elif run.state == "NEWS_CHECKING":
            verdict = transport.vertex(_required(run.selected_symbol))
            run.vertex_call_count += 1
            if verdict in {"VETO_BUY", "ABSTAIN"}:
                self._transition(run, "NEWS_VETOED", "NEWS_RESULT_RECORDED", now)
            else:
                self._transition(run, "RISK_CHECKING", "NEWS_RESULT_RECORDED", now)
        elif run.state == "RISK_CHECKING":
            if inputs.kill_switch_active:
                self._halt(run, now, "KILL_SWITCH")
            elif not inputs.risk_allow:
                self._release_exit_pending(run)
                self._transition(run, "SKIPPED_NO_ACTION", "RISK_RESULT_RECORDED", now)
            else:
                self._transition(run, "ORDER_SUBMITTING", "RISK_RESULT_RECORDED", now)
        elif run.state == "ORDER_SUBMITTING":
            self._submitting(run, now, inputs, transport)
        elif run.state in {"ORDER_SUBMITTED", "PENDING_RECONCILIATION"}:
            self._reconcile_order(run, now, inputs, transport)

    def _scheduled(self, run: AutomationRun, now: datetime, inputs: AutomationInputs) -> None:
        if not _is_xkrx_session(run.session_date):
            self._transition(run, "SKIPPED_NO_ACTION", "RUN_TRANSITIONED", now)
            return
        local_time = now.timetz().replace(tzinfo=None)
        if now.date() != run.session_date or local_time > _KST_CLOSE_ORDER_TIME:
            self._transition(run, "SKIPPED_LATE_START", "RUN_TRANSITIONED", now)
            return
        if self.store.control_state == "DISARMED":
            self._transition(run, "SKIPPED_NO_ACTION", "RUN_TRANSITIONED", now)
            return
        if self.store.control_state != "ARMED":
            self._halt(run, now, "CONTROL_NOT_ARMED")
            return
        if not inputs.account_complete or not inputs.no_open_order:
            self._transition(run, "SKIPPED_DATA_UNAVAILABLE", "RUN_TRANSITIONED", now)
            return
        if not (
            inputs.release_active
            and inputs.daily_shard_fresh_complete
            and inputs.principle_active_current
        ):
            self._transition(run, "SKIPPED_DATA_UNAVAILABLE", "RUN_TRANSITIONED", now)
            return
        if self.store.brokerage_mode == "KIS_MOCK" and self.store.certification_status != "VALID":
            self._transition(run, "SKIPPED_DATA_UNAVAILABLE", "RUN_TRANSITIONED", now)
            return
        self._transition(run, "PRECHECK", "RUN_TRANSITIONED", now)

    def _select(self, run: AutomationRun, now: datetime, inputs: AutomationInputs) -> None:
        positions = [position for position in self.store.positions if position.status == "OPEN"]
        signals = {candidate.symbol: candidate for candidate in inputs.signals}
        exits = sorted(
            (
                position
                for position in positions
                if run.session_date >= position.expiry_session
                or (
                    (signal := signals.get(position.symbol)) is not None
                    and signal.lstm_signal == signal.baseline_signal == "SELL"
                )
            ),
            key=lambda position: (
                -_session_distance(position.expiry_session, run.session_date),
                position.entry_session,
                position.symbol,
            ),
        )
        if exits:
            run.selected_symbol = exits[0].symbol
            run.selected_side = "SELL"
            exits[0].status = "EXIT_PENDING"
            self._transition(run, "EXIT_SELECTED", "EXIT_SELECTED", now)
            return
        held = {position.symbol for position in positions} | set(inputs.manual_position_symbols)
        buys = sorted(
            (
                candidate
                for candidate in inputs.signals
                if candidate.lstm_signal == candidate.baseline_signal == "BUY"
                and candidate.symbol not in held
                and math.isfinite(candidate.expected_return)
                and math.isfinite(candidate.confidence)
            ),
            key=lambda candidate: (
                -candidate.expected_return,
                -candidate.confidence,
                candidate.symbol,
            ),
        )
        if not buys:
            self._transition(run, "SKIPPED_NO_ACTION", "RUN_TRANSITIONED", now)
            return
        run.selected_symbol = buys[0].symbol
        run.selected_side = "BUY"
        self._transition(run, "BUY_CANDIDATE_SELECTED", "BUY_SELECTED", now)

    def _submitting(
        self,
        run: AutomationRun,
        now: datetime,
        inputs: AutomationInputs,
        transport: AutomationFixtureTransportPort,
    ) -> None:
        if now.timetz().replace(tzinfo=None) > _KST_CLOSE_ORDER_TIME:
            self._release_exit_pending(run)
            self._transition(run, "SKIPPED_LATE_START", "RUN_TRANSITIONED", now)
            return
        if not inputs.account_complete:
            self._release_exit_pending(run)
            self._transition(run, "SKIPPED_DATA_UNAVAILABLE", "RUN_TRANSITIONED", now)
            return
        if run.reservation is None:
            if run.selected_side == "BUY" and inputs.buyable_quantity < 1:
                self._transition(run, "SKIPPED_NO_ACTION", "RUN_TRANSITIONED", now)
                return
            quote = transport.quote(_required(run.selected_symbol))
            if not quote.fresh:
                self._release_exit_pending(run)
                self._transition(run, "SKIPPED_DATA_UNAVAILABLE", "RUN_TRANSITIONED", now)
                return
            side = cast(Side, _required(run.selected_side))
            price = _limit_price(quote, side)
            run.reservation = OrderReservation(quote.symbol, side, 1, price)
            run.updated_at = now
            self.store.append_event(
                run,
                "ORDER_RESERVED",
                {"side": run.selected_side, "symbol": quote.symbol},
                now,
            )
            return
        if run.logical_submit_count >= 1:
            self._halt(run, now, "DUPLICATE_SUBMIT_ATTEMPT")
            return
        reserved_run = self.store.session_submit_reservations.get(run.session_date)
        if reserved_run not in {None, run.run_id}:
            self._halt(run, now, "SESSION_SUBMIT_CAP_EXHAUSTED")
            return
        self.store.session_submit_reservations[run.session_date] = run.run_id
        outcome = transport.submit(run.reservation)
        run.logical_submit_count += 1
        run.submit_outcome = outcome
        if outcome == "AMBIGUOUS":
            self._transition(run, "PENDING_RECONCILIATION", "ORDER_OUTCOME_RECORDED", now)
        else:
            self._transition(run, "ORDER_SUBMITTED", "ORDER_OUTCOME_RECORDED", now)

    def _reconcile_order(
        self,
        run: AutomationRun,
        now: datetime,
        inputs: AutomationInputs,
        transport: AutomationFixtureTransportPort,
    ) -> None:
        outcome = transport.reconcile(run.reservation)
        if run.reservation is None:
            if outcome == "UNRESOLVED":
                return
            self._select(run, now, inputs)
            return
        if outcome == "FILLED":
            self._apply_fill(run, now)
            self._transition(run, "COMPLETED", "ACCOUNT_RECONCILED", now)
            return
        if outcome == "UNRESOLVED":
            if run.state != "PENDING_RECONCILIATION":
                self._transition(run, "PENDING_RECONCILIATION", "ACCOUNT_RECONCILED", now)
            return
        if now.timetz().replace(tzinfo=None) < _CANCEL_TIME:
            if run.state != "PENDING_RECONCILIATION":
                self._transition(run, "PENDING_RECONCILIATION", "ACCOUNT_RECONCILED", now)
            return
        reservation = run.reservation
        if reservation is None or not transport.cancel(reservation):
            self._halt(run, now, "CANCEL_FAILED")
            return
        self._release_exit_pending(run)
        self._transition(run, "CANCELLED_UNFILLED", "CANCEL_RECORDED", now)

    def _release_exit_pending(self, run: AutomationRun) -> None:
        if run.selected_side != "SELL":
            return
        for position in self.store.positions:
            if position.symbol == run.selected_symbol and position.status == "EXIT_PENDING":
                position.status = "OPEN"

    def _apply_fill(self, run: AutomationRun, now: datetime) -> None:
        symbol = _required(run.selected_symbol)
        if run.selected_side == "BUY":
            if any(
                position.symbol == symbol and position.status != "CLOSED"
                for position in self.store.positions
            ):
                raise AutomationError("bot position quantity would exceed one")
            seed = f"{run.run_id}:{symbol}:{run.session_date}".encode()
            self.store.positions.append(
                BotPosition(
                    position_id=f"auto_pos_{hashlib.sha256(seed).hexdigest()[:24]}",
                    account_id=self.store.account_id,
                    symbol=symbol,
                    entry_session=run.session_date,
                    expiry_session=_nth_next_session(run.session_date, 5),
                    created_at=now,
                )
            )
        else:
            matches = [
                position
                for position in self.store.positions
                if position.symbol == symbol and position.status in {"OPEN", "EXIT_PENDING"}
            ]
            if len(matches) != 1:
                raise AutomationError("SELL fill does not match one bot-owned lot")
            matches[0].status = "CLOSED"
            matches[0].closed_at = now

    def _transition(
        self,
        run: AutomationRun,
        state: str,
        event_type: str,
        now: datetime,
    ) -> None:
        run.state = state
        run.updated_at = now
        self.store.append_event(
            run,
            event_type,
            {"side": run.selected_side, "state": state, "symbol": run.selected_symbol},
            now,
        )

    def _halt(self, run: AutomationRun, now: datetime, reason: str) -> None:
        self.store.control_state = "HALTED"
        self.store.version += 1
        self._transition(run, "HALTED", "RUN_HALTED", now)
        self.store.append_event(run, "DRIFT_DETECTED", {"reason": reason}, now)


def _limit_price(quote: Quote, side: Side) -> int:
    if quote.price_krw <= 0 or quote.lower_limit_krw <= 0 or quote.upper_limit_krw <= 0:
        raise AutomationError("quote prices are invalid")
    tick = _tick_size(quote.price_krw, quote.is_etf_etn)
    price = quote.price_krw + tick if side == "BUY" else quote.price_krw - tick
    return min(quote.upper_limit_krw, price) if side == "BUY" else max(quote.lower_limit_krw, price)


def _tick_size(price: int, is_etf_etn: bool) -> int:
    if is_etf_etn:
        return 1 if price < 2_000 else 5
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def _is_xkrx_session(session_date: date) -> bool:
    _calendar()
    return bool(xcals.get_calendar("XKRX").is_session(pd.Timestamp(session_date)))


def _nth_next_session(session_date: date, count: int) -> date:
    calendar = _calendar()
    current = calendar.date_to_session(pd.Timestamp(session_date), direction="none")
    for _ in range(count):
        current = calendar.next_session(current)
    return cast(date, current.date())


def _session_distance(start: date, end: date) -> int:
    if end < start:
        return 0
    calendar = _calendar()
    return len(calendar.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))) - 1


def _calendar() -> Any:
    if version("exchange-calendars") != "4.13.2":
        raise AutomationError("XKRX calendar version drifted")
    return xcals.get_calendar("XKRX")


def _required(value: str | None) -> str:
    if value is None:
        raise AutomationError("required automation selection is missing")
    return value


def _validate_id(value: str, prefix: str) -> None:
    if not value.startswith(prefix) or not 8 <= len(value.removeprefix(prefix)) <= 96:
        raise AutomationError("automation identifier is invalid")


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise AutomationError("automation timestamps must be timezone aware")
    return value.isoformat()
