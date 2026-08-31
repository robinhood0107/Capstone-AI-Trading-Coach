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
from typing import Any, Literal, Mapping, Protocol, cast

import exchange_calendars as xcals
import pandas as pd

from app.data._shared.canonical_json import canonical_json_bytes

Signal = Literal["BUY", "HOLD", "SELL"]
Side = Literal["BUY", "SELL"]
NewsVerdict = Literal["VETO_BUY", "NO_VETO", "ABSTAIN"]
SubmitOutcome = Literal["FILLED", "UNFILLED", "AMBIGUOUS"]
ReconcileOutcome = Literal["FILLED", "UNFILLED", "UNRESOLVED"]
PolicyPreset = Literal["CONSERVATIVE", "BALANCED", "AGGRESSIVE", "CUSTOM"]
ExitReason = Literal["STOP_LOSS", "MAX_HOLDING_SESSIONS", "MODEL_SELL", "TAKE_PROFIT"]

_ACTIVE_STATES = frozenset(
    {
        "SCHEDULED",
        "PRECHECK",
        "RECONCILING_PREVIOUS",
        "EXIT_SELECTED",
        "AI_JUDGING",
        "BUY_CANDIDATE_SELECTED",
        "NEWS_CHECKING",
        "ORDER_SIZING",
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
# 엔진이 낼 수 있는 전이 전체. DB whitelist(p1_automation_transition_valid_v2)와 한 글자라도
# 어긋나면 checkpoint가 CAS 충돌로 죽으므로 이 표를 단일 진실로 두고 양쪽을 대조한다.
# HALTED는 SESSION_DRIFT/ACCOUNT_DRIFT/KILL_SWITCH 가드가 모든 active 상태에서 낼 수 있다.
# 매수 후보가 있으면 선택은 AI_JUDGING으로 넘어간다. BUY_CANDIDATE_SELECTED로 직행하는 전이는
# 그대로 둔다 - 이 변경 전에 그 상태로 checkpoint된 run이 재개될 수 있어야 한다.
_SELECTION_TARGETS = frozenset(
    {
        "EXIT_SELECTED",
        "AI_JUDGING",
        "BUY_CANDIDATE_SELECTED",
        "SKIPPED_NO_ACTION",
        "SKIPPED_DATA_UNAVAILABLE",
    }
)
_LEGAL_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {(state, "HALTED") for state in _ACTIVE_STATES}
    | {
        ("SCHEDULED", "SCHEDULED"),
        ("SCHEDULED", "PRECHECK"),
        ("SCHEDULED", "SKIPPED_NO_ACTION"),
        ("SCHEDULED", "SKIPPED_LATE_START"),
        ("SCHEDULED", "SKIPPED_DATA_UNAVAILABLE"),
        ("PRECHECK", "RECONCILING_PREVIOUS"),
        ("RECONCILING_PREVIOUS", "PENDING_RECONCILIATION"),
        ("EXIT_SELECTED", "ORDER_SIZING"),
        ("AI_JUDGING", "BUY_CANDIDATE_SELECTED"),
        ("AI_JUDGING", "SKIPPED_NO_ACTION"),
        ("BUY_CANDIDATE_SELECTED", "NEWS_CHECKING"),
        ("NEWS_CHECKING", "NEWS_VETOED"),
        ("NEWS_CHECKING", "ORDER_SIZING"),
        ("NEWS_CHECKING", "SKIPPED_DATA_UNAVAILABLE"),
        ("ORDER_SIZING", "RISK_CHECKING"),
        ("ORDER_SIZING", "SKIPPED_NO_ACTION"),
        ("ORDER_SIZING", "SKIPPED_DATA_UNAVAILABLE"),
        ("ORDER_SIZING", "SKIPPED_LATE_START"),
        ("RISK_CHECKING", "ORDER_SUBMITTING"),
        ("RISK_CHECKING", "SKIPPED_NO_ACTION"),
        ("ORDER_SUBMITTING", "ORDER_SUBMITTING"),
        ("ORDER_SUBMITTING", "ORDER_SUBMITTED"),
        ("ORDER_SUBMITTING", "PENDING_RECONCILIATION"),
        ("ORDER_SUBMITTING", "SKIPPED_DATA_UNAVAILABLE"),
        ("ORDER_SUBMITTING", "SKIPPED_LATE_START"),
        ("ORDER_SUBMITTED", "PENDING_RECONCILIATION"),
        ("ORDER_SUBMITTED", "COMPLETED"),
        ("ORDER_SUBMITTED", "CANCELLED_UNFILLED"),
        ("PENDING_RECONCILIATION", "PENDING_RECONCILIATION"),
        ("PENDING_RECONCILIATION", "COMPLETED"),
        ("PENDING_RECONCILIATION", "CANCELLED_UNFILLED"),
    }
    # PRECHECK와 RECONCILING_PREVIOUS는 _select로 넘어가고, PENDING_RECONCILIATION은
    # 예약 없는 대사가 풀리면 같은 선택 경로로 되돌아간다.
    | {
        (state, target)
        for state in ("PRECHECK", "RECONCILING_PREVIOUS", "PENDING_RECONCILIATION")
        for target in _SELECTION_TARGETS
    }
)
_KST_OPEN_TIME = time(9, 30)
_KST_CLOSE_ORDER_TIME = time(9, 40)
_CANCEL_TIME = time(15, 20)
_MAX_OPEN_POSITIONS = 5
_MAX_PHYSICAL_CALLS = 16
_ROUND_TRIP_COST_BPS = 35
_MAX_BIGINT = 9_223_372_036_854_775_807
_POLICY_PRESETS: Mapping[str, tuple[int, int]] = {
    "CONSERVATIVE": (300, 500),
    "BALANCED": (500, 1_000),
    "AGGRESSIVE": (800, 1_500),
}


class AutomationError(RuntimeError):
    """Automation state or fixture transport violated a fail-closed invariant."""


@dataclass(frozen=True, slots=True)
class AutomationPolicySnapshot:
    """Versioned user budget and exit policy consumed by one automation run."""

    policy_id: str
    version: int
    capital_limit_krw: int
    stop_loss_bps: int
    take_profit_bps: int
    preset: PolicyPreset = "CUSTOM"
    max_open_positions: int = _MAX_OPEN_POSITIONS

    def __post_init__(self) -> None:
        policy_suffix = self.policy_id.removeprefix("auto_pol_")
        if (
            not self.policy_id.startswith("auto_pol_")
            or len(policy_suffix) != 32
            or any(character not in "0123456789abcdef" for character in policy_suffix)
            or not 1 <= self.version
        ):
            raise AutomationError("automation policy identity is invalid")
        if not 10_000 <= self.capital_limit_krw <= 10_000_000_000:
            raise AutomationError("automation capital limit is invalid")
        if self.capital_limit_krw % 10_000:
            raise AutomationError("automation capital limit increment is invalid")
        if not 100 <= self.stop_loss_bps <= 1_500:
            raise AutomationError("automation stop loss is invalid")
        if not 200 <= self.take_profit_bps <= 3_000:
            raise AutomationError("automation take profit is invalid")
        if self.take_profit_bps <= self.stop_loss_bps:
            raise AutomationError("automation exit thresholds are invalid")
        if self.max_open_positions != _MAX_OPEN_POSITIONS:
            raise AutomationError("automation position cap is invalid")
        expected = _POLICY_PRESETS.get(self.preset)
        if expected is not None and expected != (self.stop_loss_bps, self.take_profit_bps):
            raise AutomationError("automation preset thresholds drifted")

    @classmethod
    def from_preset(
        cls,
        *,
        policy_id: str,
        version: int,
        capital_limit_krw: int,
        preset: Literal["CONSERVATIVE", "BALANCED", "AGGRESSIVE"],
    ) -> AutomationPolicySnapshot:
        stop_loss, take_profit = _POLICY_PRESETS[preset]
        return cls(
            policy_id=policy_id,
            version=version,
            capital_limit_krw=capital_limit_krw,
            stop_loss_bps=stop_loss,
            take_profit_bps=take_profit,
            preset=preset,
        )


def _default_policy() -> AutomationPolicySnapshot:
    return AutomationPolicySnapshot.from_preset(
        policy_id="auto_pol_ffffffffffffffffffffffffffffffff",
        version=1,
        capital_limit_krw=10_000_000,
        preset="BALANCED",
    )


@dataclass(frozen=True, slots=True)
class ExactOrderIntent:
    """The one exact intent shared byte-for-byte by Decision and brokerage submit."""

    symbol: str
    side: Side
    order_type: Literal["LIMIT"]
    quantity: int
    estimated_price: int
    estimated_amount: int
    timeframe: Literal["1d"]
    strategy_id: str

    def __post_init__(self) -> None:
        if not (len(self.symbol) == 6 and self.symbol.isdigit()):
            raise AutomationError("automation intent symbol is invalid")
        if self.quantity <= 0 or self.estimated_price <= 0:
            raise AutomationError("automation intent amount is invalid")
        if self.quantity > _MAX_BIGINT // self.estimated_price:
            raise AutomationError("automation intent amount overflowed")
        if self.estimated_amount != self.quantity * self.estimated_price:
            raise AutomationError("automation intent amount drifted")
        if not self.strategy_id.startswith("strategy_"):
            raise AutomationError("automation strategy identity is invalid")

    def projection(self) -> dict[str, object]:
        return {
            "estimatedAmount": self.estimated_amount,
            "estimatedPrice": self.estimated_price,
            "orderType": self.order_type,
            "quantity": self.quantity,
            "side": self.side,
            "strategyId": self.strategy_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.projection())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class AccountLineageSnapshot:
    """Valuation-free account identity used to detect structural external drift."""

    account_id: str
    cash_krw: int
    positions: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if not self.account_id.startswith("acct_") or self.cash_krw < 0:
            raise AutomationError("automation account lineage is invalid")
        normalized = tuple(sorted(self.positions))
        if normalized != self.positions or len({item[0] for item in normalized}) != len(normalized):
            raise AutomationError("automation account lineage positions are invalid")
        if any(
            len(symbol) != 6 or not symbol.isdigit() or quantity <= 0
            for symbol, quantity in normalized
        ):
            raise AutomationError("automation account lineage position is invalid")

    @classmethod
    def from_projection(cls, value: Mapping[str, object]) -> AccountLineageSnapshot:
        raw_positions = value.get("positions")
        if not isinstance(raw_positions, list):
            raise AutomationError("automation account lineage projection is invalid")
        positions: list[tuple[str, int]] = []
        for item in raw_positions:
            if not isinstance(item, Mapping):
                raise AutomationError("automation account lineage projection is invalid")
            positions.append(
                (str(item.get("symbol", "")), _projection_integer(item.get("quantity")))
            )
        return cls(
            account_id=str(value.get("accountId", "")),
            cash_krw=_projection_integer(value.get("cashKrw")),
            positions=tuple(sorted(positions)),
        )

    def projection(self) -> dict[str, object]:
        return {
            "accountId": self.account_id,
            "cashKrw": self.cash_krw,
            "positions": [
                {"quantity": quantity, "symbol": symbol} for symbol, quantity in self.positions
            ],
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.projection())).hexdigest()

    def exact_match(self, observed: AccountLineageSnapshot) -> bool:
        return self == observed

    def permits_fill(
        self,
        observed: AccountLineageSnapshot,
        *,
        symbol: str,
        side: Side,
        filled_quantity: int,
        average_fill_price_krw: int,
    ) -> bool:
        if (
            self.account_id != observed.account_id
            or filled_quantity <= 0
            or average_fill_price_krw <= 0
        ):
            return False
        before = dict(self.positions)
        after = dict(observed.positions)
        expected_quantity = before.get(symbol, 0) + (
            filled_quantity if side == "BUY" else -filled_quantity
        )
        if expected_quantity < 0:
            return False
        expected_positions = dict(before)
        if expected_quantity:
            expected_positions[symbol] = expected_quantity
        else:
            expected_positions.pop(symbol, None)
        if after != expected_positions:
            return False
        notional = filled_quantity * average_fill_price_krw
        cost_bound = (notional * _ROUND_TRIP_COST_BPS + 9_999) // 10_000
        cash_delta = observed.cash_krw - self.cash_krw
        if side == "BUY":
            return -(notional + cost_bound) <= cash_delta <= -notional
        return notional - cost_bound <= cash_delta <= notional


@dataclass(frozen=True, slots=True)
class ReconcileSnapshot:
    """Sanitized cumulative execution state for one exact reservation."""

    resolved: bool
    cumulative_quantity: int
    leaves_quantity: int
    average_fill_price_krw: int | None
    cancelled: bool = False
    rejected: bool = False
    provider_exec_ref_hash: str | None = None

    def __post_init__(self) -> None:
        if self.cumulative_quantity < 0 or self.leaves_quantity < 0:
            raise AutomationError("automation execution quantity is invalid")
        if (self.cumulative_quantity > 0) != (self.average_fill_price_krw is not None):
            raise AutomationError("automation execution average price is invalid")
        if self.average_fill_price_krw is not None and self.average_fill_price_krw <= 0:
            raise AutomationError("automation execution average price is invalid")


@dataclass(frozen=True, slots=True)
class SignalCandidate:
    symbol: str
    lstm_signal: Signal
    baseline_signal: Signal
    expected_return: float
    confidence: float


@dataclass(frozen=True, slots=True)
class AiCandidateVerdict:
    """후보 하나에 대한 모델의 판단. 수량도 주문도 여기에 없다."""

    symbol: str
    score: float
    veto: bool
    reason: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0 or not math.isfinite(self.score):
            raise AutomationError("automation ai score is invalid")
        if not self.reason:
            raise AutomationError("automation ai reason is empty")


@dataclass(frozen=True, slots=True)
class AiJudgement:
    """한 번의 판단 전체. 이 값으로 순위·차단·수량이 결정론적으로 계산된다."""

    verdicts: tuple[AiCandidateVerdict, ...]
    confidence: float
    summary: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0 or not math.isfinite(self.confidence):
            raise AutomationError("automation ai confidence is invalid")
        symbols = [item.symbol for item in self.verdicts]
        if len(symbols) != len(set(symbols)):
            raise AutomationError("automation ai verdict symbols are duplicated")


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
    buyable_amount_krw: int = _MAX_BIGINT
    open_position_market_value_krw: int = 0
    pending_buy_notional_krw: int = 0
    principle_max_single_order_krw: int = _MAX_BIGINT
    principle_asset_remaining_krw: int = _MAX_BIGINT
    policy: AutomationPolicySnapshot = field(default_factory=_default_policy)
    no_open_order: bool = True
    unfinished_previous_order: bool = False
    # 뉴스 거부권 provider가 붙어 있는지. 붙어 있으면 ABSTAIN도 매수를 막고, 없으면 통과시킨다.
    # 부정 이벤트 차단은 원칙의 disclosure_risk_guard가 RiskEngine에서 결정론적으로 수행한다.
    news_veto_provider_bound: bool = False
    # Strong LLM이 붙어 있는지. 붙어 있지 않거나 답하지 못하면 판단에 AI_NOT_PARTICIPATED를
    # 남기고 기존 규칙만으로 진행한다. AI가 없다고 자동매매가 멈추지는 않는다.
    ai_judgement_provider_bound: bool = False
    # 이 run에서 이미 받은 판단의 확신도. AI_JUDGING과 ORDER_SIZING은 서로 다른 tick이라
    # run 객체로는 건너오지 못한다. 저장된 판단을 상태가 다시 실어 온다.
    ai_confidence: float | None = None
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
    quantity: int = 1
    entry_average_fill_price_krw: int | None = None
    entry_notional_krw: int | None = None
    policy_id: str = "auto_pol_ffffffffffffffffffffffffffffffff"
    policy_version: int = 1
    stop_loss_bps: int = 500
    take_profit_bps: int = 1_000
    exit_reason: ExitReason | None = None
    exit_average_fill_price_krw: int | None = None
    exit_filled_quantity: int = 0
    realized_pnl_krw: int | None = None

    def __post_init__(self) -> None:
        if self.quantity < 0 or (self.status != "CLOSED" and self.quantity == 0):
            raise AutomationError("automation position quantity is invalid")
        if self.entry_average_fill_price_krw is not None:
            if self.entry_average_fill_price_krw <= 0:
                raise AutomationError("automation position entry price is invalid")
            exact_notional = self.quantity * self.entry_average_fill_price_krw
            if self.entry_notional_krw not in {None, exact_notional}:
                raise AutomationError("automation position entry notional drifted")
            if self.entry_notional_krw is None:
                self.entry_notional_krw = exact_notional

    def projection(self) -> dict[str, object]:
        return {
            "accountId": self.account_id,
            "botOwned": True,
            "closedAt": _iso(self.closed_at) if self.closed_at is not None else None,
            "contractId": "automation-position.v2",
            "createdAt": _iso(self.created_at),
            "entrySession": self.entry_session.isoformat(),
            "entryAverageFillPriceKrw": self.entry_average_fill_price_krw,
            "exitAverageFillPriceKrw": self.exit_average_fill_price_krw,
            "exitReason": self.exit_reason,
            "realizedPnlKrw": self.realized_pnl_krw,
            "expirySession": self.expiry_session.isoformat(),
            "policyId": self.policy_id,
            "policyVersion": self.policy_version,
            "positionId": self.position_id,
            "quantity": self.quantity,
            "shortAllowed": False,
            "status": self.status,
            "stopLossBps": self.stop_loss_bps,
            "symbol": self.symbol,
            "takeProfitBps": self.take_profit_bps,
        }


@dataclass(slots=True)
class OrderReservation:
    symbol: str
    side: Side
    quantity: int
    limit_price_krw: int
    quote_observed: bool = True
    intent: ExactOrderIntent | None = None

    def __post_init__(self) -> None:
        if self.quantity <= 0 or self.limit_price_krw <= 0:
            raise AutomationError("automation reservation is invalid")
        if self.intent is not None and (
            self.intent.symbol != self.symbol
            or self.intent.side != self.side
            or self.intent.quantity != self.quantity
            or self.intent.estimated_price != self.limit_price_krw
        ):
            raise AutomationError("automation reservation intent drifted")


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
    exit_reason: ExitReason | None = None
    selected_quote: Quote | None = None
    filled_quantity: int = 0
    leaves_quantity: int = 0
    average_fill_price_krw: int | None = None
    provider_exec_ref_hash: str | None = None
    policy_snapshot: AutomationPolicySnapshot | None = None
    unfilled_terminated_quantity: int = 0
    # AI 판단의 흔적. 무엇이 바뀌었는지 사후에 말할 수 없으면 이 승격은 검증 불가능해진다.
    ai_participation: str = "NOT_PARTICIPATED"
    ai_confidence: float | None = None
    ai_judge_call_count: int = 0
    ai_baseline_symbol: str | None = None
    ai_candidate_count: int = 0
    ai_vetoed_symbols: tuple[str, ...] = ()
    ai_quantity_before: int | None = None
    ai_quantity_after: int | None = None

    def projection(self) -> dict[str, object]:
        reservation = self.reservation
        policy = self.policy_snapshot
        return {
            "brokerageMode": self.brokerage_mode,
            "contractId": "automation-run.v2",
            "estimatedAmountKrw": (
                reservation.intent.estimated_amount
                if reservation is not None and reservation.intent is not None
                else None
            ),
            "exitReason": self.exit_reason,
            "filledQuantity": self.filled_quantity if reservation is not None else None,
            "leavesQuantity": self.leaves_quantity if reservation is not None else None,
            "limitPriceKrw": reservation.limit_price_krw if reservation is not None else None,
            "orderQuantity": reservation.quantity if reservation is not None else None,
            "physicalSubmitCount": self.physical_submit_count,
            "policyId": policy.policy_id if policy is not None else None,
            "policyVersion": policy.version if policy is not None else None,
            "providerCalls": self.provider_call_count,
            "runId": self.run_id,
            "selectedSide": self.selected_side,
            "selectedSymbol": self.selected_symbol,
            "sessionDate": self.session_date.isoformat(),
            "startedAt": _iso(self.started_at),
            "state": self.state,
            "updatedAt": _iso(self.updated_at),
        }


class AutomationFixtureTransportPort(Protocol):
    physical_calls: int
    physical_submit_calls: int
    quote_calls: int
    vertex_calls: int
    judge_calls: int
    submit_calls: int
    reconcile_calls: int
    cancel_calls: int

    def quote(self, symbol: str) -> Quote: ...

    def vertex(self, symbol: str) -> NewsVerdict: ...

    def judge(self, candidates: tuple[SignalCandidate, ...]) -> AiJudgement | None:
        """Strong LLM 판단. None은 물어볼 곳이 없었거나 답을 못 받았다는 뜻이다."""

    def submit(self, reservation: OrderReservation) -> SubmitOutcome: ...

    def reconcile(
        self, reservation: OrderReservation | None
    ) -> ReconcileOutcome | ReconcileSnapshot: ...

    def cancel(self, reservation: OrderReservation) -> bool: ...


@dataclass(slots=True)
class FixtureAutomationTransport:
    """Configurable logical outcomes with physical call count fixed at zero."""

    quotes: dict[str, Quote]
    news_verdict: NewsVerdict = "NO_VETO"
    ai_judgement: AiJudgement | None = None
    submit_outcome: SubmitOutcome = "FILLED"
    reconcile_outcomes: list[ReconcileOutcome] = field(default_factory=lambda: ["FILLED"])
    reconcile_snapshots: list[ReconcileSnapshot] = field(default_factory=list)
    cancel_succeeds: bool = True
    physical_calls: int = 0
    physical_submit_calls: int = 0
    quote_calls: int = 0
    vertex_calls: int = 0
    judge_calls: int = 0
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

    def judge(self, candidates: tuple[SignalCandidate, ...]) -> AiJudgement | None:
        del candidates
        self.judge_calls += 1
        return self.ai_judgement

    def submit(self, reservation: OrderReservation) -> SubmitOutcome:
        del reservation
        self.submit_calls += 1
        return self.submit_outcome

    def reconcile(
        self, reservation: OrderReservation | None
    ) -> ReconcileOutcome | ReconcileSnapshot:
        self.reconcile_calls += 1
        if self.reconcile_snapshots:
            return self.reconcile_snapshots.pop(0)
        if reservation is not None and self.reconcile_outcomes:
            outcome = self.reconcile_outcomes.pop(0)
            if outcome == "FILLED":
                return ReconcileSnapshot(
                    resolved=True,
                    cumulative_quantity=reservation.quantity,
                    leaves_quantity=0,
                    average_fill_price_krw=reservation.limit_price_krw,
                )
            if outcome == "UNFILLED":
                return ReconcileSnapshot(
                    resolved=True,
                    cumulative_quantity=0,
                    leaves_quantity=reservation.quantity,
                    average_fill_price_krw=None,
                )
            return ReconcileSnapshot(False, 0, reservation.quantity, None)
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
        if run.policy_snapshot is None:
            run.policy_snapshot = inputs.policy
        elif (
            run.policy_snapshot.policy_id != inputs.policy.policy_id
            or run.policy_snapshot.version != inputs.policy.version
        ):
            self._halt(run, now, "POLICY_VERSION_DRIFT")
        self._advance(run, now, inputs, transport)
        if transport.physical_calls < run.provider_call_count:
            raise AutomationError("transport provider count moved backwards")
        if transport.physical_submit_calls < run.physical_submit_count:
            raise AutomationError("transport submit count moved backwards")
        run.provider_call_count = transport.physical_calls
        run.physical_submit_count = transport.physical_submit_calls
        if run.provider_call_count > _MAX_PHYSICAL_CALLS or run.physical_submit_count > 1:
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
                self._select(run, now, inputs, transport)
        elif run.state == "RECONCILING_PREVIOUS":
            if not self._ensure_capacity(run, now, transport):
                return
            outcome = transport.reconcile(None)
            if outcome == "UNRESOLVED":
                self._transition(run, "PENDING_RECONCILIATION", "ACCOUNT_RECONCILED", now)
            else:
                self._select(run, now, inputs, transport)
        elif run.state == "EXIT_SELECTED":
            self._transition(run, "ORDER_SIZING", "RUN_TRANSITIONED", now)
        elif run.state == "AI_JUDGING":
            self._judge(run, now, inputs, transport)
        elif run.state == "BUY_CANDIDATE_SELECTED":
            self._transition(run, "NEWS_CHECKING", "RUN_TRANSITIONED", now)
        elif run.state == "NEWS_CHECKING":
            if run.selected_quote is None:
                quote = self._quote(run, now, transport, _required(run.selected_symbol))
                if quote is None:
                    return
                run.selected_quote = quote
            if not self._ensure_capacity(run, now, transport):
                return
            verdict = transport.vertex(_required(run.selected_symbol))
            run.vertex_call_count += 1
            # provider가 붙어 있으면 판단 불가(ABSTAIN)도 차단으로 본다. 붙어 있지 않으면 ABSTAIN은
            # "물어볼 곳이 없었다"는 뜻이므로 이것만으로 매수를 막지 않는다. VETO_BUY는 언제나 차단이다.
            vetoed = verdict == "VETO_BUY" or (
                verdict == "ABSTAIN" and inputs.news_veto_provider_bound
            )
            if vetoed:
                self._transition(run, "NEWS_VETOED", "NEWS_RESULT_RECORDED", now)
            else:
                self._transition(run, "ORDER_SIZING", "NEWS_RESULT_RECORDED", now)
        elif run.state == "ORDER_SIZING":
            self._size_order(run, now, inputs, transport)
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
        if local_time < _KST_OPEN_TIME:
            self._transition(run, "SCHEDULED", "RUN_TRANSITIONED", now)
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

    def _select(
        self,
        run: AutomationRun,
        now: datetime,
        inputs: AutomationInputs,
        transport: AutomationFixtureTransportPort,
    ) -> None:
        positions = [position for position in self.store.positions if position.status == "OPEN"]
        if len(positions) > inputs.policy.max_open_positions:
            self._halt(run, now, "POSITION_CAP_DRIFT")
            return
        signals = {candidate.symbol: candidate for candidate in inputs.signals}
        quotes: dict[str, Quote] = {}
        returns: dict[str, int] = {}
        for position in positions:
            if position.entry_average_fill_price_krw is None:
                continue
            quote = self._quote(run, now, transport, position.symbol)
            if quote is None:
                return
            quotes[position.symbol] = quote
            returns[position.symbol] = _estimated_net_return_bps(
                position.entry_average_fill_price_krw,
                _limit_price(quote, "SELL"),
            )

        stop_exits = sorted(
            (
                position
                for position in positions
                if position.symbol in returns
                and returns[position.symbol] <= -position.stop_loss_bps
            ),
            key=lambda position: (
                returns[position.symbol],
                position.entry_session,
                position.symbol,
            ),
        )
        expiry_exits = sorted(
            (position for position in positions if run.session_date >= position.expiry_session),
            key=lambda position: (
                -_session_distance(position.expiry_session, run.session_date),
                position.entry_session,
                position.symbol,
            ),
        )
        model_exits = sorted(
            (
                position
                for position in positions
                if (signal := signals.get(position.symbol)) is not None
                and signal.lstm_signal == signal.baseline_signal == "SELL"
            ),
            key=lambda position: (position.entry_session, position.symbol),
        )
        profit_exits = sorted(
            (
                position
                for position in positions
                if position.symbol in returns
                and returns[position.symbol] >= position.take_profit_bps
            ),
            key=lambda position: (
                -returns[position.symbol],
                position.entry_session,
                position.symbol,
            ),
        )
        selected: BotPosition | None = None
        reason: ExitReason | None = None
        for candidates, candidate_reason in (
            (stop_exits, "STOP_LOSS"),
            (expiry_exits, "MAX_HOLDING_SESSIONS"),
            (model_exits, "MODEL_SELL"),
            (profit_exits, "TAKE_PROFIT"),
        ):
            if candidates:
                selected = candidates[0]
                reason = cast(ExitReason, candidate_reason)
                break
        if selected is not None:
            run.selected_symbol = selected.symbol
            run.selected_side = "SELL"
            run.exit_reason = reason
            selected.status = "EXIT_PENDING"
            run.selected_quote = quotes.get(selected.symbol)
            self._transition(run, "EXIT_SELECTED", "EXIT_SELECTED", now)
            return
        if len(positions) >= inputs.policy.max_open_positions:
            self._transition(run, "SKIPPED_NO_ACTION", "RUN_TRANSITIONED", now)
            return
        buys = self._buy_candidates(inputs)
        if not buys:
            self._transition(run, "SKIPPED_NO_ACTION", "RUN_TRANSITIONED", now)
            return
        # 후보 집합의 소유자는 Return Engine이다. AI는 이 목록 안에서만 답하고 종목을 더하지 못한다.
        self._transition(run, "AI_JUDGING", "RUN_TRANSITIONED", now)

    def _buy_candidates(self, inputs: AutomationInputs) -> tuple[SignalCandidate, ...]:
        """2-of-2 합의를 통과한 매수 후보. 같은 입력에 같은 순서가 나온다.

        AI_JUDGING에서 이것을 다시 계산한다. checkpoint에 목록을 실어 나르면 저장 계약이
        늘어나고, 그 목록과 지금 입력이 어긋났을 때 어느 쪽이 옳은지 말할 수 없게 된다.
        """

        held = {
            position.symbol for position in self.store.positions if position.status == "OPEN"
        } | set(inputs.manual_position_symbols)
        return tuple(
            sorted(
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
        )

    def _judge(
        self,
        run: AutomationRun,
        now: datetime,
        inputs: AutomationInputs,
        transport: AutomationFixtureTransportPort,
    ) -> None:
        buys = self._buy_candidates(inputs)
        if not buys:
            # 판단을 기다리는 사이 입력이 바뀌어 후보가 사라졌다. 옛 후보를 사지 않는다.
            self._transition(run, "SKIPPED_NO_ACTION", "RUN_TRANSITIONED", now)
            return
        # 규칙만으로 고른 1등을 먼저 적는다. 이것과 실제 선택이 다르면 재순위가 일어난 run이다.
        run.ai_baseline_symbol = buys[0].symbol
        run.ai_candidate_count = len(buys)
        judgement = self._ai_judgement(run, inputs, transport)
        ranked = _apply_judgement(buys, judgement)
        run.ai_vetoed_symbols = tuple(
            candidate.symbol for candidate in buys if candidate not in ranked
        )
        if not ranked:
            # 모든 후보가 차단됐다. 차단은 매수를 막을 뿐 다른 종목을 만들지 않는다.
            self._transition(run, "SKIPPED_NO_ACTION", "RUN_TRANSITIONED", now)
            return
        run.selected_symbol = ranked[0].symbol
        run.selected_side = "BUY"
        self._transition(run, "BUY_CANDIDATE_SELECTED", "BUY_SELECTED", now)

    def _ai_judgement(
        self,
        run: AutomationRun,
        inputs: AutomationInputs,
        transport: AutomationFixtureTransportPort,
    ) -> AiJudgement | None:
        if not inputs.ai_judgement_provider_bound:
            return None
        judgement = transport.judge(self._buy_candidates(inputs))
        run.ai_judge_call_count += 1
        if judgement is None:
            # 1차도 2차도 답하지 못했다. 기존 규칙만으로 계속한다.
            return None
        run.ai_participation = "APPLIED"
        run.ai_confidence = judgement.confidence
        return judgement

    def _size_order(
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
        if not inputs.account_complete or not inputs.account_digest_matches:
            self._release_exit_pending(run)
            state = "HALTED" if not inputs.account_digest_matches else "SKIPPED_DATA_UNAVAILABLE"
            if state == "HALTED":
                self._halt(run, now, "ACCOUNT_DRIFT")
            else:
                self._transition(run, state, "RUN_TRANSITIONED", now)
            return
        quote = run.selected_quote
        if quote is None:
            quote = self._quote(run, now, transport, _required(run.selected_symbol))
            if quote is None:
                self._release_exit_pending(run)
                return
            run.selected_quote = quote
        side = cast(Side, _required(run.selected_side))
        limit_price = _limit_price(quote, side)
        if side == "SELL":
            matches = [
                item
                for item in self.store.positions
                if item.symbol == quote.symbol and item.status == "EXIT_PENDING"
            ]
            if len(matches) != 1:
                self._halt(run, now, "SELL_POSITION_DRIFT")
                return
            quantity = matches[0].quantity
            policy = AutomationPolicySnapshot(
                policy_id=matches[0].policy_id,
                version=matches[0].policy_version,
                capital_limit_krw=inputs.policy.capital_limit_krw,
                stop_loss_bps=matches[0].stop_loss_bps,
                take_profit_bps=matches[0].take_profit_bps,
                preset="CUSTOM",
            )
        else:
            quantity = _variable_buy_quantity(inputs, limit_price)
            # 확신도는 수량을 줄이기만 한다. 상한은 정책이 이미 정했고 AI가 그것을 넘지 못한다.
            run.ai_quantity_before = quantity
            quantity = _ai_reduced_quantity(quantity, inputs.ai_confidence)
            run.ai_quantity_after = quantity
            policy = inputs.policy
            if quantity < 1:
                self._transition(run, "SKIPPED_NO_ACTION", "RUN_TRANSITIONED", now)
                return
        intent = ExactOrderIntent(
            symbol=quote.symbol,
            side=side,
            order_type="LIMIT",
            quantity=quantity,
            estimated_price=limit_price,
            estimated_amount=quantity * limit_price,
            timeframe="1d",
            strategy_id=self.store.strategy_id,
        )
        # ORDER_SIZING -> RISK_CHECKING은 예약 이벤트를 따로 쓰느라 _transition을 우회한다.
        # 같은 표로 검사되도록 여기서도 확인한다.
        if (run.state, "RISK_CHECKING") not in _LEGAL_TRANSITIONS:
            raise AutomationError("automation transition is not legal")
        run.reservation = OrderReservation(quote.symbol, side, quantity, limit_price, True, intent)
        run.policy_snapshot = policy
        run.leaves_quantity = quantity
        run.state = "RISK_CHECKING"
        run.updated_at = now
        self.store.append_event(
            run,
            "ORDER_RESERVED",
            {
                "estimatedAmount": intent.estimated_amount,
                "intentSha256": intent.sha256,
                "quantity": quantity,
                "side": side,
                "symbol": quote.symbol,
            },
            now,
        )

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
        if not inputs.account_complete or not inputs.account_digest_matches:
            self._release_exit_pending(run)
            if not inputs.account_digest_matches:
                self._halt(run, now, "ACCOUNT_DRIFT")
            else:
                self._transition(run, "SKIPPED_DATA_UNAVAILABLE", "RUN_TRANSITIONED", now)
            return
        if run.reservation is None or run.reservation.intent is None:
            self._halt(run, now, "EXACT_INTENT_MISSING")
            return
        if run.logical_submit_count >= 1:
            self._halt(run, now, "DUPLICATE_SUBMIT_ATTEMPT")
            return
        reserved_run = self.store.session_submit_reservations.get(run.session_date)
        if reserved_run not in {None, run.run_id}:
            self._halt(run, now, "SESSION_SUBMIT_CAP_EXHAUSTED")
            return
        self.store.session_submit_reservations[run.session_date] = run.run_id
        if not self._ensure_capacity(run, now, transport):
            return
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
        if not self._ensure_capacity(run, now, transport):
            return
        outcome = transport.reconcile(run.reservation)
        if run.reservation is None:
            if outcome == "UNRESOLVED":
                self._transition(run, "PENDING_RECONCILIATION", "ACCOUNT_RECONCILED", now)
                return
            self._select(run, now, inputs, transport)
            return
        snapshot = _reconcile_snapshot(outcome, run.reservation)
        if snapshot.cumulative_quantity + snapshot.leaves_quantity > run.reservation.quantity:
            self._halt(run, now, "EXECUTION_QUANTITY_DRIFT")
            return
        run.filled_quantity = snapshot.cumulative_quantity
        run.leaves_quantity = snapshot.leaves_quantity
        run.average_fill_price_krw = snapshot.average_fill_price_krw
        run.provider_exec_ref_hash = snapshot.provider_exec_ref_hash
        if not snapshot.resolved:
            self._transition(run, "PENDING_RECONCILIATION", "ACCOUNT_RECONCILED", now)
            return
        if (
            snapshot.cumulative_quantity == run.reservation.quantity
            and snapshot.leaves_quantity == 0
        ):
            self._apply_fill(
                run,
                now,
                snapshot.cumulative_quantity,
                cast(int, snapshot.average_fill_price_krw),
            )
            self._transition(run, "COMPLETED", "ACCOUNT_RECONCILED", now)
            return
        if snapshot.leaves_quantity == 0 and (snapshot.cancelled or snapshot.rejected):
            run.unfilled_terminated_quantity = (
                run.reservation.quantity - snapshot.cumulative_quantity
            )
            if snapshot.cumulative_quantity:
                self._apply_fill(
                    run,
                    now,
                    snapshot.cumulative_quantity,
                    cast(int, snapshot.average_fill_price_krw),
                )
                self._transition(run, "COMPLETED", "ACCOUNT_RECONCILED", now)
            else:
                self._release_exit_pending(run)
                self._transition(run, "CANCELLED_UNFILLED", "CANCEL_RECORDED", now)
            return
        if now.timetz().replace(tzinfo=None) < _CANCEL_TIME:
            self._transition(run, "PENDING_RECONCILIATION", "ACCOUNT_RECONCILED", now)
            return
        reservation = run.reservation
        if not self._ensure_capacity(run, now, transport):
            return
        if not transport.cancel(reservation):
            self._halt(run, now, "CANCEL_FAILED")
            return
        run.unfilled_terminated_quantity = reservation.quantity - snapshot.cumulative_quantity
        run.leaves_quantity = 0
        if snapshot.cumulative_quantity:
            self._apply_fill(
                run,
                now,
                snapshot.cumulative_quantity,
                cast(int, snapshot.average_fill_price_krw),
            )
            self._transition(run, "COMPLETED", "CANCEL_RECORDED", now)
        else:
            self._release_exit_pending(run)
            self._transition(run, "CANCELLED_UNFILLED", "CANCEL_RECORDED", now)

    def _release_exit_pending(self, run: AutomationRun) -> None:
        if run.selected_side != "SELL":
            return
        for position in self.store.positions:
            if position.symbol == run.selected_symbol and position.status == "EXIT_PENDING":
                position.status = "OPEN"

    def _apply_fill(
        self,
        run: AutomationRun,
        now: datetime,
        filled_quantity: int,
        average_fill_price_krw: int,
    ) -> None:
        symbol = _required(run.selected_symbol)
        if filled_quantity <= 0 or average_fill_price_krw <= 0:
            raise AutomationError("automation fill is invalid")
        if run.selected_side == "BUY":
            if any(
                position.symbol == symbol and position.status != "CLOSED"
                for position in self.store.positions
            ):
                raise AutomationError("bot position quantity would exceed one")
            seed = f"{run.run_id}:{symbol}:{run.session_date}".encode()
            policy = run.policy_snapshot
            if policy is None:
                raise AutomationError("automation fill policy is unavailable")
            self.store.positions.append(
                BotPosition(
                    position_id=f"auto_pos_{hashlib.sha256(seed).hexdigest()[:24]}",
                    account_id=self.store.account_id,
                    symbol=symbol,
                    entry_session=run.session_date,
                    expiry_session=_nth_next_session(run.session_date, 5),
                    created_at=now,
                    quantity=filled_quantity,
                    entry_average_fill_price_krw=average_fill_price_krw,
                    entry_notional_krw=filled_quantity * average_fill_price_krw,
                    policy_id=policy.policy_id,
                    policy_version=policy.version,
                    stop_loss_bps=policy.stop_loss_bps,
                    take_profit_bps=policy.take_profit_bps,
                )
            )
        else:
            matches = [
                position
                for position in self.store.positions
                if position.symbol == symbol and position.status in {"OPEN", "EXIT_PENDING"}
            ]
            if len(matches) != 1:
                raise AutomationError("SELL fill does not match one bot-owned position")
            position = matches[0]
            if filled_quantity > position.quantity:
                raise AutomationError("SELL fill exceeds bot-owned position")
            entry_price = position.entry_average_fill_price_krw
            position.quantity -= filled_quantity
            if entry_price is not None:
                position.entry_notional_krw = position.quantity * entry_price
            # 부분 청산이 이어져도 실현손익이 수량 가중 평균으로 누적되게 한다.
            previous_exit_quantity = position.exit_filled_quantity
            previous_exit_price = position.exit_average_fill_price_krw or 0
            position.exit_filled_quantity = previous_exit_quantity + filled_quantity
            position.exit_average_fill_price_krw = (
                previous_exit_quantity * previous_exit_price
                + filled_quantity * average_fill_price_krw
            ) // position.exit_filled_quantity
            if entry_price is not None:
                # 왕복 비용은 진입·청산 약정금액 합에 정수 35bp로 올림 적용한다.
                gross = (average_fill_price_krw - entry_price) * filled_quantity
                turnover = (average_fill_price_krw + entry_price) * filled_quantity
                cost = (turnover * _ROUND_TRIP_COST_BPS + 19_999) // 20_000
                position.realized_pnl_krw = (position.realized_pnl_krw or 0) + gross - cost
            position.exit_reason = run.exit_reason
            if position.quantity == 0:
                position.status = "CLOSED"
                position.closed_at = now
            else:
                position.status = "OPEN"
                position.closed_at = None

    def _quote(
        self,
        run: AutomationRun,
        now: datetime,
        transport: AutomationFixtureTransportPort,
        symbol: str,
    ) -> Quote | None:
        if not self._ensure_capacity(run, now, transport):
            return None
        try:
            quote = transport.quote(symbol)
        except Exception:
            self._transition(run, "SKIPPED_DATA_UNAVAILABLE", "RUN_TRANSITIONED", now)
            return None
        if quote.symbol != symbol or not quote.fresh:
            self._transition(run, "SKIPPED_DATA_UNAVAILABLE", "RUN_TRANSITIONED", now)
            return None
        return quote

    def _ensure_capacity(
        self,
        run: AutomationRun,
        now: datetime,
        transport: AutomationFixtureTransportPort,
    ) -> bool:
        if transport.physical_calls >= _MAX_PHYSICAL_CALLS:
            self._halt(run, now, "PROVIDER_CALL_CAP_EXHAUSTED")
            return False
        return True

    def _transition(
        self,
        run: AutomationRun,
        state: str,
        event_type: str,
        now: datetime,
    ) -> None:
        if (run.state, state) not in _LEGAL_TRANSITIONS:
            raise AutomationError("automation transition is not legal")
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


def _projection_integer(value: object) -> int:
    if isinstance(value, bool):
        raise AutomationError("automation account lineage number is invalid")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise AutomationError("automation account lineage number is invalid")


# 확신도가 0이어도 절반까지만 줄인다. 후보가 2-of-2 합의와 정책 상한을 이미 통과했는데
# 모델의 확신이 낮다는 이유만으로 0으로 만들면 그것은 축소가 아니라 거부권이고, 거부권은
# veto가 따로 표현한다.
_AI_SIZE_FLOOR_BPS = 5_000


def _apply_judgement(
    candidates: tuple[SignalCandidate, ...], judgement: AiJudgement | None
) -> tuple[SignalCandidate, ...]:
    """차단을 걷어내고 점수로 다시 세운다. 후보를 더하지 않는다.

    모델은 점수와 차단만 낸다. 무엇을 사고 얼마나 살지는 이 함수와 `_ai_reduced_quantity`가
    정수 산술로 계산한다. 같은 판단에 같은 결과가 나오고 그 계산은 감사 가능하다.
    """

    if judgement is None:
        return candidates
    verdicts = {item.symbol: item for item in judgement.verdicts}
    surviving = [
        candidate
        for candidate in candidates
        if not (verdict := verdicts.get(candidate.symbol)) or not verdict.veto
    ]
    return tuple(
        sorted(
            surviving,
            key=lambda candidate: (
                # 점수가 없는 후보는 중립 0.5에서 시작한다. 답을 못 받은 것이 곧 나쁜 후보라는
                # 뜻은 아니므로 맨 뒤로 밀지 않는다.
                -_score_bps(verdicts[candidate.symbol].score)
                if candidate.symbol in verdicts
                else -5_000,
                -candidate.expected_return,
                -candidate.confidence,
                candidate.symbol,
            ),
        )
    )


def _score_bps(score: float) -> int:
    """점수를 정수 basis point로 고정한다. 순서가 부동소수 비교에 걸리지 않게 한다."""

    return round(score * 10_000)


def _ai_reduced_quantity(quantity: int, confidence: float | None) -> int:
    """확신도로 수량을 줄인다. 절대 늘리지 않는다."""

    if confidence is None or quantity < 1:
        return quantity
    scale = _AI_SIZE_FLOOR_BPS + round(confidence * (10_000 - _AI_SIZE_FLOOR_BPS))
    return max(1, min(quantity, quantity * scale // 10_000))


def _variable_buy_quantity(inputs: AutomationInputs, limit_price_krw: int) -> int:
    if limit_price_krw <= 0:
        raise AutomationError("automation sizing price is invalid")
    nonnegative = (
        inputs.buyable_quantity,
        inputs.buyable_amount_krw,
        inputs.open_position_market_value_krw,
        inputs.pending_buy_notional_krw,
        inputs.principle_max_single_order_krw,
        inputs.principle_asset_remaining_krw,
    )
    if any(value < 0 for value in nonnegative):
        raise AutomationError("automation sizing input is invalid")
    slot_budget = inputs.policy.capital_limit_krw // inputs.policy.max_open_positions
    capital_remaining = max(
        0,
        inputs.policy.capital_limit_krw
        - inputs.open_position_market_value_krw
        - inputs.pending_buy_notional_krw,
    )
    order_budget = min(
        slot_budget,
        capital_remaining,
        inputs.principle_max_single_order_krw,
        inputs.principle_asset_remaining_krw,
        inputs.buyable_amount_krw,
    )
    return min(order_budget // limit_price_krw, inputs.buyable_quantity)


def _estimated_net_return_bps(entry_average_fill_price_krw: int, sell_limit_price_krw: int) -> int:
    if entry_average_fill_price_krw <= 0 or sell_limit_price_krw <= 0:
        raise AutomationError("automation exit price is invalid")
    return (
        sell_limit_price_krw * 10_000 // entry_average_fill_price_krw
        - 10_000
        - _ROUND_TRIP_COST_BPS
    )


def _reconcile_snapshot(
    value: ReconcileOutcome | ReconcileSnapshot,
    reservation: OrderReservation,
) -> ReconcileSnapshot:
    if isinstance(value, ReconcileSnapshot):
        return value
    if value == "FILLED":
        return ReconcileSnapshot(
            resolved=True,
            cumulative_quantity=reservation.quantity,
            leaves_quantity=0,
            average_fill_price_krw=reservation.limit_price_krw,
        )
    if value == "UNFILLED":
        return ReconcileSnapshot(
            resolved=True,
            cumulative_quantity=0,
            leaves_quantity=reservation.quantity,
            average_fill_price_krw=None,
        )
    return ReconcileSnapshot(
        resolved=False,
        cumulative_quantity=0,
        leaves_quantity=reservation.quantity,
        average_fill_price_krw=None,
    )


def _limit_price(quote: Quote, side: Side) -> int:
    if quote.price_krw <= 0 or quote.lower_limit_krw <= 0 or quote.upper_limit_krw <= 0:
        raise AutomationError("quote prices are invalid")
    tick = _tick_size(quote.price_krw, quote.is_etf_etn)
    price = quote.price_krw + tick if side == "BUY" else quote.price_krw - tick
    price = (
        min(quote.upper_limit_krw, price) if side == "BUY" else max(quote.lower_limit_krw, price)
    )
    # 상·하한가로 clamp하면 그 값이 속한 호가 밴드의 격자에서 벗어날 수 있고, KIS는 그런 주문을
    # 40030000 호가단위 오류로 거절한다. 매수는 내림, 매도는 올림으로 스냅해 주문 가능 범위
    # 안쪽에 남긴다.
    grid = _tick_size(price, quote.is_etf_etn)
    remainder = price % grid
    if remainder:
        price = price - remainder if side == "BUY" else price + (grid - remainder)
    if price <= 0 or price % _tick_size(price, quote.is_etf_etn):
        raise AutomationError("automation limit price is off the KRX tick grid")
    return price


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
