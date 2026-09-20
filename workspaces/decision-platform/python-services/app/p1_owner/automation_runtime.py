"""V90 PostgreSQL에 결속된 KIS_MOCK automation runtime과 XKRX boundary loop."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import signal
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

import pandas as pd

from app.data.calendar.adapters.xkrx import (
    build_xkrx_sessions_in_range,
    xkrx_calendar_bounds,
)
from app.data.calendar.xkrx_policy import corrected_calendar
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from app.data._shared.canonical_json import canonical_json_bytes
from app.p1_owner.automation_journal import AutomationJournal, notice_from_event
from app.strong_llm.prompt import PROMPT_CONTRACT_ID
from app.p1_owner.automation import (
    _DEFAULT_RISK_PER_TRADE_BPS,
    _MAX_OPEN_POSITIONS,
    _SESSION_STAGE_SYMBOL,
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
    StageOutcome,
)
from app.data.kis.accounting import KISCallBudgetExceeded
from app.p1_owner.automation_atr import AtrHistoryError, CompletedDailyBar
from app.p1_owner.daily_inference import DailyInferenceError, DailyInferenceService

_KST = ZoneInfo("Asia/Seoul")
_PREPARATION_BOUNDARY = time(8, 30)
_PREPARATION_DEADLINE = time(8, 50)
_OPEN_BOUNDARY = time(9, 30)
# 신규 진입을 판단하는 시점. 예전에는 09:40 하나였고 그것이 곧 그날의 마지막이라
# 개장 10분이 지나면 자본이 굳었다.
#
# 09:45 - 09:30~09:45 는 갭 정리와 장전 주문 소화로 양방향·스프레드 확대·체결 불안정
#         구간이다(장중 변동성 U자형에서 개장 직후가 3~4배). 그 뒤 첫 진입.
# 11:00 - 오전 중반 저변동 구간.
# 14:00 - 마지막 진입. 15:20 취소 경계와 충분히 이격한다.
#
# 시점 수와 시각은 상수로 둔다. 정책 컬럼으로 빼면 잘못 설정했을 때 거래가 0 이 되는
# 새 실패 지점이 생긴다. 시점당 주문 수는 묶지 않는다 - 상한 안에서 빈 슬롯을 채운다.
_DECISION_TIMES: tuple[time, ...] = (time(9, 45), time(11, 0), time(14, 0))
_LAST_DECISION_TIME = _DECISION_TIMES[-1]
_CANCEL_BOUNDARY = time(15, 20)

#: 매수 제출 마감. **DB 와 같은 값이어야 한다** -
#: `p1_begin_automation_portfolio_execution_v2`(V172) 가 이 시각 이후의 BUY 를 거부한다.
#:
#: 예전 `_SUBMIT_DEADLINE = 09:40` 은 Python 에서 참조 0 으로 죽었는데 SQL 에서는 살아
#: 있었다. 그래서 09:45/11:00/14:00 세 결정 시점이 **전부 DB 마감 이후**가 되어 포트폴리오
#: 경로의 매수가 구조적으로 불가능했다. 한 곳에서만 정의하고 SQL 정합 테스트가 고정한다.
#:
#: 14:30 인 이유: 마지막 결정 시점(14:00)보다 뒤여야 run 이 제출까지 갈 여유가 있고,
#: 취소 경계(15:20)보다는 충분히 앞이어야 한다 - 15:19 에 낸 매수는 60초 뒤 취소돼 지금과
#: 같은 미체결 사망을 반대쪽 끝에서 재현한다.
_BUY_SUBMIT_DEADLINE = time(14, 30)

#: 매도 제출 마감. 취소·대사 창이 열리는 시각과 같다.
_SELL_SUBMIT_DEADLINE = _CANCEL_BOUNDARY

# 잘못 고치면 거래가 0 이 되거나 취소 직전 주문이 나간다. import 시점에 막는다.
assert _LAST_DECISION_TIME < _BUY_SUBMIT_DEADLINE < _CANCEL_BOUNDARY, (
    "buy submit deadline must sit between the last decision time and the cancel boundary"
)
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_USER_ID = re.compile(r"^usr_[A-Za-z0-9_-]{8,96}$")
_RUN_ID = re.compile(r"^auto_run_[0-9a-f]{32}$")
_LEGACY_AI_SETTINGS_SHA256 = hashlib.sha256(
    b'{"aiJudgementEnabled":false,"thinkingLevel":"low"}'
).hexdigest()
# tick 이 일시 실패했을 때의 재시도. 창(09:30~15:20)을 다 쓰지 않으면서도 몇 분간의 브리지
# 장애는 넘길 수 있는 크기다. 이 한도를 넘으면 run 을 그 자리에 두고 물러난다.
_TICK_RETRY_SECONDS = 20.0
_MAX_TICK_FAILURES = 15
_WALL_CLOCK_CHECK_SECONDS = 30.0
# 다시 켰을 때 빠진 일별 배치를 한 tick 에 몇 세션까지 소급할지. 09:30 창을 먹지 않는 크기로
# 두고 나머지는 다음 tick 들이 이어 채운다. lookback 은 그 세션 수를 담을 달력 창이다
# (주말·휴일 때문에 세션 수보다 넉넉해야 한다).
_MAX_CATCH_UP_SESSIONS = 5
_CATCH_UP_LOOKBACK_DAYS = 21
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
# 단일 주문 엔진이 끝난 뒤 남은 세션 예산으로 포트폴리오 주문을 이어갈 수 있는 상태.
# SKIPPED_NO_ACTION 이 빠져 있으면 기존 엔진이 후보를 못 고른 날에는 새 planner 가
# 아예 호출되지 않아 탈락 근거조차 남지 않는다. 데이터 결손(SKIPPED_DATA_UNAVAILABLE),
# 명시 거부권(NEWS_VETOED), 정지(HALTED)는 이어가지 않는다.
#: 끊긴 스케줄 연쇄를 복원할 때 한 tick 에 굴릴 최대 칸 수. 거래일 기준 400 이면
#: 1 년 반이 넘는 정지에서도 따라잡는다. 무한 루프만 막는 안전핀이다.
_MAX_SCHEDULE_RECOVERY_HOPS = 400

#: 주문을 낸 뒤 마감 정산을 기다리는 상태. 15:20 경계가 이들에게만 뜻이 있다.
_SETTLEMENT_PENDING_STATES = frozenset({"ORDER_SUBMITTED", "PENDING_RECONCILIATION"})

_PORTFOLIO_CONTINUATION_STATES = frozenset(
    {
        "COMPLETED",
        "CANCELLED_UNFILLED",
        "SKIPPED_NO_ACTION",
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
    provider_exec_ref_hash: str | None = None


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
            return self._advance_account_lineage(cursor, claim, lineage)

    @staticmethod
    def _advance_account_lineage(
        cursor: Any, claim: RuntimeClaim, lineage: AccountLineageAdvance
    ) -> int:
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

    def advance_with_lineage(
        self, command: AdvanceCommand, claim: RuntimeClaim, lineage: AccountLineageAdvance
    ) -> tuple[int, bool]:
        with self._connect() as connection, connection.cursor() as cursor:
            result = self._advance(cursor, command)
            self._advance_account_lineage(cursor, claim, lineage)
            if lineage.provider_exec_ref_hash is not None:
                cursor.execute(
                    "select p1_sync_completed_automation_order_v1(%s,%s,%s)",
                    (claim.run_id, claim.claim_token_hash, lineage.provider_exec_ref_hash),
                )
            return result

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

    def settle_missed_schedules(self, user_id: str, today: date) -> int:
        """실행되지 않은 채 지나간 ARMED 스케줄을 마감한다.

        스케줄은 "직전 세션이 COMPLETED 여야 다음을 ARM" 하는 연쇄라, runtime 이 며칠
        멈추면 그 행이 영원히 COMPLETED 가 되지 않아 자동 운용이 돌아오지 않는다.
        run 이 실제로 있었던 세션은 건드리지 않는다.
        """

        _require_user_id(user_id)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select p1_settle_missed_automation_schedules_v1(%s,%s)", (user_id, today)
            )
            row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def _recovery_cursor(self, user_id: str) -> tuple[date | None, int | None]:
        """복구 기준점 두 값을 한 번에 읽는다.

        자동운용 runtime 역할에는 `automation_runtime_schedule`/`automation_control` 의
        직접 SELECT 권한이 **없다**(모든 읽기가 함수를 거치는 최소권한 설계). 예전에는
        이 두 값을 테이블에서 직접 읽어 프로덕션에서 InsufficientPrivilege 로 끊겼다 -
        놓친 세션을 마감한 직후, 한 칸도 굴리지 못하고. V171 의 함수로 읽는다.
        """

        _require_user_id(user_id)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("select * from p1_read_automation_recovery_cursor_v1(%s)", (user_id,))
            row = cursor.fetchone()
        if row is None:
            raise AutomationRuntimeError("AUTOMATION_RECOVERY_CURSOR_UNAVAILABLE")
        return row[0], (int(row[1]) if row[1] is not None else None)

    def last_completed_session(self, user_id: str) -> date | None:
        """연쇄를 다시 잇기 시작할 지점. 가장 최근 COMPLETED 스케줄이다."""

        return self._recovery_cursor(user_id)[0]

    def control_version(self, user_id: str) -> int:
        version = self._recovery_cursor(user_id)[1]
        if version is None:
            raise AutomationRuntimeError("AUTOMATION_CONTROL_UNAVAILABLE")
        return version

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

    def retry_at(self, user_id: str, session_date: date) -> datetime | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select p1_automation_data_gap_retry_at_v1(%s,%s)", (user_id, session_date)
            )
            row = cursor.fetchone()
        return row[0] if row is not None and isinstance(row[0], datetime) else None

    def resume_data_gap(self, user_id: str, session_date: date) -> int:
        readiness = self.readiness(user_id, session_date)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select p1_resume_automation_data_gap_v1(%s,%s)",
                (user_id, readiness.current_control_version),
            )
            row = cursor.fetchone()
        if row is None:
            raise AutomationRuntimeError("AUTOMATION_RESUME_UNAVAILABLE")
        return int(row[0])

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

    def record_stage_outcomes(self, claim: RuntimeClaim, outcomes: tuple[StageOutcome, ...]) -> int:
        """단계별 후보 결과를 남긴다. 진단 기록이라 상태 전이와 분리한다.

        실패해도 실행 결과를 되돌리지 않는다. 반대로 이게 없으면 무주문 실행의
        원인을 사람이 되짚을 수 없다.
        """

        if not outcomes:
            return 0
        _require_hash(claim.claim_token_hash)
        payload = canonical_json_bytes([item.projection() for item in outcomes]).decode()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.p1_record_automation_stage_outcomes_v1(%s,%s,%s::jsonb)",
                (claim.run_id, claim.claim_token_hash, payload),
            )
            row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

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
        lineage = None
        if (
            run.state == "COMPLETED"
            and run.filled_quantity > 0
            and run.selected_side
            and run.selected_symbol
        ):
            lineage = port.account_lineage_advance(
                symbol=run.selected_symbol,
                side=run.selected_side,
                filled_quantity=run.filled_quantity,
                average_fill_price_krw=_required_price(run.average_fill_price_krw),
            )
            if lineage is None:
                raise AutomationRuntimeError("AUTOMATION_ACCOUNT_LINEAGE_UNRESOLVED")
            run.provider_call_count = port.physical_calls
            result = run.projection()
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
        elif lineage is not None:
            self._repository.advance_with_lineage(command, claim, lineage)
        else:
            self._repository.advance(command)
        # 단계별 후보 결과는 진단 기록이라 전이가 durable 해진 뒤에 남긴다.
        # 실패해도 tick 을 되돌리지 않는다 - 대신 무엇이 실패했는지는 말한다.
        if run.stage_outcomes:
            recorder = getattr(self._repository, "record_stage_outcomes", None)
            if callable(recorder):
                try:
                    recorder(claim, run.stage_outcomes)
                except Exception as error:
                    print(
                        f"AUTOMATION_STAGE_OUTCOMES=FAILED error={type(error).__name__}",
                        flush=True,
                    )
        # durable하게 남은 뒤에만 알린다. 저널이 실패해도 tick은 계속된다.
        self._journal.notify(
            notice_from_event(
                event,
                run_id=claim.run_id,
                session_date=claim.session_date.isoformat(),
                state=run.state,
            )
        )
        if lineage is not None:
            publish = getattr(port, "publish_completed_observations", None)
            if callable(publish):
                publish()
        return result


def _ai_judgement_record(
    checkpoint_version: int,
    run: AutomationRun,
    port: AutomationRuntimePort,
) -> AiJudgementRecord:
    verdicts = port.last_judgement_json
    v3 = run.policy_snapshot is not None and run.policy_snapshot.is_v3
    if not verdicts and (run.candidate_screenings or run.ai_judge_call_count):
        # 기존 감사 필드에 선택적 실패를 남긴다. 미호출과 실패를 정상 0건으로 합치지 않는다.
        verdicts = canonical_json_bytes(
            {
                "judgeStatus": "ABSTAIN" if run.ai_judge_call_count else "NOT_CALLED",
                "screenings": [
                    {"symbol": item.symbol, "status": item.status, "reason": item.reason[:128]}
                    for item in run.candidate_screenings
                ],
            }
        ).decode()
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


#: 보정 적용 후 XKRX 개장일이 검증 시점과 같은지 보는 기준. 범위와 digest 는 함께 바뀐다.
_XKRX_REFERENCE_RANGE = (date(2015, 1, 1), date(2026, 12, 31))
_XKRX_REFERENCE_SESSION_COUNT = 2_944
_XKRX_REFERENCE_SESSION_SHA256 = "4e7a064d869ad1a35e27714d64a2e4e5d9eebde906dba15f6331cdb07e75512c"


def _verify_xkrx_sessions(calendar: Any) -> None:
    """달력이 검증 시점과 같은 개장일을 주는지 확인한다.

    버전 핀(`xkrx_policy._require_pinned_version`)은 이미 있지만 그것은 라이브러리
    식별자만 본다. 보정 규칙이나 휴장일 데이터가 바뀌면 같은 버전에서도 개장일이
    달라질 수 있고, 그때 잘못된 날에 claim 이 생긴다. 실제 세션 집합을 대조해 그 경우를
    닫는다. 버전 핀을 대체하지 않고 더한다.
    """

    start, end = _XKRX_REFERENCE_RANGE
    sessions = calendar.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
    payload = ",".join(item.date().isoformat() for item in sessions)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    if len(sessions) != _XKRX_REFERENCE_SESSION_COUNT or digest != _XKRX_REFERENCE_SESSION_SHA256:
        raise AutomationRuntimeError("XKRX_CALENDAR_SESSION_DRIFT")


class XkrxBoundaryPlanner:
    """반복 heartbeat 대신 현재 durable state에서 다음 한 boundary만 계산한다."""

    def __init__(self) -> None:
        if version("exchange-calendars") != "4.13.2":
            raise AutomationRuntimeError("XKRX_CALENDAR_VERSION_DRIFT")
        self._calendar = corrected_calendar()
        _verify_xkrx_sessions(self._calendar)

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

    def next_wakeup(
        self,
        now: datetime,
        state: str | None = None,
        session: date | None = None,
    ) -> datetime:
        """다음 경계 하나. `session` 은 계획의 기준이 되는 거래일이다.

        기준일을 받는 이유: 15:20 을 지나면 `current_or_next_session` 이 **다음** 거래일을
        돌려준다. 그것으로 오늘 실행을 계획하면 오늘 정산이 내일로 밀린다. 호출자가 아는
        claim 의 세션을 넘기면 그 실행의 시간표로 계산한다. 넘기지 않으면 종전과 같다.
        """

        local = _kst(now)
        if session is None:
            session = self.current_or_next_session(local)
        cancel = datetime.combine(session, _CANCEL_BOUNDARY, _KST)
        if state in _SETTLEMENT_PENDING_STATES:
            if state == "PENDING_RECONCILIATION" and local < cancel:
                return cancel
            if local >= cancel:
                # 마감 경계를 이미 지난 미정산 실행은 기다릴 것이 없다. 즉시 정산한다.
                # 정각 15:20 은 아래 경로가 이미 `local` 을 돌려주므로(경계가 `<=`),
                # 이 가지는 "늦게 도착한" 경우만 바꾼다.
                return local
        opening = datetime.combine(session, _OPEN_BOUNDARY, _KST)
        if local < opening:
            return opening
        if local.date() == session and local.timetz().replace(tzinfo=None) <= _CANCEL_BOUNDARY:
            return local
        next_session = self.next_session(session)
        return datetime.combine(next_session, _OPEN_BOUNDARY, _KST)

    def preparation_wakeup(self, now: datetime) -> datetime:
        """같은 XKRX 세션의 장전 계산 경계다. 주문 시작 시각은 변경하지 않는다."""
        local = _kst(now)
        session = self.current_or_next_session(local)
        return max(local, datetime.combine(session, _PREPARATION_BOUNDARY, _KST))


def _catch_up_sessions(target_session: date) -> list[date]:
    """target 직전까지 소급 생성할 XKRX 개장일을 오래된 순으로 돌려준다.

    target 자체는 넣지 않는다 - 호출자가 그 몫을 따로 만든다. 상한을 두는 이유는 오래
    쉬었을 때 한 번에 수십 세션을 만들며 09:30 창을 먹지 않게 하는 것이다. 나머지는 다음
    tick 들이 이어서 채운다. 달력은 오프라인 XKRX 이므로 DB 읽기가 없다.

    이미 만들어진 세션은 `ensure_daily_signals` 가 context 조회에서 `REPLAYED` 로 끊으므로
    여기서 무엇이 남았는지 미리 알 필요가 없다.
    """

    first, last = xkrx_calendar_bounds()
    start = max(target_session - timedelta(days=_CATCH_UP_LOOKBACK_DAYS), first)
    end = min(target_session - timedelta(days=1), last)
    if end < start:
        return []
    sessions = [
        built.session_date for built in build_xkrx_sessions_in_range(start, end) if built.is_open
    ]
    return sorted(sessions)[-_MAX_CATCH_UP_SESSIONS:]


class AutomationRuntimeService:
    """explicit enable 뒤에만 session claim을 처리하며 다음 XKRX boundary까지 block한다."""

    def __init__(
        self,
        repository: PostgresAutomationRuntimeRepository,
        port_factory: AutomationRuntimePortFactory,
        shared_secret: str,
        planner: XkrxBoundaryPlanner | None = None,
        daily_inference: DailyInferencePort | None = None,
        connectivity_check: Callable[[], bool] | None = None,
        performance_report_refresh: Callable[[], object] | None = None,
        portfolio_runner: Any | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._~:-]{32,256}", shared_secret):
            raise AutomationRuntimeError("AUTOMATION_RUNTIME_SECRET_INVALID")
        self._repository = repository
        self._port_factory = port_factory
        self._shared_secret = shared_secret.encode()
        self._planner = planner or XkrxBoundaryPlanner()
        self._runner = PersistentAutomationRunner(repository)
        self._daily_inference = daily_inference
        self._connectivity_check = connectivity_check
        self._performance_report_refresh = performance_report_refresh
        self._portfolio_runner = portfolio_runner
        self._owner_user_id = os.environ.get("P1_AUTOMATION_OWNER_USER_ID", "").strip()
        self._stop = threading.Event()

    def _wait_until(self, boundary: datetime) -> bool:
        # A long monotonic wait can miss the market after laptop suspend or a clock jump.
        while not self._stop.is_set():
            remaining = (boundary - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                return False
            if self._stop.wait(min(remaining, _WALL_CLOCK_CHECK_SECONDS)):
                return True
        return True

    def stop(self) -> None:
        self._stop.set()

    def serve(self) -> None:
        self._repository.preflight()
        daily_inference = self._daily_inference or DailyInferenceService.from_environment()
        report_refresh = (
            self._performance_report_refresh or _performance_report_refresh_from_environment()
        )
        # 잠들기 전에 한 번. 지난 기동이 마감을 끝내지 못하고 죽었으면 여기서 닫는다.
        # 닫지 않으면 ACTIVE claim 이 남아 다음 세션 claim 이 40001 로 거절된다.
        self._recover_stranded_session()
        try:
            while not self._stop.is_set():
                now = datetime.now(UTC).astimezone(_KST)
                wakeup = self._planner.preparation_wakeup(now)
                if self._wait_until(wakeup):
                    return
                now = datetime.now(UTC).astimezone(_KST)
                if self._planner.preparation_wakeup(now) > now:
                    continue
                if self._connectivity_check is not None and not self._connectivity_check():
                    print("AUTOMATION_CONNECTIVITY=UNAVAILABLE SESSION_NOT_CONSUMED", flush=True)
                    if self._stop.wait(60.0):
                        return
                    continue
                session_date = self._planner.current_or_next_session(now)
                # 오늘 예측이 과거 보충 뒤로 밀리지 않도록 현재 target을 먼저 처리한다.
                try:
                    prepared = daily_inference.ensure_daily_signals(session_date)
                    if getattr(prepared, "outcome", None) not in {
                        "REPLAYED",
                        "IMPORTED",
                    }:
                        raise DailyInferenceError("DAILY_PREPARATION_NOT_COMPLETE")
                except DailyInferenceError as error:
                    print(
                        f"AUTOMATION_DAILY_INFERENCE=UNAVAILABLE error={type(error).__name__}",
                        flush=True,
                    )
                    if now.time() < _OPEN_BOUNDARY:
                        if self._stop.wait(60.0):
                            return
                        continue
                else:
                    finished = datetime.now(UTC).astimezone(_KST)
                    timing = (
                        "ON_TIME"
                        if finished.date() == session_date
                        and finished.time() <= _PREPARATION_DEADLINE
                        else "LATE"
                    )
                    # REPLAYED는 기존 context의 판정이다. legacy producer 완결성까지
                    # 재검증했다는 READY 표식으로 승격하지 않는다.
                    print(
                        f"AUTOMATION_PREPARATION_ATTEMPT={timing} session={session_date} "
                        f"outcome={getattr(prepared, 'outcome', 'UNKNOWN')}",
                        flush=True,
                    )
                    if report_refresh is not None:
                        try:
                            report_result = report_refresh()
                            report_status = (
                                report_result.get("performanceReport", "UNKNOWN")
                                if isinstance(report_result, dict)
                                else "UNKNOWN"
                            )
                            print(f"AUTOMATION_PERFORMANCE_REPORT={report_status}", flush=True)
                        except (OSError, RuntimeError, ValueError) as error:
                            # 예측 준비와 주문 안전 경계는 보고서 갱신 실패로 되돌리지 않는다.
                            if _is_already_published(error):
                                # 같은 입력 generation 이 이미 발행돼 있다. 할 일이 없는 것을
                                # 실패로 적으면 진짜 장애와 구분되지 않는다.
                                #
                                # `continue` 로 넘기지 않는다 - 여기는 serve() 의 본 루프
                                # 안이고, 건너뛰면 그 바퀴의 claim 을 통째로 잃는다.
                                print(
                                    "AUTOMATION_PERFORMANCE_REPORT=ALREADY_CURRENT",
                                    flush=True,
                                )
                            else:
                                print(
                                    f"AUTOMATION_PERFORMANCE_REPORT=FAILED "
                                    f"error={type(error).__name__} "
                                    f"detail={_bounded_error_detail(error)}",
                                    flush=True,
                                )
                if now.time() < _OPEN_BOUNDARY:
                    if self._wait_until(datetime.combine(session_date, _OPEN_BOUNDARY, _KST)):
                        return
                    # suspend 이후 날짜와 연결성을 재검증한 뒤에만 claim한다.
                    continue
                # 한동안 쓰지 않다가 다시 켜면 그 사이 거래일의 일별 배치가 비어 있다.
                # `ensure_daily_signals` 는 세션별로 멱등(`REPLAYED`)이고 이미 만든 세션은
                # context 조회에서 바로 끊기므로, 빠진 구간을 오래된 순으로 상한 안에서
                # 채운다. 새 스케줄러도 새 컨테이너도 만들지 않는다.
                for missed in _catch_up_sessions(session_date):
                    try:
                        daily_inference.ensure_daily_signals(missed)
                    except DailyInferenceError as error:
                        # 소급은 최선 노력이다. 한 세션이 막혀도 나머지와 오늘 몫을 계속한다.
                        print(
                            "AUTOMATION_DAILY_CATCHUP=UNAVAILABLE "
                            f"session={missed.isoformat()} error={type(error).__name__}",
                            flush=True,
                        )
                # 끊긴 스케줄 연쇄를 잇는다. 스케줄은 "직전 세션이 COMPLETED 여야 다음을
                # ARM" 하는 구조라, runtime 이 며칠 멈추면 그날 ARMED 행이 실행되지 않은
                # 채 남고 그 행은 영원히 COMPLETED 가 되지 않는다. 그러면 오늘 세션의
                # ARMED 행도 없어 claim 이 아무 일도 하지 않고, 사람이 DB 를 만지기
                # 전까지 자동 운용이 돌아오지 않는다.
                self._settle_and_arm(session_date)
                claim_hash = _claim_hash(self._shared_secret, session_date)
                claim = self._repository.claim(session_date, claim_hash)
                if claim is None:
                    retry_at = (
                        self._repository.retry_at(self._owner_user_id, session_date)
                        if self._owner_user_id
                        else None
                    )
                    if retry_at is not None:
                        if self._wait_until(retry_at):
                            return
                        # Recheck TLS and the actual market date on the next loop before resuming.
                        now = datetime.now(UTC).astimezone(_KST)
                        if now.date() != session_date or now.time() >= _CANCEL_BOUNDARY:
                            continue
                        if self._connectivity_check is not None and not self._connectivity_check():
                            if self._stop.wait(60.0):
                                return
                            continue
                        try:
                            self._repository.resume_data_gap(self._owner_user_id, session_date)
                            print("AUTOMATION_FALLBACK=RESUMED", flush=True)
                        except (AutomationRuntimeError, psycopg.Error) as error:
                            print(
                                f"AUTOMATION_FALLBACK=BLOCKED error={type(error).__name__}",
                                flush=True,
                            )
                            if self._stop.wait(60.0):
                                return
                        continue
                    # 재시도 경계도 없는데 claim 이 비었다면 그 세션에 ARMED 행이
                    # 없다는 뜻이다. 연쇄에 빈칸이 생기면 마감할 행도 없어 아무도 메우지
                    # 않고 자동 운용이 조용히 멈춘다. 자러 가기 전에 한 번 이어 붙인다.
                    if self._advance_schedule_to(session_date):
                        # 빈칸을 메웠으면 오늘 것을 오늘 잡는다. 여기서 다음 세션까지
                        # 자면 메워 놓고도 그날 운용을 통째로 날린다.
                        continue
                    next_wakeup = datetime.combine(
                        self._planner.next_session(session_date), _PREPARATION_BOUNDARY, _KST
                    )
                    if self._wait_until(next_wakeup):
                        return
                    continue
                self._drive_claim(claim)
        finally:
            daily_inference.close()

    def _recover_stranded_session(self) -> None:
        """마감을 못 끝내고 죽은 직전 세션을 이어받아 정산한다.

        프로세스가 15:20 정산 전에 죽으면 run 은 미terminal, claim 은 ACTIVE 로 남는다.
        그 상태에서 새 프로세스는 `preparation_wakeup` 으로 곧장 다음 거래일까지 자버려
        아무도 그 실행을 닫지 않았다. 그리고 `p1_claim_automation_session_v1` 은 그
        사용자에게 ACTIVE claim 이 하나라도 있으면 다음 세션 claim 을 40001 로 거절한다.
        즉 한 번의 프로세스 죽음이 자동 운용을 영구히 멈춘다.

        스케줄 연쇄가 "직전 세션이 COMPLETED 여야 다음을 ARM" 이므로, 마지막 COMPLETED
        **다음** 거래일이 후보다 - 미완 세션은 많아야 하나다. 새 조회를 만들지 않고 이미
        있는 복구 커서를 쓴다.

        claim 재획득도 새 경로가 아니다. `_claim_hash` 가 결정적이라 같은 비밀이면
        기존 ACTIVE claim 이 그대로 replay 된다. 정산도 `_drive_claim` 을 그대로 지나가므로
        취소·체결반영·lineage·관측발행·orders 투영이 평소와 똑같은 순서로 일어난다.
        여기서 새로 사는 것은 없다 - `_reconcile_order` 는 주문을 내지 않는 함수다.
        """

        if not self._owner_user_id:
            return
        now = datetime.now(UTC).astimezone(_KST)
        try:
            last_completed = self._repository.last_completed_session(self._owner_user_id)
        except (AutomationRuntimeError, psycopg.Error) as error:
            print(
                f"AUTOMATION_RECOVERY=FAILED stage=cursor error={type(error).__name__}",
                flush=True,
            )
            return
        if last_completed is None:
            print("AUTOMATION_RECOVERY=NONE reason=NO_COMPLETED_SESSION", flush=True)
            return
        candidate = self._planner.next_session(last_completed)
        # 아직 정산할 때가 아닌 세션은 건드리지 않는다. 평소 흐름이 제 시각에 처리한다.
        if datetime.combine(candidate, _CANCEL_BOUNDARY, _KST) > now:
            print(f"AUTOMATION_RECOVERY=NONE session={candidate.isoformat()}", flush=True)
            return
        try:
            claim = self._repository.claim(candidate, _claim_hash(self._shared_secret, candidate))
        except (AutomationRuntimeError, psycopg.Error) as error:
            print(
                f"AUTOMATION_RECOVERY=FAILED stage=claim session={candidate.isoformat()} "
                f"error={type(error).__name__}",
                flush=True,
            )
            return
        if claim is None:
            # 그 세션이 claim 되지 않았거나 이미 풀렸다. 정산할 것이 없다.
            print(f"AUTOMATION_RECOVERY=NONE session={candidate.isoformat()}", flush=True)
            return
        print(
            f"AUTOMATION_RECOVERY=RESUMED session={candidate.isoformat()} run={claim.run_id}",
            flush=True,
        )
        try:
            self._drive_claim(claim)
        except Exception as error:
            # 복구가 기동을 막지 못하게 한다. 오늘 하나를 못 닫는 것보다 runtime 이 아예
            # 안 뜨는 쪽이 훨씬 나쁘다. 무엇이 일어났는지는 남긴다.
            print(
                f"AUTOMATION_RECOVERY=FAILED stage=drive session={candidate.isoformat()} "
                f"error={type(error).__name__} detail={_bounded_error_detail(error)}",
                flush=True,
            )
            return
        try:
            final_state = str(self._repository.read_state(claim)["state"])
        except (AutomationRuntimeError, psycopg.Error, KeyError) as error:
            print(
                f"AUTOMATION_RECOVERY=SETTLED session={candidate.isoformat()} "
                f"state=UNREADABLE error={type(error).__name__}",
                flush=True,
            )
            return
        # terminal 이 아니면 정산된 것이 아니다. 그대로 `SETTLED` 로 적으면 claim 이
        # 아직 ACTIVE 로 남아 다음 세션을 막고 있다는 사실이 로그에서 사라진다.
        outcome = "SETTLED" if final_state in _TERMINAL_STATES else "NOT_SETTLED"
        print(
            f"AUTOMATION_RECOVERY={outcome} session={candidate.isoformat()} state={final_state}",
            flush=True,
        )

    def _settle_and_arm(self, session_date: date) -> None:
        """놓친 세션을 마감하고 오늘 세션까지 스케줄을 전진시킨다.

        장애로 며칠 멈춰도 사람 개입 없이 거래일을 다시 따라가게 하는 것이 목적이다.
        run 이 실제로 있었던 세션은 마감하지 않는다 - 그 결과가 상태를 정한다.
        실패는 삼키지 않되 tick 을 막지도 않는다. 스케줄이 없으면 claim 이 그냥
        아무 일도 하지 않고 다음 경계에서 다시 시도한다.
        """

        if not self._owner_user_id:
            return
        try:
            settled = self._repository.settle_missed_schedules(self._owner_user_id, session_date)
        except (AutomationRuntimeError, psycopg.Error, AttributeError) as error:
            print(
                f"AUTOMATION_SCHEDULE_SETTLE=FAILED error={type(error).__name__}",
                flush=True,
            )
            return
        if not settled:
            return
        print(f"AUTOMATION_SCHEDULE_SETTLE=MISSED_SESSIONS count={settled}", flush=True)
        # 마감한 마지막 세션에서 오늘까지 한 칸씩 굴려 연쇄를 복원한다.
        #
        # `p1_roll_automation_schedule_v1` 은 직전 세션이 COMPLETED 일 것을 요구한다.
        # 그래서 한 칸 굴려 만든 **과거** 세션은 실행될 일이 없어 영원히 ARMED 로 남고,
        # 그 다음 칸을 굴리려 하면 gate 가 닫힌다. 며칠 밀린 연쇄가 하루에 한 칸씩만
        # 전진해 영영 오늘을 따라잡지 못한다. 한 칸 굴릴 때마다 다시 마감해 준다.
        self._advance_schedule_to(session_date)

    def _advance_schedule_to(self, session_date: date) -> bool:
        """마지막 COMPLETED 에서 목표 세션까지 스케줄을 한 칸씩 ARM 한다.

        한 칸이라도 굴렸으면 True. 호출자가 그걸 보고 claim 을 다시 시도한다.

        멱등하고 스스로 멈춘다 - 커서가 목표에 닿으면 반환하고, hop 상한이 무한 루프를
        막는다. 이미 ARM 된 칸을 다시 굴리면 DB gate 가 닫고 그 사실만 마커로 남는다.
        """

        if not self._owner_user_id:
            return False
        armed = False
        for _ in range(_MAX_SCHEDULE_RECOVERY_HOPS):
            cursor = self._repository.last_completed_session(self._owner_user_id)
            if cursor is None or cursor >= session_date:
                return armed
            following = self._planner.next_session(cursor)
            try:
                self._repository.roll_schedule(
                    self._owner_user_id,
                    cursor,
                    following,
                    self._repository.control_version(self._owner_user_id),
                )
            except (AutomationRuntimeError, psycopg.Error) as error:
                print(
                    f"AUTOMATION_SCHEDULE_ARM=STOPPED session={following.isoformat()} "
                    f"error={type(error).__name__}",
                    flush=True,
                )
                return armed
            armed = True
            print(f"AUTOMATION_SCHEDULE_ARM=RECOVERED session={following.isoformat()}", flush=True)
            if following >= session_date:
                return armed
            try:
                self._repository.settle_missed_schedules(self._owner_user_id, session_date)
            except (AutomationRuntimeError, psycopg.Error, AttributeError) as error:
                print(
                    f"AUTOMATION_SCHEDULE_SETTLE=FAILED error={type(error).__name__}",
                    flush=True,
                )
                return armed
        print("AUTOMATION_SCHEDULE_ARM=STOPPED reason=HOP_LIMIT", flush=True)
        return armed

    def _drive_continuations(
        self, *, claim: RuntimeClaim, state: dict[str, Any], port: Any
    ) -> bool:
        """결정 시점마다 남은 세션 예산으로 빈 슬롯을 채운다. 계속해도 되면 True.

        예전에는 단일 run 이 끝난 직후 한 번만 돌았고 신규 주문은 09:40 까지만 허용돼,
        개장 10분이 지나면 그날 자본이 그대로 굳었다. 시점을 늘려도 **시점당 주문 수는
        묶지 않는다** - 상한 안에서 살 것이 있으면 사고 없으면 아무것도 하지 않는다.
        그 판단은 이미 `plan_portfolio_orders` 가 한다.

        세션 총 주문 수는 DB(`max_orders_per_session`)와 ordinal 원장이 계속 쥔다.
        """

        runner = self._portfolio_runner
        if runner is None:
            return True
        for boundary in _DECISION_TIMES:
            target = datetime.combine(claim.session_date, boundary, _KST)
            if datetime.now(UTC).astimezone(_KST) < target and self._wait_until(target):
                return False
            while True:
                now_kst = datetime.now(UTC).astimezone(_KST)
                # 매수 창이 닫힌 뒤에도 이미 제출된 주문은 반드시 대사한다.
                # 매수 창의 끝은 "마지막 결정 시점"이 아니라 제출 마감이다. 예전에는
                # `<= _LAST_DECISION_TIME`(14:00) 이라, 14:00 시점은 `_wait_until` 이 깨어난
                # 뒤 실제 실행이 14:00:0x 라서 **항상** BUY_WINDOW_CLOSED 로 닫혔다.
                # 2026-09-15 14:00 에 실측으로 그렇게 끝났다 - 세 시점 중 마지막이
                # 구조적으로 매수를 못 하는 상태였다. DB 와 같은 마감을 본다.
                allow_new_orders = (
                    now_kst.date() == claim.session_date
                    and now_kst.timetz().replace(tzinfo=None) <= _BUY_SUBMIT_DEADLINE
                )
                try:
                    continuation = runner.continue_session(
                        claim=claim,
                        state=state,
                        port=port,
                        allow_new_orders=allow_new_orders,
                    )
                except Exception as error:
                    # 종류 이름만으로는 "마감을 넘겨 claim 읽기가 닫힌 것"과 "DB 장애"를
                    # 구분할 수 없다. 앞은 미체결 잔량의 장부 마감이 미뤄진 것이고 뒤는
                    # 진짜 장애다 - 다음 날 아침에 둘을 가릴 수 있어야 한다.
                    # sqlstate 42501 + claim unavailable 이면 앞이다.
                    print(
                        f"AUTOMATION_PORTFOLIO=FAILED error={type(error).__name__} "
                        f"sqlstate={getattr(error, 'sqlstate', None)} "
                        f"detail={_bounded_error_detail(error)}",
                        flush=True,
                    )
                    return False
                print(
                    f"AUTOMATION_PORTFOLIO={continuation.status} "
                    f"decision={boundary.isoformat(timespec='minutes')} "
                    f"planned={continuation.planned_orders} "
                    f"completed={continuation.completed_orders}",
                    flush=True,
                )
                # 로그에만 남기면 화면에는 또 "주문 없이 종료"만 보인다. 무주문으로 끝난
                # 결정 시점의 사유를 퍼널에 세션 단계로 남긴다 - 단일 주문 엔진 경로에는
                # 이미 붙어 있고 이쪽만 비어 있었다. 유일키가 (run,stage,symbol) 이라
                # 한 세션의 첫 무주문 시점이 남는다. 진단 기록이므로 실패해도 삼킨다.
                if continuation.planned_orders == 0 and continuation.completed_orders == 0:
                    self._record_portfolio_reason(claim, boundary, continuation.status)
                if continuation.status != "PENDING_RECONCILIATION":
                    break
                if self._stop.wait(_TICK_RETRY_SECONDS):
                    return False
            if continuation.status in {"SUBMIT_RESPONSE_UNRESOLVED", "EXECUTION_STATE_INVALID"}:
                return False
            # 예산이 다 찼으면 남은 시점을 기다릴 이유가 없다.
            if continuation.status == "ORDER_BUDGET_EXHAUSTED":
                break
        return True

    def _record_portfolio_reason(self, claim: RuntimeClaim, boundary: time, status: str) -> None:
        """무주문으로 끝난 결정 시점의 사유를 퍼널에 남긴다."""

        recorder = getattr(self._repository, "record_stage_outcomes", None)
        if not callable(recorder):
            return
        try:
            recorder(
                claim,
                (
                    StageOutcome(
                        "ORDER",
                        _SESSION_STAGE_SYMBOL,
                        "DROPPED",
                        status,
                        f"{boundary.isoformat(timespec='minutes')} 결정 시점에 주문이 없었다",
                    ),
                ),
            )
        except Exception as error:
            print(
                f"AUTOMATION_PORTFOLIO_REASON=FAILED error={type(error).__name__}",
                flush=True,
            )

    def _drive_claim(self, claim: RuntimeClaim) -> None:
        state = self._repository.read_state(claim)
        port = self._port_factory.build(claim, state)
        try:
            index = int(state["checkpointVersion"])
            failures = 0
            while not self._stop.is_set():
                state = self._repository.read_state(claim)
                current = str(state["state"])
                if current in _TERMINAL_STATES:
                    return
                now = datetime.now(UTC).astimezone(_KST)
                wakeup = self._planner.next_wakeup(now, current, claim.session_date)
                if self._wait_until(wakeup):
                    return
                wakeup = datetime.now(UTC).astimezone(_KST)
                index += 1
                try:
                    result = self._runner.run_tick(
                        claim=claim,
                        tick_id=f"{claim.run_id}:boundary:{index}",
                        now=wakeup,
                        port=port,
                    )
                except KISCallBudgetExceeded as error:
                    # 예산이 바닥난 상태에서 같은 호출을 반복하는 것은 절대 성공하지 못한다.
                    # 남은 재시도를 태우면 진짜 원인이 마지막 줄에 묻히고, 다음 기회에 쓸
                    # 예산까지 없어진다. 여기서 멈추고 무엇이 막았는지 그대로 남긴다.
                    print(
                        f"AUTOMATION_TICK_BUDGET_EXHAUSTED state={current} "
                        f"error={type(error).__name__} detail={_bounded_error_detail(error)}",
                        flush=True,
                    )
                    return
                except Exception as error:
                    # Retry the same idempotent tick without terminating the supervised process.
                    failures += 1
                    print(
                        f"AUTOMATION_TICK_FAILED state={current} "
                        f"error={type(error).__name__} attempt={failures}",
                        flush=True,
                    )
                    index -= 1
                    if failures > _MAX_TICK_FAILURES or self._stop.wait(_TICK_RETRY_SECONDS):
                        return
                    continue
                failures = 0
                next_state = str(result["state"])
                if next_state in _TERMINAL_STATES:
                    if (
                        self._portfolio_runner is not None
                        and next_state in _PORTFOLIO_CONTINUATION_STATES
                    ):
                        if not self._drive_continuations(claim=claim, state=state, port=port):
                            return
                    # HALTED 여도 다음 세션 스케줄은 만든다. 예전에는 만들지 않아서,
                    # control 이 잠긴 것과 스케줄이 없는 것이 겹쳐 사람이 두 곳을 다
                    # 고쳐야 다시 열렸다. control 이 잠겨 있으면 다음 run 이 사유와 함께
                    # 다시 정지할 뿐이고, 그것이 침묵보다 낫다.
                    try:
                        self._repository.roll_schedule(
                            claim.user_id,
                            claim.session_date,
                            self._planner.next_session(claim.session_date),
                            claim.control_version,
                        )
                    except (AutomationRuntimeError, psycopg.Error) as error:
                        # stop/disarm 또는 gate drift 는 정상 fail-close 다. 다만 DB 일시
                        # 장애와 구분되지 않으므로 삼키지 말고 무엇이 일어났는지는 말한다.
                        print(
                            f"AUTOMATION_SCHEDULE_ROLL=SKIPPED state={next_state} "
                            f"error={type(error).__name__}",
                            flush=True,
                        )
                    return
                if (
                    next_state in {"ORDER_SUBMITTED", "PENDING_RECONCILIATION"}
                    and wakeup.time() < _CANCEL_BOUNDARY
                ):
                    continue
        finally:
            port.close()


def _is_already_published(error: BaseException) -> bool:
    """이 실패가 "이미 발행돼 있음"인지 본다.

    `p1_publish_owner_performance_report_v1` 은 같은 입력 generation 이 이미 SUCCESS 면
    23505 를 올린다(V158:143). 파이썬 쪽은 그것을 `ScenarioMaterializationError` 로 감싸
    던지므로 원인 사슬을 따라가야 sqlstate 가 보인다.
    """

    seen: set[int] = set()
    cause: BaseException | None = error
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if getattr(cause, "sqlstate", None) == "23505":
            return True
        cause = cause.__cause__ or cause.__context__
    return False


def _bounded_error_detail(error: BaseException, limit: int = 200) -> str:
    """예외 메시지를 한 줄로, 길이를 묶어서 돌려준다.

    로그 한 줄이 통제 불능으로 길어지면 그 줄을 아무도 읽지 않는다.
    """

    text = " ".join(str(error).split())
    if len(text) <= limit:
        return text or "(no message)"
    return text[:limit] + "..."


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
    for item in raw_signals:
        if isinstance(item, dict) and "combinationMethod" in item:
            if item["combinationMethod"] != "EQUAL_WEIGHT_50_50" or not {
                "forecastClose",
                "lstmExpectedReturn",
                "ridgeExpectedReturn",
                "ridgeModelSha256",
            }.issubset(item):
                raise AutomationRuntimeError("AUTOMATION_RETURN_COMBINATION_INVALID")
    signals = tuple(
        SignalCandidate(
            symbol=str(item["symbol"]),
            lstm_signal=cast(Any, str(item["lstmSignal"])),
            baseline_signal=cast(Any, str(item["baselineSignal"])),
            expected_return=float(item["expectedReturn"]),
            forecast_close=float(item["forecastClose"]) if "forecastClose" in item else None,
            lstm_expected_return=float(item["lstmExpectedReturn"])
            if "lstmExpectedReturn" in item
            else None,
            ridge_expected_return=float(item["ridgeExpectedReturn"])
            if "ridgeExpectedReturn" in item
            else None,
            combination_method=str(item["combinationMethod"])
            if "combinationMethod" in item
            else None,
            ridge_model_sha256=str(item["ridgeModelSha256"])
            if "ridgeModelSha256" in item
            else None,
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
        # 지금까지 이 키를 읽는 곳이 없어서, 관측 적재가 실패해 RiskEngine 이 전부
        # HOLD 하는 날에도 사유가 로그와 화면 어디에도 남지 않았다.
        observation_publish=(
            str(state["observationPublish"])
            if isinstance(state.get("observationPublish"), str)
            else None
        ),
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
            # 상한과 거래당 위험은 V166 부터 원칙에 저장된다. 예전 state 에는 없으므로
            # 제품 기본값(10 / 1%)으로 읽는다. 5 를 기본으로 두면 6개가 열린 계정이
            # 영구 포화돼 매수가 나가지 않는다.
            max_open_positions=int(value.get("maxOpenPositions", _MAX_OPEN_POSITIONS)),
            risk_per_trade_bps=int(value.get("riskPerTradeBps", _DEFAULT_RISK_PER_TRADE_BPS)),
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
    calendar = corrected_calendar()
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


def _performance_report_refresh_from_environment() -> Callable[[], object] | None:
    bundle_root = os.environ.get("RETURN_INFERENCE_BUNDLE_ROOT", "").strip()
    dsn = os.environ.get("ASYNC_WORKER_DATABASE_DSN", "").strip()
    if not bundle_root or not dsn:
        return None

    def refresh() -> object:
        from app.p1_owner.scenario_materializer import materialize

        return materialize(Path(bundle_root), dsn)

    return refresh


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
    from app.p1_owner.automation_runtime_live import (
        LiveAutomationPortFactory,
        kis_mock_connectivity_ready,
    )
    from app.p1_owner.automation_portfolio_repository import (
        PostgresAutomationPortfolioRepository,
    )
    from app.p1_owner.automation_portfolio_runtime import PortfolioContinuationRunner

    service = AutomationRuntimeService(
        repository,
        LiveAutomationPortFactory(),
        shared_secret,
        connectivity_check=kis_mock_connectivity_ready,
        portfolio_runner=PortfolioContinuationRunner(
            PostgresAutomationPortfolioRepository(database_dsn)
        ),
    )
    signal.signal(signal.SIGTERM, lambda _signum, _frame: service.stop())
    signal.signal(signal.SIGINT, lambda _signum, _frame: service.stop())
    service.serve()
    return 0


if __name__ == "__main__":
    # Keep exception classes bound to the canonical module during `python -m` execution.
    from app.p1_owner.automation_runtime import main as _canonical_main

    raise SystemExit(_canonical_main())


def _required_price(value: int | None) -> int:
    if value is None or value <= 0:
        raise AutomationRuntimeError("AUTOMATION_FILL_PRICE_MISSING")
    return value
