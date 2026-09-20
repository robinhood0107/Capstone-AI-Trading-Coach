"""다종목 목표비중 계획과 주문별 멱등 실행 원장. 수량 상한은 외부 RiskEngine 결과만 소비한다."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Literal

from app.data._shared.canonical_json import canonical_json_bytes

Side = Literal["BUY", "SELL"]
Phase = Literal["EXIT", "REDUCE", "INCREASE", "ENTRY"]
ExecutionState = Literal[
    "PLANNED",
    "SUBMITTING",
    "PENDING_RECONCILIATION",
    "FILLED",
    "CANCELLED",
    "REJECTED",
]
_MAX_BIGINT = 9_223_372_036_854_775_807
#: 세션 주문 상한. DB CHECK(automation_capital_policy_versions_v1)와 ordinal 원장,
#: 그리고 KIS 호출 예산(주문 1건당 3~4콜)이 함께 이 수를 지탱해야 한다.
#: 이 값만 올리고 호출 예산을 안 올리면 예산이 먼저 소진돼 효과가 없다.
_MAX_ORDERS_PER_SESSION = 5


class PortfolioAutomationError(ValueError):
    """자본 snapshot, RiskEngine 수량 또는 주문 실행 전이가 계약을 벗어났다."""


@dataclass(frozen=True, slots=True)
class CapitalPolicy:
    reinvest_realized_pnl: bool = True
    cash_buffer_bps: int = 100
    rebalance_deviation_bps: int = 200
    minimum_adjustment_krw: int = 10_000
    max_open_positions: int = 10
    max_orders_per_session: int = 5

    def __post_init__(self) -> None:
        if (
            type(self.reinvest_realized_pnl) is not bool
            or not all(
                _is_strict_int(value)
                for value in (
                    self.cash_buffer_bps,
                    self.rebalance_deviation_bps,
                    self.minimum_adjustment_krw,
                    self.max_open_positions,
                    self.max_orders_per_session,
                )
            )
            or self.cash_buffer_bps != 100
            or self.rebalance_deviation_bps != 200
            or self.minimum_adjustment_krw != 10_000
            # 상한은 사용자가 고르는 값이다. AutomationPolicySnapshot 과 같은 범위를 쓴다.
            or not 1 <= self.max_open_positions <= 20
            or not 1 <= self.max_orders_per_session <= _MAX_ORDERS_PER_SESSION
        ):
            raise PortfolioAutomationError("automation capital policy is invalid")


@dataclass(frozen=True, slots=True)
class CapitalSnapshot:
    configured_capital_krw: int
    realized_pnl_since_transition_krw: int
    broker_buyable_cash_krw: int
    bot_position_market_value_krw: int
    reserved_buy_cash_krw: int = 0

    def __post_init__(self) -> None:
        if (
            not all(
                _is_strict_int(value)
                for value in (
                    self.configured_capital_krw,
                    self.realized_pnl_since_transition_krw,
                    self.broker_buyable_cash_krw,
                    self.bot_position_market_value_krw,
                    self.reserved_buy_cash_krw,
                )
            )
            or self.configured_capital_krw < 0
            or self.broker_buyable_cash_krw < 0
            or self.bot_position_market_value_krw < 0
            or self.reserved_buy_cash_krw < 0
            or any(
                abs(value) > _MAX_BIGINT
                for value in (
                    self.configured_capital_krw,
                    self.realized_pnl_since_transition_krw,
                    self.broker_buyable_cash_krw,
                    self.bot_position_market_value_krw,
                    self.reserved_buy_cash_krw,
                )
            )
        ):
            raise PortfolioAutomationError("automation capital snapshot is invalid")


@dataclass(frozen=True, slots=True)
class PortfolioPosition:
    symbol: str
    quantity: int
    price_krw: int
    bot_owned: bool = True
    #: 이 세션에 진입했는가. 참이면 목표비중 초과라도 같은 날 되팔지 않는다.
    #: 날짜 비교는 세션일을 아는 호출자가 하고, 계획 함수는 순수하게 남는다.
    entered_today: bool = False

    def __post_init__(self) -> None:
        _symbol(self.symbol)
        if (
            not _is_strict_int(self.quantity)
            or not _is_strict_int(self.price_krw)
            or self.quantity <= 0
            or self.price_krw <= 0
            or self.quantity > _MAX_BIGINT
            or self.price_krw > _MAX_BIGINT
            or self.quantity > _MAX_BIGINT // self.price_krw
            or type(self.bot_owned) is not bool
            or type(self.entered_today) is not bool
        ):
            raise PortfolioAutomationError("automation position valuation is invalid")

    @property
    def market_value_krw(self) -> int:
        return self.quantity * self.price_krw


@dataclass(frozen=True, slots=True)
class PortfolioCandidate:
    symbol: str
    price_krw: int
    risk_quantity: int
    expected_return: float
    eligible: bool = True

    def __post_init__(self) -> None:
        _symbol(self.symbol)
        if (
            not _is_strict_int(self.price_krw)
            or not _is_strict_int(self.risk_quantity)
            or self.price_krw <= 0
            or self.risk_quantity < 0
            or self.price_krw > _MAX_BIGINT
            or self.risk_quantity > _MAX_BIGINT
            or self.risk_quantity > _MAX_BIGINT // self.price_krw
            or isinstance(self.expected_return, bool)
            or not isinstance(self.expected_return, (int, float))
            or type(self.eligible) is not bool
        ):
            raise PortfolioAutomationError("automation candidate sizing is invalid")


@dataclass(frozen=True, slots=True)
class PlannedOrder:
    ordinal: int
    phase: Phase
    symbol: str
    side: Side
    quantity: int
    limit_price_krw: int
    current_quantity: int
    target_quantity: int
    reason: str

    def __post_init__(self) -> None:
        _symbol(self.symbol)
        if (
            not all(
                _is_strict_int(value)
                for value in (
                    self.ordinal,
                    self.quantity,
                    self.limit_price_krw,
                    self.current_quantity,
                    self.target_quantity,
                )
            )
            or self.ordinal < 1
            or self.quantity < 1
            or self.limit_price_krw < 1
            or self.current_quantity < 0
            or self.target_quantity < 0
            or self.quantity > _MAX_BIGINT // self.limit_price_krw
        ):
            raise PortfolioAutomationError("automation planned order is invalid")

    @property
    def estimated_amount_krw(self) -> int:
        return self.quantity * self.limit_price_krw

    def intent(self, strategy_id: str) -> dict[str, object]:
        """현재 claim의 strategy를 exact 8-field intent에 결속한다."""

        _strategy_id(strategy_id)
        return {
            "estimatedAmount": self.estimated_amount_krw,
            "estimatedPrice": self.limit_price_krw,
            "orderType": "LIMIT",
            "quantity": self.quantity,
            "side": self.side,
            "strategyId": strategy_id,
            "symbol": self.symbol,
            "timeframe": "1d",
        }

    def intent_sha256(self, strategy_id: str) -> str:
        return hashlib.sha256(canonical_json_bytes(self.intent(strategy_id))).hexdigest()


@dataclass(frozen=True, slots=True)
class PortfolioPlan:
    allocation_cap_krw: int
    investable_cap_krw: int
    target_per_position_krw: int
    available_buy_cash_krw: int
    orders: tuple[PlannedOrder, ...]
    unused_cash_reason: str | None


def plan_portfolio_orders(
    *,
    policy: CapitalPolicy,
    capital: CapitalSnapshot,
    positions: tuple[PortfolioPosition, ...],
    candidates: tuple[PortfolioCandidate, ...],
    forced_exit_symbols: frozenset[str] = frozenset(),
    unresolved_order: bool = False,
    principle_orders_remaining: int = _MAX_ORDERS_PER_SESSION,
) -> PortfolioPlan:
    """청산→축소→증액/신규 순으로 계획하고 매수 예약금·미확정 매도대금은 재사용하지 않는다."""

    if unresolved_order:
        raise PortfolioAutomationError("automation unresolved order must reconcile first")
    if (
        not _is_strict_int(principle_orders_remaining)
        or not 0 <= principle_orders_remaining <= _MAX_ORDERS_PER_SESSION
    ):
        raise PortfolioAutomationError("automation principle order budget is invalid")
    symbols = [item.symbol for item in positions]
    if len(symbols) != len(set(symbols)) or any(not item.bot_owned for item in positions):
        raise PortfolioAutomationError("automation positions must be distinct bot-owned lots")
    candidate_symbols = [item.symbol for item in candidates]
    if len(candidate_symbols) != len(set(candidate_symbols)):
        raise PortfolioAutomationError("automation candidates must be distinct")
    for symbol in forced_exit_symbols:
        _symbol(symbol)
    # 손실은 재투자 설정과 무관하게 봇 배정자본을 줄인다. OFF는 이익만 유보한다.
    pnl_adjustment = (
        capital.realized_pnl_since_transition_krw
        if policy.reinvest_realized_pnl
        else min(0, capital.realized_pnl_since_transition_krw)
    )
    configured = max(0, capital.configured_capital_krw + pnl_adjustment)
    if configured > _MAX_BIGINT:
        raise PortfolioAutomationError("automation capital snapshot is invalid")
    # 계좌 전체 현금을 봇 자금으로 간주하지 않고 설정자금+확정손익을 절대 상한으로 둔다.
    allocation_cap = min(
        configured,
        capital.broker_buyable_cash_krw + capital.bot_position_market_value_krw,
    )
    investable = allocation_cap * (10_000 - policy.cash_buffer_bps) // 10_000
    target = investable // policy.max_open_positions
    buy_cash = max(
        0,
        min(capital.broker_buyable_cash_krw, investable - capital.bot_position_market_value_krw)
        - capital.reserved_buy_cash_krw,
    )
    cap = min(policy.max_orders_per_session, principle_orders_remaining)
    planned: list[PlannedOrder] = []
    by_symbol = {item.symbol: item for item in positions}
    eligible = {
        item.symbol: item
        for item in candidates
        if item.eligible
        and item.risk_quantity > 0
        and math.isfinite(item.expected_return)
        and item.expected_return > 0
        and item.symbol not in forced_exit_symbols
    }

    def add(
        phase: Phase,
        symbol: str,
        side: Side,
        quantity: int,
        current_quantity: int,
        target_quantity: int,
        price: int,
        reason: str,
    ) -> None:
        if len(planned) >= cap or quantity < 1:
            return
        # 손절/필수 청산은 리밸런싱 편의 임계값보다 우선한다.
        if phase != "EXIT" and quantity * price < policy.minimum_adjustment_krw:
            return
        planned.append(
            PlannedOrder(
                len(planned) + 1,
                phase,
                symbol,
                side,
                quantity,
                price,
                current_quantity,
                target_quantity,
                reason,
            )
        )

    for symbol in sorted(forced_exit_symbols):
        position = by_symbol.get(symbol)
        if position is not None:
            add(
                "EXIT",
                symbol,
                "SELL",
                position.quantity,
                position.quantity,
                0,
                position.price_krw,
                "EXIT_RULE",
            )
    deviation = allocation_cap * policy.rebalance_deviation_bps // 10_000
    for position in sorted(positions, key=lambda item: (-item.market_value_krw, item.symbol)):
        if (
            position.symbol in forced_exit_symbols
            # 오늘 산 것을 목표비중 초과라는 이유로 같은 날 되팔지 않는다.
            # 이 경로에는 entry_session 가드가 없어서 당일 회전이 열려 있었다.
            or position.entered_today
            or position.market_value_krw - target < deviation
        ):
            continue
        target_quantity = target // position.price_krw
        add(
            "REDUCE",
            position.symbol,
            "SELL",
            position.quantity - target_quantity,
            position.quantity,
            target_quantity,
            position.price_krw,
            "TARGET_WEIGHT_OVER",
        )
    for symbol, candidate in sorted(
        eligible.items(), key=lambda item: (-item[1].expected_return, item[0])
    ):
        if len(planned) >= cap:
            break
        position = by_symbol.get(symbol)
        if position is not None:
            if target - position.market_value_krw < deviation:
                continue
            desired = max(0, target // candidate.price_krw - position.quantity)
            phase: Phase = "INCREASE"
            current = position.quantity
        else:
            occupied = len(positions) + sum(1 for item in planned if item.phase == "ENTRY")
            if occupied >= policy.max_open_positions:
                continue
            desired = target // candidate.price_krw
            phase = "ENTRY"
            current = 0
        quantity = min(desired, candidate.risk_quantity, buy_cash // candidate.price_krw)
        before = len(planned)
        add(
            phase,
            symbol,
            "BUY",
            quantity,
            current,
            current + quantity,
            candidate.price_krw,
            "TARGET_WEIGHT_UNDER" if phase == "INCREASE" else "ELIGIBLE_EMPTY_SLOT",
        )
        if len(planned) > before:
            buy_cash -= planned[-1].estimated_amount_krw
    reason = None
    if not planned:
        reason = "NO_ELIGIBLE_ADJUSTMENT"
    elif buy_cash > 0:
        reason = "BUFFER_OR_NO_MORE_ELIGIBLE_CANDIDATES"
    return PortfolioPlan(allocation_cap, investable, target, buy_cash, tuple(planned), reason)


@dataclass(slots=True)
class PortfolioExecution:
    order: PlannedOrder
    state: ExecutionState = "PLANNED"
    idempotency_key_hash: str | None = None
    provider_order_ref_hash: str | None = None


@dataclass(slots=True)
class PortfolioExecutionJournal:
    """한 번에 주문 하나만 열고 응답 유실은 대사 전까지 다음 주문과 자금 사용을 막는다."""

    executions: list[PortfolioExecution]
    replayed_submit_hashes: set[str] = field(default_factory=set)

    def current(self) -> PortfolioExecution | None:
        return next(
            (
                item
                for item in self.executions
                if item.state not in {"FILLED", "CANCELLED", "REJECTED"}
            ),
            None,
        )

    def begin_submit(self, *, ordinal: int, idempotency_key_hash: str) -> bool:
        current = self.current()
        if current is None or current.order.ordinal != ordinal:
            raise PortfolioAutomationError("automation order execution is not current")
        if not idempotency_key_hash.startswith("sha256:") or len(idempotency_key_hash) != 71:
            raise PortfolioAutomationError("automation order idempotency is invalid")
        if current.idempotency_key_hash == idempotency_key_hash:
            self.replayed_submit_hashes.add(idempotency_key_hash)
            return False
        if current.state != "PLANNED" or current.idempotency_key_hash is not None:
            raise PortfolioAutomationError("automation order submit would duplicate")
        current.idempotency_key_hash = idempotency_key_hash
        current.state = "SUBMITTING"
        return True

    def record_ambiguous(self, *, ordinal: int) -> None:
        current = self.current()
        if current is None or current.order.ordinal != ordinal or current.state != "SUBMITTING":
            raise PortfolioAutomationError("automation ambiguous result is not current")
        current.state = "PENDING_RECONCILIATION"

    def resolve(self, *, ordinal: int, outcome: Literal["FILLED", "CANCELLED", "REJECTED"]) -> None:
        current = self.current()
        if current is None or current.order.ordinal != ordinal:
            raise PortfolioAutomationError("automation reconciliation is not current")
        if current.state not in {"SUBMITTING", "PENDING_RECONCILIATION"}:
            raise PortfolioAutomationError("automation order was not submitted")
        current.state = outcome


def _symbol(value: str) -> None:
    if not isinstance(value, str) or len(value) != 6 or not value.isascii() or not value.isdigit():
        raise PortfolioAutomationError("automation symbol is invalid")


def _is_strict_int(value: object) -> bool:
    """DB bigint 경계에서 bool을 정수로 받아들이지 않는다."""

    return type(value) is int


def _strategy_id(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"strategy_[A-Za-z0-9_-]{8,96}", value) is None:
        raise PortfolioAutomationError("automation strategy is invalid")
