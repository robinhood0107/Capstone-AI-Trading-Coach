"""파이프라인 관통 테스트에서 KIS만 대체한다. bridge·RiskEngine·DB는 전부 실제다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않는다.

가짜는 두 port뿐이다.
  `QuoteSourcePort`      시세 조회
  `ExecutionSourcePort`  잔고·체결 조회

`FixtureAutomationTransport`를 상속하지 않는다. 상속하면 엔진의 physical-call 회계가 0인 채로
테스트가 통과해 bridge를 한 번도 부르지 않고도 성공한 것처럼 보인다.

잔고는 작은 원장으로 유지한다. `AccountLineageSnapshot.permits_fill`(automation.py)이 체결 델타가
자기 주문으로 설명되는지 검사하므로, 체결마다 현금과 보유수량이 정확히 그 계약대로 움직여야 한다.
움직이지 않으면 lineage가 전진하지 않고 다음 세션이 `ACCOUNT_DRIFT`로 HALT한다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final

from app.p1_owner.automation import Quote, ReconcileOutcome, ReconcileSnapshot

_ROUND_TRIP_COST_BPS: Final = 35


class KisFakeError(RuntimeError):
    """가짜 KIS가 계약을 벗어난 요청을 받았다."""


def round_trip_cost(notional: int) -> int:
    """엔진과 같은 올림 규칙을 쓴다. 다르면 `permits_fill`이 조용히 실패한다."""

    return (notional * _ROUND_TRIP_COST_BPS + 9_999) // 10_000


@dataclass
class AccountLedger:
    """체결이 일어날 때만 움직이는 결정적 계좌. 시각이나 난수에 의존하지 않는다."""

    account_id: str
    cash_krw: int
    positions: dict[str, int] = field(default_factory=dict)
    market_prices: dict[str, int] = field(default_factory=dict)
    applied_orders: set[str] = field(default_factory=set)

    def apply_fill(
        self, *, order_id: str, symbol: str, side: str, quantity: int, price_krw: int
    ) -> None:
        if order_id in self.applied_orders:
            return
        if quantity <= 0 or price_krw <= 0:
            raise KisFakeError("a fill must have a positive quantity and price")
        notional = quantity * price_krw
        cost = round_trip_cost(notional)
        if side == "BUY":
            self.cash_krw -= notional + cost
            self.positions[symbol] = self.positions.get(symbol, 0) + quantity
        elif side == "SELL":
            held = self.positions.get(symbol, 0)
            if held < quantity:
                raise KisFakeError("a SELL cannot exceed the held quantity")
            self.cash_krw += notional - cost
            if held == quantity:
                self.positions.pop(symbol, None)
            else:
                self.positions[symbol] = held - quantity
        else:
            raise KisFakeError(f"unknown side: {side}")
        self.market_prices[symbol] = price_krw
        self.applied_orders.add(order_id)

    def balance(self) -> dict[str, Any]:
        positions: list[dict[str, Any]] = []
        market_value_total = 0
        for symbol, quantity in sorted(self.positions.items()):
            market_value = quantity * self.market_prices.get(symbol, 0)
            market_value_total += market_value
            positions.append(
                {
                    "isGoldEtfEtn": False,
                    "marketValueKrw": market_value,
                    "quantity": quantity,
                    "symbol": symbol,
                }
            )
        equity = self.cash_krw + market_value_total
        return {
            "accountId": self.account_id,
            "cashKrw": self.cash_krw,
            "marginRequirementKrw": 0,
            "portfolioEquityKrw": equity,
            "positionsComplete": True,
            "positions": positions,
        }


@dataclass(frozen=True, slots=True)
class SubmittedOrder:
    order_id: str
    symbol: str
    side: str
    quantity: int
    price_krw: int


class LedgerQuoteSource:
    """호가 격자 위의 결정적 시세. `_limit_price`가 격자를 벗어나면 예외를 던진다."""

    def __init__(self, prices: dict[str, int]) -> None:
        self._prices = dict(prices)
        self.calls = 0

    def quote(self, symbol: str) -> Quote:
        price = self._prices.get(symbol)
        if price is None:
            raise KisFakeError(f"no fixture quote for {symbol}")
        self.calls += 1
        return Quote(
            symbol=symbol,
            price_krw=price,
            lower_limit_krw=int(price * 0.7) // 100 * 100,
            upper_limit_krw=int(price * 1.3) // 100 * 100,
            fresh=True,
            is_etf_etn=False,
        )

    def close(self) -> None:
        return None


class LedgerExecutionSource:
    """브로커가 실제로 기록한 주문을 읽어 그대로 전량 체결시킨다.

    주문의 종목·방향·수량·가격을 테스트가 미리 정하지 않고 `public.orders`에서 읽는다. 파이프라인이
    실제로 낸 주문과 체결이 어긋날 수 없게 하기 위해서다. 읽기 전용 조회 한 번뿐이다.
    """

    def __init__(self, ledger: AccountLedger, *, connect: Callable[[], Any]) -> None:
        self._ledger = ledger
        self._connect = connect
        self._seen: dict[str, SubmittedOrder] = {}
        self.balance_calls = 0
        self.read_calls = 0

    @property
    def filled_orders(self) -> tuple[SubmittedOrder, ...]:
        return tuple(self._seen.values())

    def _lookup(self, order_id: str) -> SubmittedOrder | None:
        cached = self._seen.get(order_id)
        if cached is not None:
            return cached
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select symbol, side, quantity, submitted_price_krw from public.orders"
                " where order_id = %s",
                (order_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        symbol, side, quantity, price = row
        order = SubmittedOrder(order_id, str(symbol), str(side), int(quantity), int(price))
        self._seen[order_id] = order
        return order

    def balance(self, account_id: str) -> dict[str, Any]:
        if account_id != self._ledger.account_id:
            raise KisFakeError("balance requested for an unexpected account")
        self.balance_calls += 1
        return self._ledger.balance()

    def read(
        self, order_id: str, account_id: str, session_date: date
    ) -> ReconcileOutcome | ReconcileSnapshot:
        del account_id, session_date
        self.read_calls += 1
        order = self._lookup(order_id)
        if order is None:
            return "UNRESOLVED"
        self._ledger.apply_fill(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price_krw=order.price_krw,
        )
        return ReconcileSnapshot(
            resolved=True,
            cumulative_quantity=order.quantity,
            leaves_quantity=0,
            average_fill_price_krw=order.price_krw,
        )

    def require_closed(self, order_id: str, account_id: str, session_date: date) -> bool:
        del account_id, session_date
        return order_id in self._ledger.applied_orders

    def close(self) -> None:
        return None
