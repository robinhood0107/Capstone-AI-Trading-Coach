"""V90 PostgreSQL에 결속된 KIS_MOCK automation runtime과 XKRX boundary loop."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import signal
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
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
from app.p1_owner.automation_journal import AutomationJournal, notice_from_event
from app.strong_llm.prompt import PROMPT_CONTRACT_ID
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
    ExactOrderIntent,
    OrderReservation,
    Quote,
    NewsScreeningBatch,
    SignalCandidate,
)
from app.p1_owner.automation_atr import AtrHistoryError, CompletedDailyBar
from app.p1_owner.daily_inference import DailyInferenceError, DailyInferenceService

_KST = ZoneInfo("Asia/Seoul")
_OPEN_BOUNDARY = time(9, 30)
_SUBMIT_DEADLINE = time(9, 40)
_CANCEL_BOUNDARY = time(15, 20)
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_USER_ID = re.compile(r"^usr_[A-Za-z0-9_-]{8,96}$")
_RUN_ID = re.compile(r"^auto_run_[0-9a-f]{32}$")
_LEGACY_AI_SETTINGS_SHA256 = hashlib.sha256(
    b'{"aiJudgementEnabled":false,"thinkingLevel":"low"}'
).hexdigest()
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
    quantity: int | None
    limit_price_krw: int | None
    exact_intent_json: str | None
    exact_intent_sha256: str | None
    quote_snapshot_json: str | None
    policy_id: str | None
    policy_version: int | None
    position_expiry_session: date | None
    filled_quantity: int
    leaves_quantity: int
    unfilled_terminated_quantity: int
    average_fill_price_krw: int | None
    exit_reason: str | None
    expected_account_digest: str | None
    order_id: str | None
    provider_order_ref_hash: str | None
    result_hash: str
    event_type: str
    event_payload_hash: str
    position_state_json: str


@dataclass(frozen=True, slots=True)
class AccountLineageAdvance:
    """자기 체결로 설명되는 계좌 이동. 기대 투영을 여기까지 전진시킨다."""

    reason: str
    projection: dict[str, object]
    digest: str
    order_id: str
    filled_quantity: int
    average_fill_price_krw: int


@dataclass(frozen=True, slots=True)
class AiJudgementRecord:
    """AI가 바꾼 순위와 VETO만 남기는 current 판단 기록."""

    checkpoint_version: int
    participation: str
    provider_id: str
    prompt_version: str
    baseline_symbol: str | None
    selected_symbol: str | None
    vetoed_symbol_count: int
    judge_call_count: int
    candidate_count: int
    verdicts_json: str
    ai_settings_sha256: str
    evidence_set_sha256: str | None
    grounding_call_count: int
    grounding_query_count: int
    evidence_count: int


class AutomationRuntimePort(Protocol):
    """Spring/KIS/Vertex adapter가 engine transport와 tick별 입력을 함께 제공한다."""

    physical_calls: int
    physical_submit_calls: int
    quote_calls: int
    vertex_calls: int
    judge_calls: int
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

    def screen(
        self,
        candidates: tuple[SignalCandidate, ...],
        quotes: Mapping[str, Quote],
        candidate_set_sha256: str,
    ) -> NewsScreeningBatch: ...

    def vertex(self, symbol: str) -> Any: ...

    def judge(self, candidates: tuple[Any, ...], candidate_set_sha256: str) -> Any: ...

    # 판단을 실제로 받았을 때만 채워진다. 못 받았으면 None이고 기록은 미참여로 남는다.
    last_judgement_json: str | None

    def submit(self, reservation: OrderReservation) -> Any: ...

    def reconcile(self, reservation: OrderReservation | None) -> Any: ...

    def cancel(self, reservation: OrderReservation) -> bool: ...

    def account_lineage_advance(
        self,
        *,
        symbol: str,
        side: str,
        filled_quantity: int,
        average_fill_price_krw: int,
    ) -> AccountLineageAdvance | None: ...

    def close(self) -> None: ...


class AutomationRuntimePortFactory(Protocol):
    def build(self, claim: RuntimeClaim, state: dict[str, Any]) -> AutomationRuntimePort: ...


class DailyInferencePort(Protocol):
    def ensure_daily_signals(self, target_session: date) -> object: ...

    def close(self) -> None: ...


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

    def advance_account_lineage(self, claim: RuntimeClaim, lineage: AccountLineageAdvance) -> int:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select p1_advance_automation_account_lineage_v3(%s,%s,%s,%s::jsonb,%s,%s,%s,%s)",
                (
                    claim.run_id,
                    claim.claim_token_hash,
                    lineage.reason,
                    canonical_json_bytes(lineage.projection).decode(),
                    lineage.digest,
                    lineage.order_id,
                    lineage.filled_quantity,
                    lineage.average_fill_price_krw,
                ),
            )
            row = cursor.fetchone()
            if row is None or not isinstance(row[0], int):
                raise AutomationRuntimeError("AUTOMATION_ACCOUNT_LINEAGE_ADVANCE_FAILED")
            return row[0]

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
                "select p1_read_automation_runtime_state_v4(%s,%s)",
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
        value["aiJudgement"] = self.read_ai_judgement(claim)
        metadata = self.read_v3_metadata(claim)
        if metadata:
            value.update(metadata)
        value.update(self.read_ai_settings_snapshot(claim))
        policy = value.get("policy")
        if isinstance(policy, dict) and policy.get("atrPeriod") is not None:
            value["atrHistories"] = self.read_atr_histories(
                value,
                as_of_session=claim.session_date,
            )
        return cast(dict[str, Any], value)

    def read_v3_metadata(self, claim: RuntimeClaim) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select p1_read_automation_v3_metadata_v1(%s,%s)",
                (claim.run_id, claim.claim_token_hash),
            )
            row = cursor.fetchone()
        if row is None or not isinstance(row[0], str):
            return {}
        try:
            value = json.loads(row[0])
        except json.JSONDecodeError as error:
            raise AutomationRuntimeError("AUTOMATION_V3_METADATA_INVALID") from error
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    def read_ai_settings_snapshot(self, claim: RuntimeClaim) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select p1_read_automation_ai_settings_snapshot_v1(%s,%s)",
                (claim.run_id, claim.claim_token_hash),
            )
            row = cursor.fetchone()
        if row is None or not isinstance(row[0], str):
            raise AutomationRuntimeError("AUTOMATION_AI_SETTINGS_SNAPSHOT_UNAVAILABLE")
        try:
            value = json.loads(row[0])
        except json.JSONDecodeError as error:
            raise AutomationRuntimeError("AUTOMATION_AI_SETTINGS_SNAPSHOT_INVALID") from error
        if not isinstance(value, dict):
            raise AutomationRuntimeError("AUTOMATION_AI_SETTINGS_SNAPSHOT_INVALID")
        return cast(dict[str, Any], value)

    def read_atr_histories(
        self,
        state: dict[str, Any],
        *,
        as_of_session: date,
    ) -> dict[str, list[dict[str, object]]]:
        policy = state.get("policy")
        if not isinstance(policy, dict) or policy.get("atrPeriod") is None:
            raise AutomationRuntimeError("AUTOMATION_ATR_POLICY_INVALID")
        policy_period = int(policy["atrPeriod"])
        required_periods = _required_atr_periods(state, policy_period=policy_period)
        histories: dict[str, list[dict[str, object]]] = {}
        with self._connect(row_factory=dict_row) as connection, connection.cursor() as cursor:
            for symbol, period in sorted(required_periods.items()):
                if period not in range(5, 101):
                    raise AutomationRuntimeError("AUTOMATION_ATR_POLICY_INVALID")
                cursor.execute(
                    "select * from p1_read_automation_atr_bars_v1(%s,%s,%s)",
                    (symbol, as_of_session, min(101, period + 1)),
                )
                histories[symbol] = [
                    {
                        "closePriceKrw": int(row["close_price"]),
                        "highPriceKrw": int(row["high_price"]),
                        "lowPriceKrw": int(row["low_price"]),
                        "openPriceKrw": int(row["open_price"]),
                        "sessionDate": row["session_date"].isoformat(),
                    }
                    for row in cursor.fetchall()
                ]
        return histories

    def read_ai_judgement(self, claim: RuntimeClaim) -> dict[str, Any] | None:
        """이 run이 이미 받은 판단. 없으면 None이고 그때 자동매매는 규칙만으로 돈다."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select p1_read_automation_ai_judgement_v1(%s,%s)",
                (claim.run_id, claim.claim_token_hash),
            )
            row = cursor.fetchone()
        if row is None or not isinstance(row[0], str):
            return None
        try:
            value = json.loads(row[0])
        except json.JSONDecodeError as error:
            raise AutomationRuntimeError("AUTOMATION_AI_JUDGEMENT_INVALID") from error
        return cast(dict[str, Any], value) if isinstance(value, dict) else None

    def record_ai_judgement(self, claim: RuntimeClaim, record: AiJudgementRecord) -> None:
        """Record a replay-safe judgement outside the runner's atomic path."""
        _require_hash(claim.claim_token_hash)
        with self._connect() as connection, connection.cursor() as cursor:
            self._record_ai_judgement(cursor, claim, record)

    def advance(self, command: AdvanceCommand) -> tuple[int, bool]:
        _require_hash(command.claim_token_hash)
        _require_hash(command.tick_identity_hash)
        _require_hash(command.result_hash)
        with self._connect() as connection, connection.cursor() as cursor:
            return self._advance(cursor, command)

    def advance_with_ai_judgement(
        self,
        command: AdvanceCommand,
        claim: RuntimeClaim,
        record: AiJudgementRecord,
    ) -> tuple[int, bool]:
        """Commit the AI boundary and its audit row in one DB transaction."""

        if claim.run_id != command.run_id or claim.claim_token_hash != command.claim_token_hash:
            raise AutomationRuntimeError("AUTOMATION_AI_JUDGEMENT_IDENTITY_MISMATCH")
        _require_hash(command.claim_token_hash)
        _require_hash(command.tick_identity_hash)
        _require_hash(command.result_hash)
        with self._connect() as connection, connection.cursor() as cursor:
            checkpoint_version, replayed = self._advance(cursor, command)
            self._record_ai_judgement(
                cursor,
                claim,
                replace(record, checkpoint_version=checkpoint_version),
            )
            return checkpoint_version, replayed

    @staticmethod
    def _record_ai_judgement(
        cursor: psycopg.Cursor[Any],
        claim: RuntimeClaim,
        record: AiJudgementRecord,
    ) -> None:
        cursor.execute(
            "select p1_record_automation_ai_judgement_v3("
            "%s::text,%s::text,%s::integer,%s::text,%s::text,%s::text,"
            "%s::text,%s::text,%s::integer,%s::integer,%s::integer,"
            "%s::text,%s::text,%s::text,%s::integer,%s::integer,%s::integer)",
            (
                claim.run_id,
                claim.claim_token_hash,
                record.checkpoint_version,
                record.participation,
                record.provider_id,
                record.prompt_version,
                record.baseline_symbol,
                record.selected_symbol,
                record.vetoed_symbol_count,
                record.judge_call_count,
                record.candidate_count,
                record.verdicts_json,
                record.ai_settings_sha256,
                record.evidence_set_sha256,
                record.grounding_call_count,
                record.grounding_query_count,
                record.evidence_count,
            ),
        )

    @staticmethod
    def _advance(cursor: psycopg.Cursor[Any], command: AdvanceCommand) -> tuple[int, bool]:
        cursor.execute(
            "select checkpoint_version,replayed from p1_advance_automation_checkpoint_v3("
            "%s::text,%s::text,%s::text,%s::integer,%s::text,%s::text,%s::text,"
            "%s::text,%s::integer,%s::integer,%s::integer,%s::text,%s::bigint,%s::bigint,"
            "%s::text,%s::text,%s::text,%s::text,%s::integer,%s::date,%s::bigint,"
            "%s::bigint,%s::bigint,%s::bigint,%s::text,%s::text,%s::text,%s::text,%s::text,"
            "%s::text,%s::text,%s::text)",
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
                command.quantity,
                command.limit_price_krw,
                command.exact_intent_json,
                command.exact_intent_sha256,
                command.quote_snapshot_json,
                command.policy_id,
                command.policy_version,
                command.position_expiry_session,
                command.filled_quantity,
                command.leaves_quantity,
                command.unfilled_terminated_quantity,
                command.average_fill_price_krw,
                command.exit_reason,
                command.expected_account_digest,
                command.order_id,
                command.provider_order_ref_hash,
                command.result_hash,
                command.event_type,
                command.event_payload_hash,
                command.position_state_json,
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

    def __init__(
        self,
        repository: PostgresAutomationRuntimeRepository,
        journal: AutomationJournal | None = None,
    ) -> None:
        self._repository = repository
        self._journal = journal or AutomationJournal.from_environment()

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
        intent = reservation.intent if reservation is not None else None
        quote = run.selected_quote
        policy = run.policy_snapshot
        result_hash = _sha(canonical_json_bytes(result))
        position_state_json = canonical_json_bytes(
            [
                {
                    "atrAsOfSession": (
                        position.atr_as_of_session.isoformat()
                        if position.atr_as_of_session
                        else None
                    ),
                    "atrStatus": position.atr_status,
                    "exitReason": position.exit_reason,
                    "expirySession": (
                        position.expiry_session.isoformat() if position.expiry_session else None
                    ),
                    "maxHoldingSessions": position.max_holding_sessions,
                    "modelSellEnabled": position.model_sell_enabled,
                    "atrPeriod": position.atr_period,
                    "atrMultiplierMilli": position.atr_multiplier_milli,
                    "peakPriceKrw": position.peak_price_krw,
                    "positionId": position.position_id,
                    "trailingStopKrw": position.trailing_stop_krw,
                }
                for position in store.positions
                if position.max_holding_sessions is not None
                and (
                    position.status != "CLOSED"
                    or (run.state == "COMPLETED" and position.symbol == run.selected_symbol)
                )
            ]
        ).decode()
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
            quantity=reservation.quantity if reservation else None,
            limit_price_krw=reservation.limit_price_krw if reservation else None,
            exact_intent_json=(intent.canonical_bytes.decode() if intent is not None else None),
            exact_intent_sha256=intent.sha256 if intent is not None else None,
            quote_snapshot_json=(
                canonical_json_bytes(
                    {
                        "fresh": quote.fresh,
                        "isEtfEtn": quote.is_etf_etn,
                        "lowerLimitKrw": quote.lower_limit_krw,
                        "priceKrw": quote.price_krw,
                        "symbol": quote.symbol,
                        "upperLimitKrw": quote.upper_limit_krw,
                    }
                ).decode()
                if quote is not None
                else None
            ),
            policy_id=policy.policy_id if policy else None,
            policy_version=policy.version if policy else None,
            position_expiry_session=expiry,
            filled_quantity=run.filled_quantity,
            leaves_quantity=run.leaves_quantity,
            unfilled_terminated_quantity=run.unfilled_terminated_quantity,
            average_fill_price_krw=run.average_fill_price_krw,
            exit_reason=run.exit_reason,
            expected_account_digest=_optional_text(state.get("expectedAccountDigest")),
            order_id=port.order_id,
            provider_order_ref_hash=port.provider_order_ref_hash,
            result_hash=result_hash,
            event_type=str(event["eventType"]),
            event_payload_hash=str(event["payloadHash"]),
            position_state_json=position_state_json,
        )
        previous_state = str(state.get("state", ""))
        records_ai_boundary = previous_state == "AI_JUDGING" or (
            previous_state == "NEWS_SCREENING" and run.state != "AI_JUDGING"
        )
        if records_ai_boundary:
            # A zero-evidence screen is also an auditable AI_NOT_PARTICIPATED
            # outcome.  The checkpoint and record commit together so a crash
            # cannot advance toward ORDER_SIZING without its explanation.
            self._repository.advance_with_ai_judgement(
                command,
                claim,
                _ai_judgement_record(command.expected_version + 1, run, port),
            )
        else:
            self._repository.advance(command)
        # durable하게 남은 뒤에만 알린다. 저널이 실패해도 tick은 계속된다.
        self._journal.notify(
            notice_from_event(
                event,
                run_id=claim.run_id,
                session_date=claim.session_date.isoformat(),
                state=run.state,
            )
        )
        # 체결이 확정되면 기대 계좌 투영을 함께 전진시킨다. 그러지 않으면 다음 tick이
        # 자기 체결을 외부 드리프트로 보고 ACCOUNT_DRIFT로 HALT하고, HALT는 stop으로 풀리지 않는다.
        if (
            run.state == "COMPLETED"
            and run.filled_quantity > 0
            and run.selected_side is not None
            and run.selected_symbol is not None
        ):
            lineage = port.account_lineage_advance(
                symbol=run.selected_symbol,
                side=run.selected_side,
                filled_quantity=run.filled_quantity,
                average_fill_price_krw=_required_price(run.average_fill_price_krw),
            )
            if lineage is not None:
                self._repository.advance_account_lineage(claim, lineage)
        return result


def _ai_judgement_record(
    checkpoint_version: int,
    run: AutomationRun,
    port: AutomationRuntimePort,
) -> AiJudgementRecord:
    verdicts = port.last_judgement_json
    v3 = run.policy_snapshot is not None and run.policy_snapshot.is_v3
    return AiJudgementRecord(
        checkpoint_version=checkpoint_version,
        participation=run.ai_participation,
        # Spring owns provider selection and the arm-time settings hash.  An
        # environment value in Python must not be allowed to rewrite history.
        provider_id="spring_bridge" if v3 and run.ai_judge_call_count else "",
        prompt_version="vertex-news-screen-v2" if v3 else PROMPT_CONTRACT_ID,
        baseline_symbol=run.ai_baseline_symbol,
        selected_symbol=run.selected_symbol,
        vetoed_symbol_count=len(run.ai_vetoed_symbols),
        judge_call_count=run.ai_judge_call_count,
        candidate_count=run.ai_candidate_count,
        verdicts_json=verdicts if isinstance(verdicts, str) and verdicts else "{}",
        ai_settings_sha256=run.ai_settings_sha256 or _LEGACY_AI_SETTINGS_SHA256,
        evidence_set_sha256=run.evidence_set_sha256,
        grounding_call_count=1 if run.screening_provider_call_count else 0,
        grounding_query_count=run.grounding_query_count,
        evidence_count=run.evidence_count,
    )


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
        daily_inference: DailyInferencePort | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._~:-]{32,256}", shared_secret):
            raise AutomationRuntimeError("AUTOMATION_RUNTIME_SECRET_INVALID")
        self._repository = repository
        self._port_factory = port_factory
        self._shared_secret = shared_secret.encode()
        self._planner = planner or XkrxBoundaryPlanner()
        self._runner = PersistentAutomationRunner(repository)
        self._daily_inference = daily_inference
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def serve(self) -> None:
        self._repository.preflight()
        daily_inference = self._daily_inference or DailyInferenceService.from_environment()
        try:
            while not self._stop.is_set():
                now = datetime.now(UTC).astimezone(_KST)
                wakeup = self._planner.next_wakeup(now)
                if wakeup > now and self._stop.wait((wakeup - now).total_seconds()):
                    return
                session_date = self._planner.current_or_next_session(wakeup)
                # 실패해도 기존 포지션 청산·대사 경로를 살리기 위해 claim은 계속한다. DB에
                # complete daily batch가 없으므로 신규 BUY 후보만 자연스럽게 0이 된다.
                try:
                    daily_inference.ensure_daily_signals(session_date)
                except DailyInferenceError:
                    pass
                claim_hash = _claim_hash(self._shared_secret, session_date)
                claim = self._repository.claim(session_date, claim_hash)
                if claim is None:
                    next_wakeup = datetime.combine(
                        self._planner.next_session(session_date), _OPEN_BOUNDARY, _KST
                    )
                    if self._stop.wait(
                        max(
                            0.0,
                            (next_wakeup - datetime.now(UTC).astimezone(_KST)).total_seconds(),
                        )
                    ):
                        return
                    continue
                self._drive_claim(claim)
        finally:
            daily_inference.close()

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
                if (
                    next_state in {"ORDER_SUBMITTED", "PENDING_RECONCILIATION"}
                    and wakeup.time() < _CANCEL_BOUNDARY
                ):
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
    policy = _policy_from_state(state)
    quote = _quote_from_state(state.get("quoteSnapshot"))
    screenings, candidate_quotes = _screenings_from_state(state)
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
        exit_reason=cast(Any, _optional_text(state.get("exitReason"))),
        selected_quote=quote,
        filled_quantity=int(state.get("filledQuantity", 0)),
        leaves_quantity=int(state.get("leavesQuantity", 0)),
        unfilled_terminated_quantity=int(state.get("unfilledTerminatedQuantity", 0)),
        average_fill_price_krw=(
            int(state["averageFillPriceKrw"])
            if state.get("averageFillPriceKrw") is not None
            else None
        ),
        provider_exec_ref_hash=_optional_text(state.get("providerExecRefHash")),
        policy_snapshot=policy,
        candidate_screenings=screenings,
        candidate_quotes=candidate_quotes,
        screening_provider_call_count=int(state.get("screeningProviderCallCount", 0)),
        grounding_query_count=int(state.get("groundingQueryCount", 0)),
        evidence_count=sum(len(item.evidence) for item in screenings),
        evidence_set_sha256=_optional_text(state.get("evidenceSetSha256")),
        candidate_set_sha256=_optional_text(state.get("candidateSetSha256")),
        ai_settings_sha256=_optional_text(state.get("aiSettingsSha256")),
    )
    reservation = state.get("reservation")
    if isinstance(reservation, dict):
        intent_value = reservation.get("exactIntent")
        intent = _intent_from_state(intent_value) if isinstance(intent_value, dict) else None
        run.reservation = OrderReservation(
            symbol=str(reservation["symbol"]),
            side=cast(Any, str(reservation["side"])),
            quantity=int(reservation["quantity"]),
            limit_price_krw=int(reservation["limitPriceKrw"]),
            intent=intent,
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
                expiry_session=(
                    date.fromisoformat(str(item["expirySession"]))
                    if item.get("expirySession")
                    else None
                ),
                created_at=_timestamp(item["createdAt"]),
                status=str(item["status"]),
                closed_at=_timestamp(item["closedAt"]) if item.get("closedAt") else None,
                quantity=int(item.get("quantity", 1)),
                entry_average_fill_price_krw=(
                    int(item["entryAverageFillPriceKrw"])
                    if item.get("entryAverageFillPriceKrw") is not None
                    else None
                ),
                entry_notional_krw=(
                    int(item["entryNotionalKrw"])
                    if item.get("entryNotionalKrw") is not None
                    else None
                ),
                policy_id=str(item.get("policyId", policy.policy_id)),
                policy_version=int(item.get("policyVersion", policy.version)),
                stop_loss_bps=int(item.get("stopLossBps", policy.stop_loss_bps)),
                take_profit_bps=int(item.get("takeProfitBps", policy.take_profit_bps)),
                exit_reason=cast(Any, _optional_text(item.get("exitReason"))),
                max_holding_sessions=(
                    int(item["maxHoldingSessions"])
                    if item.get("maxHoldingSessions") is not None
                    else None
                ),
                atr_period=(int(item["atrPeriod"]) if item.get("atrPeriod") is not None else None),
                atr_multiplier_milli=(
                    int(item["atrMultiplierMilli"])
                    if item.get("atrMultiplierMilli") is not None
                    else None
                ),
                model_sell_enabled=bool(item.get("modelSellEnabled", True)),
                peak_price_krw=(
                    int(item["peakPriceKrw"]) if item.get("peakPriceKrw") is not None else None
                ),
                atr_as_of_session=(
                    date.fromisoformat(str(item["atrAsOfSession"]))
                    if item.get("atrAsOfSession")
                    else None
                ),
                trailing_stop_krw=(
                    int(item["trailingStopKrw"])
                    if item.get("trailingStopKrw") is not None
                    else None
                ),
                atr_status=cast(Any, str(item.get("atrStatus", "LEGACY"))),
            )
        )
    return store, run


def inputs_from_state(
    state: dict[str, Any],
    *,
    risk_allow: bool,
    buyable_quantity: int,
    buyable_amount_krw: int = 9_223_372_036_854_775_807,
    account_complete: bool | None = None,
    account_digest_matches: bool | None = None,
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
        )
        for item in raw_signals
        if isinstance(item, dict)
    )
    manual = state.get("manualPositionSymbols")
    if not isinstance(manual, list) or not all(isinstance(item, str) for item in manual):
        raise AutomationRuntimeError("AUTOMATION_MANUAL_POSITIONS_INVALID")
    atr_histories = _atr_histories_from_state(state)
    session_date = date.fromisoformat(str(state["sessionDate"]))
    expected_sessions = _expected_atr_sessions(session_date, 101) if atr_histories else ()
    return AutomationInputs(
        session_date=session_date,
        release_active=bool(state["releaseActive"]),
        daily_shard_fresh_complete=bool(state["dailyShardFreshComplete"]),
        principle_active_current=bool(state["principleActiveCurrent"]),
        risk_allow=risk_allow,
        kill_switch_active=bool(state["killSwitchActive"]),
        account_complete=(
            bool(state["accountComplete"]) if account_complete is None else account_complete
        ),
        account_digest_matches=(
            bool(state["accountDigestMatches"])
            if account_digest_matches is None
            else account_digest_matches
        ),
        buyable_quantity=buyable_quantity,
        buyable_amount_krw=buyable_amount_krw,
        open_position_market_value_krw=int(state.get("openPositionMarketValueKrw", 0)),
        pending_buy_notional_krw=int(state.get("pendingBuyNotionalKrw", 0)),
        principle_max_single_order_krw=int(
            state.get("principleMaxSingleOrderKrw", 9_223_372_036_854_775_807)
        ),
        principle_asset_remaining_krw=int(
            state.get("principleAssetRemainingKrw", 9_223_372_036_854_775_807)
        ),
        policy=_policy_from_state(state),
        no_open_order=bool(state["noOpenOrder"]),
        unfinished_previous_order=bool(state["unfinishedPreviousOrder"]),
        news_veto_provider_bound=state.get("newsVetoProviderBound") is True,
        ai_judgement_provider_bound=state.get("aiJudgementProviderBound") is True,
        ai_judgement_enabled=state.get("aiJudgementEnabled") is True,
        ai_thinking_level=cast(Any, str(state.get("thinkingLevel", "low"))),
        ai_settings_sha256=_optional_text(state.get("aiSettingsSha256")),
        manual_position_symbols=frozenset(cast(list[str], manual)),
        signals=signals,
        atr_histories=atr_histories,
        atr_expected_sessions=expected_sessions,
    )


def _policy_from_state(state: dict[str, Any]) -> AutomationPolicySnapshot:
    value = state.get("policy")
    if not isinstance(value, dict):
        raise AutomationRuntimeError("AUTOMATION_POLICY_INVALID")
    try:
        return AutomationPolicySnapshot(
            policy_id=str(value["policyId"]),
            version=int(value["version"]),
            capital_limit_krw=int(value["capitalLimitKrw"]),
            stop_loss_bps=int(value["stopLossBps"]),
            take_profit_bps=int(value["takeProfitBps"]),
            preset=cast(Any, str(value["preset"])),
            max_open_positions=int(value.get("maxOpenPositions", 5)),
            max_holding_sessions=(
                int(value["maxHoldingSessions"])
                if value.get("maxHoldingSessions") is not None
                else None
            ),
            atr_period=(int(value["atrPeriod"]) if value.get("atrPeriod") is not None else None),
            atr_multiplier_milli=(
                int(value["atrMultiplierMilli"])
                if value.get("atrMultiplierMilli") is not None
                else None
            ),
            model_sell_enabled=bool(value.get("modelSellEnabled", True)),
        )
    except (KeyError, TypeError, ValueError, AutomationError) as error:
        raise AutomationRuntimeError("AUTOMATION_POLICY_INVALID") from error


def _atr_histories_from_state(
    state: dict[str, Any],
) -> dict[str, tuple[CompletedDailyBar, ...]]:
    raw = state.get("atrHistories")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AutomationRuntimeError("AUTOMATION_ATR_HISTORY_INVALID")
    histories: dict[str, tuple[CompletedDailyBar, ...]] = {}
    try:
        for symbol, rows in raw.items():
            if not isinstance(symbol, str) or not re.fullmatch(r"[0-9]{6}", symbol):
                raise AutomationRuntimeError("AUTOMATION_ATR_HISTORY_INVALID")
            if not isinstance(rows, list):
                raise AutomationRuntimeError("AUTOMATION_ATR_HISTORY_INVALID")
            histories[symbol] = tuple(
                CompletedDailyBar(
                    session_date=date.fromisoformat(str(item["sessionDate"])),
                    open_price_krw=int(item["openPriceKrw"]),
                    high_price_krw=int(item["highPriceKrw"]),
                    low_price_krw=int(item["lowPriceKrw"]),
                    close_price_krw=int(item["closePriceKrw"]),
                )
                for item in rows
                if isinstance(item, dict)
            )
            if len(histories[symbol]) != len(rows):
                raise AutomationRuntimeError("AUTOMATION_ATR_HISTORY_INVALID")
    except (KeyError, TypeError, ValueError, AtrHistoryError) as error:
        raise AutomationRuntimeError("AUTOMATION_ATR_HISTORY_INVALID") from error
    return histories


def _required_atr_periods(
    state: Mapping[str, Any],
    *,
    policy_period: int,
) -> dict[str, int]:
    """Use each open position's immutable ATR snapshot, not only today's policy."""

    if policy_period not in range(5, 101):
        raise AutomationRuntimeError("AUTOMATION_ATR_POLICY_INVALID")
    required: dict[str, int] = {}
    raw_signals = state.get("signals")
    if isinstance(raw_signals, list):
        for item in raw_signals:
            if isinstance(item, dict) and re.fullmatch(r"[0-9]{6}", str(item.get("symbol"))):
                required[str(item["symbol"])] = policy_period
    raw_positions = state.get("positions")
    if isinstance(raw_positions, list):
        for item in raw_positions:
            if (
                not isinstance(item, dict)
                or re.fullmatch(r"[0-9]{6}", str(item.get("symbol"))) is None
            ):
                continue
            position_period = int(item.get("atrPeriod", policy_period))
            if position_period not in range(5, 101):
                raise AutomationRuntimeError("AUTOMATION_ATR_POLICY_INVALID")
            symbol = str(item["symbol"])
            required[symbol] = max(required.get(symbol, 0), position_period)
    return required


def _screenings_from_state(
    state: dict[str, Any],
) -> tuple[tuple[CandidateScreening, ...], dict[str, Quote]]:
    raw = state.get("screenings")
    if raw is None:
        return (), {}
    if not isinstance(raw, list):
        raise AutomationRuntimeError("AUTOMATION_SCREENINGS_INVALID")
    screenings: list[CandidateScreening] = []
    quotes: dict[str, Quote] = {}
    try:
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("evidence"), list):
                raise AutomationRuntimeError("AUTOMATION_SCREENINGS_INVALID")
            symbol = str(item["symbol"])
            evidence = tuple(
                EvidenceSpan(
                    symbol=str(span["symbol"]),
                    citation_id=str(span["citationId"]),
                    source_id=str(span["sourceId"]),
                    source_type=cast(Any, str(span["sourceType"])),
                    source_event_date=(
                        date.fromisoformat(str(span["sourceEventDate"]))
                        if span.get("sourceEventDate")
                        else None
                    ),
                    age_warning=bool(span["ageWarning"]),
                    uri_sha256=str(span["uriSha256"]),
                    bounded_quote=str(span["boundedQuote"]),
                    quote_sha256=str(span["quoteSha256"]),
                    verified=span.get("verified") is True,
                )
                for span in item["evidence"]
                if isinstance(span, dict)
            )
            if len(evidence) != len(item["evidence"]):
                raise AutomationRuntimeError("AUTOMATION_SCREENINGS_INVALID")
            screenings.append(
                CandidateScreening(
                    symbol=symbol,
                    status=cast(Any, str(item["status"])),
                    verdict=cast(Any, str(item["verdict"])),
                    score_bps=int(item["scoreBps"]),
                    reason=str(item["reason"]),
                    evidence=evidence,
                )
            )
            quotes[symbol] = Quote(
                symbol=symbol,
                price_krw=int(item["priceKrw"]),
                lower_limit_krw=int(item["lowerLimitKrw"]),
                upper_limit_krw=int(item["upperLimitKrw"]),
                is_etf_etn=bool(item["isEtfEtn"]),
            )
    except (KeyError, TypeError, ValueError, AutomationError) as error:
        raise AutomationRuntimeError("AUTOMATION_SCREENINGS_INVALID") from error
    return tuple(screenings), quotes


def _expected_atr_sessions(as_of_session: date, limit: int) -> tuple[date, ...]:
    calendar = xcals.get_calendar("XKRX")
    stamp = pd.Timestamp(as_of_session)
    anchor = (
        calendar.previous_session(stamp)
        if calendar.is_session(stamp)
        else calendar.date_to_session(stamp, direction="previous")
    )
    return tuple(cast(date, item.date()) for item in calendar.sessions_window(anchor, -(limit - 1)))


def _intent_from_state(value: dict[str, Any]) -> ExactOrderIntent:
    try:
        return ExactOrderIntent(
            symbol=str(value["symbol"]),
            side=cast(Any, str(value["side"])),
            order_type=cast(Any, str(value["orderType"])),
            quantity=int(value["quantity"]),
            estimated_price=int(value["estimatedPrice"]),
            estimated_amount=int(value["estimatedAmount"]),
            timeframe=cast(Any, str(value["timeframe"])),
            strategy_id=str(value["strategyId"]),
        )
    except (KeyError, TypeError, ValueError, AutomationError) as error:
        raise AutomationRuntimeError("AUTOMATION_EXACT_INTENT_INVALID") from error


def _quote_from_state(value: object) -> Quote | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AutomationRuntimeError("AUTOMATION_QUOTE_SNAPSHOT_INVALID")
    try:
        return Quote(
            symbol=str(value["symbol"]),
            price_krw=int(value["priceKrw"]),
            lower_limit_krw=int(value["lowerLimitKrw"]),
            upper_limit_krw=int(value["upperLimitKrw"]),
            fresh=bool(value["fresh"]),
            is_etf_etn=bool(value["isEtfEtn"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise AutomationRuntimeError("AUTOMATION_QUOTE_SNAPSHOT_INVALID") from error


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


def _required_price(value: int | None) -> int:
    if value is None or value <= 0:
        raise AutomationRuntimeError("AUTOMATION_FILL_PRICE_MISSING")
    return value
