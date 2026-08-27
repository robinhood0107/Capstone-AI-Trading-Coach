"""V90 PostgreSQL에 결속된 KIS_MOCK automation runtime과 XKRX boundary loop."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import signal
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from importlib.metadata import version
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from app.data._shared.canonical_json import canonical_json_bytes
from app.p1_owner.automation import (
    AutomationEngine,
    AutomationInputs,
    AutomationRun,
    AutomationStore,
    BotPosition,
    OrderReservation,
    SignalCandidate,
)

_KST = ZoneInfo("Asia/Seoul")
_OPEN_BOUNDARY = time(9, 10)
_CANCEL_BOUNDARY = time(15, 20)
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_USER_ID = re.compile(r"^usr_[A-Za-z0-9_-]{8,96}$")
_RUN_ID = re.compile(r"^auto_run_[0-9a-f]{32}$")
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


class AutomationRuntimeError(RuntimeError):
    """Persistent runtime의 DB, clock, state 또는 adapter 계약이 닫혔다."""


@dataclass(frozen=True, slots=True)
class RuntimeClaim:
    user_id: str
    run_id: str
    control_version: int
    account_id: str
    principle_id: str
    strategy_id: str
    baseline_account_digest: str
    replayed: bool
    session_date: date
    claim_token_hash: str


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    markers: dict[str, bool]
    current_control_version: int
    all_ready: bool


@dataclass(frozen=True, slots=True)
class AdvanceCommand:
    run_id: str
    claim_token_hash: str
    tick_identity_hash: str
    expected_version: int
    next_state: str
    selected_symbol: str | None
    selected_side: str | None
    decision_id: str | None
    vertex_call_count: int
    provider_call_count: int
    logical_submit_count: int
    reservation_id: str | None
    limit_price_krw: int | None
    position_expiry_session: date | None
    order_id: str | None
    provider_order_ref_hash: str | None
    result_hash: str
    event_type: str
    event_payload_hash: str


class AutomationRuntimePort(Protocol):
    """Spring/KIS/Vertex adapter가 engine transport와 tick별 입력을 함께 제공한다."""

    physical_calls: int
    physical_submit_calls: int
    quote_calls: int
    vertex_calls: int
    submit_calls: int
    reconcile_calls: int
    cancel_calls: int
    order_id: str | None
    provider_order_ref_hash: str | None
    decision_id: str | None

    def inputs(
        self,
        *,
        state: dict[str, Any],
        run: AutomationRun,
        now: datetime,
    ) -> AutomationInputs: ...

    def quote(self, symbol: str) -> Any: ...

    def vertex(self, symbol: str) -> Any: ...

    def submit(self, reservation: OrderReservation) -> Any: ...

    def reconcile(self, reservation: OrderReservation | None) -> Any: ...

    def cancel(self, reservation: OrderReservation) -> bool: ...

    def close(self) -> None: ...


class AutomationRuntimePortFactory(Protocol):
    def build(self, claim: RuntimeClaim, state: dict[str, Any]) -> AutomationRuntimePort: ...


class PostgresAutomationRuntimeRepository:
    """decision_automation_runtime 함수 allowlist만 호출하는 V90 adapter다."""

    def __init__(self, database_dsn: str) -> None:
        try:
            parsed = conninfo_to_dict(database_dsn)
        except psycopg.Error as error:
            raise AutomationRuntimeError("AUTOMATION_RUNTIME_DSN_INVALID") from error
        if (
            parsed.get("user") != "decision_automation_runtime"
            or parsed.get("host") not in {"postgres", "127.0.0.1", "localhost"}
            or not parsed.get("dbname")
        ):
            raise AutomationRuntimeError("AUTOMATION_RUNTIME_DSN_ROLE_INVALID")
        self._database_dsn = database_dsn

    def preflight(self) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("select current_user,session_user")
            if cursor.fetchone() != ("decision_automation_runtime", "decision_automation_runtime"):
                raise AutomationRuntimeError("AUTOMATION_RUNTIME_ROLE_MISMATCH")

    def readiness(self, user_id: str, target_session: date) -> ReadinessResult:
        _require_user_id(user_id)
        with self._connect(row_factory=dict_row) as connection, connection.cursor() as cursor:
            cursor.execute(
                "select * from p1_automation_runtime_readiness_v1(%s,%s)",
                (user_id, target_session),
            )
            row = cursor.fetchone()
            if row is None:
                raise AutomationRuntimeError("AUTOMATION_READINESS_UNAVAILABLE")
        marker_keys = (
            "control_configured",
            "certification_valid",
            "release_source_bound",
            "real_team_b_ready",
            "principle_current",
            "kill_switch_inactive",
            "account_baseline_matches",
            "unresolved_state_clear",
            "target_available",
        )
        return ReadinessResult(
            markers={key: bool(row[key]) for key in marker_keys},
            current_control_version=int(row["current_control_version"]),
            all_ready=bool(row["all_ready"]),
        )

    def start(
        self,
        user_id: str,
        target_session: date,
        expected_control_version: int,
    ) -> tuple[str, int, bool]:
        _require_user_id(user_id)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select schedule_id,control_version,replayed "
                "from p1_start_automation_runtime_v1(%s,%s,%s)",
                (user_id, target_session, expected_control_version),
            )
            row = cursor.fetchone()
            if row is None:
                raise AutomationRuntimeError("AUTOMATION_START_UNAVAILABLE")
            return str(row[0]), int(row[1]), bool(row[2])

    def stop(self, user_id: str, expected_control_version: int) -> tuple[int, bool]:
        _require_user_id(user_id)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select control_version,replayed from p1_stop_automation_runtime_v1(%s,%s)",
                (user_id, expected_control_version),
            )
            row = cursor.fetchone()
            if row is None:
                raise AutomationRuntimeError("AUTOMATION_STOP_UNAVAILABLE")
            return int(row[0]), bool(row[1])

    def roll_schedule(
        self,
        user_id: str,
        completed_session: date,
        next_session: date,
        expected_control_version: int,
    ) -> str:
        _require_user_id(user_id)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select p1_roll_automation_schedule_v1(%s,%s,%s,%s)",
                (user_id, completed_session, next_session, expected_control_version),
            )
            row = cursor.fetchone()
            if row is None or not isinstance(row[0], str):
                raise AutomationRuntimeError("AUTOMATION_ROLL_UNAVAILABLE")
            return row[0]

    def claim(self, session_date: date, claim_token_hash: str) -> RuntimeClaim | None:
        _require_hash(claim_token_hash)
        with self._connect(row_factory=dict_row) as connection, connection.cursor() as cursor:
            cursor.execute(
                "select * from p1_claim_automation_session_v1(%s,%s)",
                (session_date, claim_token_hash),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return RuntimeClaim(
                user_id=str(row["user_id"]),
                run_id=str(row["run_id"]),
                control_version=int(row["control_version"]),
                account_id=str(row["account_id"]),
                principle_id=str(row["principle_id"]),
                strategy_id=str(row["strategy_id"]),
                baseline_account_digest=str(row["baseline_account_digest"]),
                replayed=bool(row["replayed"]),
                session_date=session_date,
                claim_token_hash=claim_token_hash,
            )

    def read_state(self, claim: RuntimeClaim) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select p1_read_automation_runtime_state_v1(%s,%s)",
                (claim.run_id, claim.claim_token_hash),
            )
            row = cursor.fetchone()
            if row is None or not isinstance(row[0], str):
                raise AutomationRuntimeError("AUTOMATION_STATE_UNAVAILABLE")
        try:
            value = json.loads(row[0])
        except json.JSONDecodeError as error:
            raise AutomationRuntimeError("AUTOMATION_STATE_INVALID") from error
        if not isinstance(value, dict) or value.get("runId") != claim.run_id:
            raise AutomationRuntimeError("AUTOMATION_STATE_IDENTITY_MISMATCH")
        return cast(dict[str, Any], value)

    def advance(self, command: AdvanceCommand) -> tuple[int, bool]:
        _require_hash(command.claim_token_hash)
        _require_hash(command.tick_identity_hash)
        _require_hash(command.result_hash)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select checkpoint_version,replayed from p1_advance_automation_checkpoint_v1("
                "%s::text,%s::text,%s::text,%s::integer,%s::text,%s::text,%s::text,"
                "%s::text,%s::integer,%s::integer,%s::integer,%s::text,%s::bigint,%s::date,"
                "%s::text,%s::text,%s::text,%s::text,%s::text)",
                (
                    command.run_id,
                    command.claim_token_hash,
                    command.tick_identity_hash,
                    command.expected_version,
                    command.next_state,
                    command.selected_symbol,
                    command.selected_side,
                    command.decision_id,
                    command.vertex_call_count,
                    command.provider_call_count,
                    command.logical_submit_count,
                    command.reservation_id,
                    command.limit_price_krw,
                    command.position_expiry_session,
                    command.order_id,
                    command.provider_order_ref_hash,
                    command.result_hash,
                    command.event_type,
                    command.event_payload_hash,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise AutomationRuntimeError("AUTOMATION_ADVANCE_UNAVAILABLE")
            return int(row[0]), bool(row[1])

    def _connect(
        self,
        *,
        row_factory: Any | None = None,
    ) -> psycopg.Connection[Any]:
        options: dict[str, Any] = {
            "autocommit": False,
            "connect_timeout": 2,
        }
        if row_factory is not None:
            options["row_factory"] = row_factory
        return psycopg.connect(self._database_dsn, **options)


class PersistentAutomationRunner:
    """각 tick을 DB CAS 하나로 봉인하고 process restart마다 state를 다시 읽는다."""

    def __init__(self, repository: PostgresAutomationRuntimeRepository) -> None:
        self._repository = repository

    def run_tick(
        self,
        *,
        claim: RuntimeClaim,
        tick_id: str,
        now: datetime,
        port: AutomationRuntimePort,
    ) -> dict[str, object]:
        state = self._repository.read_state(claim)
        store, run = _store_from_state(claim, state)
        if run.state in _TERMINAL_STATES:
            return run.projection()
        inputs = port.inputs(state=state, run=run, now=now)
        result = AutomationEngine(store).tick(
            run_id=claim.run_id,
            tick_id=tick_id,
            now=now,
            inputs=inputs,
            transport=port,
        )
        event = store.events[-1] if store.events else None
        if event is None:
            raise AutomationRuntimeError("AUTOMATION_TICK_WITHOUT_DURABLE_EVENT")
        reservation = run.reservation
        expiry = _new_position_expiry(store, run)
        result_hash = _sha(canonical_json_bytes(result))
        command = AdvanceCommand(
            run_id=claim.run_id,
            claim_token_hash=claim.claim_token_hash,
            tick_identity_hash=_sha(tick_id.encode()),
            expected_version=int(state["checkpointVersion"]),
            next_state=run.state,
            selected_symbol=run.selected_symbol,
            selected_side=run.selected_side,
            decision_id=port.decision_id,
            vertex_call_count=run.vertex_call_count,
            provider_call_count=run.provider_call_count,
            logical_submit_count=run.logical_submit_count,
            reservation_id=_reservation_id(claim.run_id, reservation) if reservation else None,
            limit_price_krw=reservation.limit_price_krw if reservation else None,
            position_expiry_session=expiry,
            order_id=port.order_id,
            provider_order_ref_hash=port.provider_order_ref_hash,
            result_hash=result_hash,
            event_type=str(event["eventType"]),
            event_payload_hash=str(event["payloadHash"]),
        )
        self._repository.advance(command)
        return result


class XkrxBoundaryPlanner:
    """반복 heartbeat 대신 현재 durable state에서 다음 한 boundary만 계산한다."""

    def __init__(self) -> None:
        if version("exchange-calendars") != "4.13.2":
            raise AutomationRuntimeError("XKRX_CALENDAR_VERSION_DRIFT")
        self._calendar = xcals.get_calendar("XKRX")

    def current_or_next_session(self, now: datetime) -> date:
        local = _kst(now)
        current_date = local.date()
        stamp = pd.Timestamp(current_date)
        if self._calendar.is_session(stamp):
            if local.timetz().replace(tzinfo=None) <= _CANCEL_BOUNDARY:
                return current_date
            return cast(date, self._calendar.next_session(stamp).date())
        return cast(date, self._calendar.date_to_session(stamp, direction="next").date())

    def next_session(self, current: date) -> date:
        session = self._calendar.date_to_session(pd.Timestamp(current), direction="none")
        return cast(date, self._calendar.next_session(session).date())

    def next_wakeup(self, now: datetime, state: str | None = None) -> datetime:
        local = _kst(now)
        session = self.current_or_next_session(local)
        if state == "PENDING_RECONCILIATION":
            cancel = datetime.combine(session, _CANCEL_BOUNDARY, _KST)
            if local < cancel:
                return cancel
        opening = datetime.combine(session, _OPEN_BOUNDARY, _KST)
        if local < opening:
            return opening
        if local.date() == session and local.timetz().replace(tzinfo=None) <= _CANCEL_BOUNDARY:
            return local
        next_session = self.next_session(session)
        return datetime.combine(next_session, _OPEN_BOUNDARY, _KST)


class AutomationRuntimeService:
    """explicit enable 뒤에만 session claim을 처리하며 다음 XKRX boundary까지 block한다."""

    def __init__(
        self,
        repository: PostgresAutomationRuntimeRepository,
        port_factory: AutomationRuntimePortFactory,
        shared_secret: str,
        planner: XkrxBoundaryPlanner | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._~:-]{32,256}", shared_secret):
            raise AutomationRuntimeError("AUTOMATION_RUNTIME_SECRET_INVALID")
        self._repository = repository
        self._port_factory = port_factory
        self._shared_secret = shared_secret.encode()
        self._planner = planner or XkrxBoundaryPlanner()
        self._runner = PersistentAutomationRunner(repository)
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def serve(self) -> None:
        self._repository.preflight()
        while not self._stop.is_set():
            now = datetime.now(UTC).astimezone(_KST)
            wakeup = self._planner.next_wakeup(now)
            if wakeup > now and self._stop.wait((wakeup - now).total_seconds()):
                return
            session_date = self._planner.current_or_next_session(wakeup)
            claim_hash = _claim_hash(self._shared_secret, session_date)
            claim = self._repository.claim(session_date, claim_hash)
            if claim is None:
                next_wakeup = datetime.combine(self._planner.next_session(session_date), _OPEN_BOUNDARY, _KST)
                if self._stop.wait(max(0.0, (next_wakeup - datetime.now(UTC).astimezone(_KST)).total_seconds())):
                    return
                continue
            self._drive_claim(claim)

    def _drive_claim(self, claim: RuntimeClaim) -> None:
        state = self._repository.read_state(claim)
        port = self._port_factory.build(claim, state)
        try:
            index = int(state["checkpointVersion"])
            while not self._stop.is_set():
                state = self._repository.read_state(claim)
                current = str(state["state"])
                if current in _TERMINAL_STATES:
                    return
                now = datetime.now(UTC).astimezone(_KST)
                wakeup = self._planner.next_wakeup(now, current)
                if wakeup > now and self._stop.wait((wakeup - now).total_seconds()):
                    return
                index += 1
                result = self._runner.run_tick(
                    claim=claim,
                    tick_id=f"{claim.run_id}:boundary:{index}",
                    now=wakeup,
                    port=port,
                )
                next_state = str(result["state"])
                if next_state in _TERMINAL_STATES:
                    if next_state != "HALTED":
                        try:
                            self._repository.roll_schedule(
                                claim.user_id,
                                claim.session_date,
                                self._planner.next_session(claim.session_date),
                                claim.control_version,
                            )
                        except (AutomationRuntimeError, psycopg.Error):
                            # stop/disarm 또는 gate drift는 다음 session을 만들지 않는 정상 fail-close다.
                            pass
                    return
                if next_state in {"ORDER_SUBMITTED", "PENDING_RECONCILIATION"} and wakeup.time() < _CANCEL_BOUNDARY:
                    continue
        finally:
            port.close()


def _store_from_state(
    claim: RuntimeClaim,
    state: dict[str, Any],
) -> tuple[AutomationStore, AutomationRun]:
    session_date = date.fromisoformat(str(state["sessionDate"]))
    store = AutomationStore(
        account_id=str(state["accountId"]),
        brokerage_mode=str(state["brokerageMode"]),
        principle_id=str(state["principleId"]),
        strategy_id=str(state["strategyId"]),
        baseline_account_digest=str(state["baselineAccountDigest"]),
        control_state=str(state["controlState"]),
        version=int(state["controlVersion"]),
        certification_status="VALID",
        baseline_event_recorded=True,
    )
    run = AutomationRun(
        run_id=claim.run_id,
        session_date=session_date,
        brokerage_mode=str(state["brokerageMode"]),
        started_at=_timestamp(state["runStartedAt"]),
        updated_at=_timestamp(state["runStartedAt"]),
        state=str(state["state"]),
        selected_symbol=_optional_text(state.get("selectedSymbol")),
        selected_side=cast(Any, _optional_text(state.get("selectedSide"))),
        vertex_call_count=int(state["vertexCallCount"]),
        logical_submit_count=int(state["logicalSubmitCount"]),
        physical_submit_count=int(state["logicalSubmitCount"]),
        provider_call_count=int(state["providerCallCount"]),
    )
    reservation = state.get("reservation")
    if isinstance(reservation, dict):
        run.reservation = OrderReservation(
            symbol=str(reservation["symbol"]),
            side=cast(Any, str(reservation["side"])),
            quantity=int(reservation["quantity"]),
            limit_price_krw=int(reservation["limitPriceKrw"]),
        )
    store.runs[run.run_id] = run
    if run.logical_submit_count:
        store.session_submit_reservations[session_date] = run.run_id
    raw_positions = state.get("positions")
    if not isinstance(raw_positions, list):
        raise AutomationRuntimeError("AUTOMATION_POSITIONS_INVALID")
    for item in raw_positions:
        if not isinstance(item, dict):
            raise AutomationRuntimeError("AUTOMATION_POSITION_INVALID")
        store.positions.append(
            BotPosition(
                position_id=str(item["positionId"]),
                account_id=str(item["accountId"]),
                symbol=str(item["symbol"]),
                entry_session=date.fromisoformat(str(item["entrySession"])),
                expiry_session=date.fromisoformat(str(item["expirySession"])),
                created_at=_timestamp(item["createdAt"]),
                status=str(item["status"]),
                closed_at=_timestamp(item["closedAt"]) if item.get("closedAt") else None,
            )
        )
    return store, run


def inputs_from_state(
    state: dict[str, Any],
    *,
    risk_allow: bool,
    buyable_quantity: int,
) -> AutomationInputs:
    """V90 sanitized state와 Spring 실시간 gate를 engine input으로 엄격 변환한다."""

    raw_signals = state.get("signals")
    if not isinstance(raw_signals, list):
        raise AutomationRuntimeError("AUTOMATION_SIGNALS_INVALID")
    signals = tuple(
        SignalCandidate(
            symbol=str(item["symbol"]),
            lstm_signal=cast(Any, str(item["lstmSignal"])),
            baseline_signal=cast(Any, str(item["baselineSignal"])),
            expected_return=float(item["expectedReturn"]),
            confidence=float(item["confidence"]),
        )
        for item in raw_signals
        if isinstance(item, dict)
    )
    manual = state.get("manualPositionSymbols")
    if not isinstance(manual, list) or not all(isinstance(item, str) for item in manual):
        raise AutomationRuntimeError("AUTOMATION_MANUAL_POSITIONS_INVALID")
    return AutomationInputs(
        session_date=date.fromisoformat(str(state["sessionDate"])),
        release_active=bool(state["releaseActive"]),
        daily_shard_fresh_complete=bool(state["dailyShardFreshComplete"]),
        principle_active_current=bool(state["principleActiveCurrent"]),
        risk_allow=risk_allow,
        kill_switch_active=bool(state["killSwitchActive"]),
        account_complete=bool(state["accountComplete"]),
        account_digest_matches=bool(state["accountDigestMatches"]),
        buyable_quantity=buyable_quantity,
        no_open_order=bool(state["noOpenOrder"]),
        unfinished_previous_order=bool(state["unfinishedPreviousOrder"]),
        manual_position_symbols=frozenset(cast(list[str], manual)),
        signals=signals,
    )


def _new_position_expiry(store: AutomationStore, run: AutomationRun) -> date | None:
    if run.state != "COMPLETED" or run.selected_side != "BUY":
        return None
    matches = [
        item
        for item in store.positions
        if item.symbol == run.selected_symbol and item.entry_session == run.session_date
    ]
    return matches[0].expiry_session if len(matches) == 1 else None


def _reservation_id(run_id: str, reservation: OrderReservation) -> str:
    content = (
        f"{run_id}:{reservation.symbol}:{reservation.side}:"
        f"{reservation.quantity}:{reservation.limit_price_krw}"
    ).encode()
    return f"auto_res_{hashlib.sha256(content).hexdigest()[:32]}"


def _claim_hash(secret: bytes, session_date: date) -> str:
    digest = hmac.new(secret, f"p1-automation-claim/v1\0{session_date}".encode(), hashlib.sha256)
    return f"sha256:{digest.hexdigest()}"


def _sha(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise AutomationRuntimeError("AUTOMATION_CLOCK_NAIVE")
    return value.astimezone(_KST)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise AutomationRuntimeError("AUTOMATION_TIMESTAMP_INVALID")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise AutomationRuntimeError("AUTOMATION_TIMESTAMP_INVALID")
    return parsed


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _require_hash(value: str) -> None:
    if _HASH.fullmatch(value) is None:
        raise AutomationRuntimeError("AUTOMATION_HASH_INVALID")


def _require_user_id(value: str) -> None:
    if _USER_ID.fullmatch(value) is None:
        raise AutomationRuntimeError("AUTOMATION_USER_ID_INVALID")


def main() -> int:
    if os.environ.get("P1_AUTOMATION_RUNTIME_ENABLED", "false").lower() != "true":
        raise AutomationRuntimeError("AUTOMATION_RUNTIME_DISABLED")
    database_dsn = os.environ.get("P1_AUTOMATION_DATABASE_DSN", "").strip()
    shared_secret = os.environ.get("AUTOMATION_RUNTIME_SHARED_SECRET", "").strip()
    repository = PostgresAutomationRuntimeRepository(database_dsn)
    # import 시 provider client를 만들지 않아 disabled/default supervisor가 socket을 열 수 없다.
    from app.p1_owner.automation_runtime_live import LiveAutomationPortFactory

    service = AutomationRuntimeService(repository, LiveAutomationPortFactory(), shared_secret)
    signal.signal(signal.SIGTERM, lambda _signum, _frame: service.stop())
    signal.signal(signal.SIGINT, lambda _signum, _frame: service.stop())
    service.serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
