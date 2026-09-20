"""기존 단일 automation run 뒤 남은 세션 주문을 V163 ordinal 원장으로 안전하게 이어간다."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from app.p1_owner.automation import ExactOrderIntent, Quote, ReconcileSnapshot, _limit_price
from app.p1_owner.automation import _MAX_OPEN_POSITIONS, _ROUND_TRIP_COST_BPS
from app.p1_owner.automation_portfolio import (
    _MAX_ORDERS_PER_SESSION,
    CapitalPolicy,
    CapitalSnapshot,
    PortfolioCandidate,
    PortfolioPlan,
    PortfolioPosition,
    plan_portfolio_orders,
)
from app.p1_owner.automation_portfolio_repository import (
    PortfolioSessionBinding,
    PostgresAutomationPortfolioRepository,
)
from app.p1_owner.automation_runtime import RuntimeClaim


class PortfolioRuntimePort(Protocol):
    def portfolio_quote(self, symbol: str) -> Quote: ...

    def portfolio_buyable(self, symbol: str, estimated_price: int) -> dict[str, object]: ...

    def portfolio_evaluate(self, intent: ExactOrderIntent, ordinal: int) -> str | None: ...

    def portfolio_submit(
        self, intent: ExactOrderIntent, *, decision_id: str, ordinal: int
    ) -> dict[str, object]: ...

    def portfolio_reconcile(self, order_id: str) -> ReconcileSnapshot: ...

    # 체결 품질 원장용. 없으면 기록을 건너뛴다(getattr 로 확인한다).
    def portfolio_order_book(self, symbol: str) -> dict[str, object] | None: ...


@dataclass(frozen=True, slots=True)
class PortfolioContinuationResult:
    status: str
    planned_orders: int
    completed_orders: int


class PortfolioContinuationRunner:
    """세션 최대 3건에서 기존 run이 소비한 주문을 뺀 ordinal만 순차 제출한다."""

    def __init__(self, repository: PostgresAutomationPortfolioRepository) -> None:
        self._repository = repository

    def continue_session(
        self,
        *,
        claim: RuntimeClaim,
        state: dict[str, Any],
        port: PortfolioRuntimePort,
        allow_new_orders: bool = True,
    ) -> PortfolioContinuationResult:
        """``allow_new_orders`` 가 False 면 새 주문 계획만 막고 대사는 계속한다.

        매수 마감(09:40) 뒤에도 이미 제출된 주문은 반드시 대사해야 하므로
        시간 제한을 호출 전체가 아니라 계획 단계에만 건다.
        """

        sources = self._repository.load_sources(claim=claim)
        remaining = max(
            0,
            min(
                _int(sources["maxOrdersPerSession"]),
                _MAX_ORDERS_PER_SESSION,
            )
            - _int(sources["ordersAlreadySubmitted"]),
        )
        current = self._repository.current_execution(claim=claim)
        if remaining == 0 and current is None:
            return PortfolioContinuationResult("ORDER_BUDGET_EXHAUSTED", 0, 0)
        planned_count = 0
        if current is None:
            if not allow_new_orders:
                return PortfolioContinuationResult("BUY_WINDOW_CLOSED", 0, 0)
            prepared = self._plan(
                claim=claim, state=state, sources=sources, port=port, remaining=remaining
            )
            if prepared is None:
                return PortfolioContinuationResult("NO_ELIGIBLE_ADJUSTMENT", 0, 0)
            plan, binding = prepared
            if not plan.orders:
                return PortfolioContinuationResult("NO_ELIGIBLE_ADJUSTMENT", 0, 0)
            self._repository.stage(claim=claim, binding=binding, plan=plan)
            planned_count = len(plan.orders)
            current = self._repository.current_execution(claim=claim)
        completed = 0
        while current is not None:
            ordinal = _int(current["ordinal"])
            execution_state = str(current["state"])
            intent = _intent(cast(dict[str, object], current["exactIntent"]))
            if execution_state == "PLANNED":
                decision_id = port.portfolio_evaluate(intent, ordinal)
                if decision_id is None:
                    if self._repository.begin(
                        claim=claim,
                        ordinal=ordinal,
                        idempotency_key_hash=str(current["idempotencyKeyHash"]),
                    ):
                        self._repository.finish(
                            claim=claim,
                            ordinal=ordinal,
                            state="REJECTED",
                        )
                    completed += 1
                    current = self._repository.current_execution(claim=claim)
                    continue
                should_submit = self._repository.begin(
                    claim=claim,
                    ordinal=ordinal,
                    idempotency_key_hash=str(current["idempotencyKeyHash"]),
                )
                if not should_submit:
                    return PortfolioContinuationResult(
                        "SUBMIT_RESPONSE_UNRESOLVED", planned_count, completed
                    )
                submitted = port.portfolio_submit(intent, decision_id=decision_id, ordinal=ordinal)
                order_id = submitted.get("orderId")
                provider_ref = submitted.get("providerOrderRefHash")
                if not isinstance(order_id, str):
                    return PortfolioContinuationResult(
                        "SUBMIT_RESPONSE_UNRESOLVED", planned_count, completed
                    )
                # 제출 **뒤에** 그 한 종목의 최우선 호가를 한 번 읽어 원장에 남긴다.
                # 가격 결정에는 쓰지 않는다 - 어떤 지정가가 실제로 체결되는지를 나중에
                # 판단하기 위한 근거다. 제출 뒤이므로 간격 제한기가 지연돼도 주문을 늦추거나
                # 순서를 바꾸지 못한다. 실패하면 조용히 건너뛴다.
                self._record_order_book(claim, ordinal, port, intent.symbol)
                self._repository.finish(
                    claim=claim,
                    ordinal=ordinal,
                    state="PENDING_RECONCILIATION",
                    order_id=order_id,
                    provider_order_ref_hash=provider_ref if isinstance(provider_ref, str) else None,
                    leaves_quantity=_int(current["quantity"]),
                )
                current = self._repository.current_execution(claim=claim)
                if current is None:
                    break
                execution_state = str(current["state"])
            if execution_state == "SUBMITTING":
                return PortfolioContinuationResult(
                    "SUBMIT_RESPONSE_UNRESOLVED", planned_count, completed
                )
            order_id = current.get("orderId")
            if execution_state != "PENDING_RECONCILIATION" or not isinstance(order_id, str):
                return PortfolioContinuationResult(
                    "EXECUTION_STATE_INVALID", planned_count, completed
                )
            snapshot = port.portfolio_reconcile(order_id)
            if not snapshot.resolved:
                return PortfolioContinuationResult(
                    "PENDING_RECONCILIATION", planned_count, completed
                )
            receipt = _receipt_state(snapshot, _int(current["quantity"]))
            self._repository.finish(
                claim=claim,
                ordinal=ordinal,
                state=receipt,
                order_id=order_id,
                provider_order_ref_hash=cast(str | None, current.get("providerOrderRefHash")),
                filled_quantity=snapshot.cumulative_quantity,
                leaves_quantity=snapshot.leaves_quantity,
                average_fill_price_krw=snapshot.average_fill_price_krw,
                provider_exec_ref_hash=snapshot.provider_exec_ref_hash,
            )
            if receipt == "PENDING_RECONCILIATION":
                # 체결된 만큼은 방금 장부에 들어갔다. 이 실행은 아직 비terminal 이라
                # `current_execution` 이 같은 행을 다시 돌려준다 - 여기서 나가지 않으면
                # while 루프가 돈다. **실행 하나당 tick 하나당 finish 한 번**이 규칙이고,
                # 그래서 spin 이 구조적으로 불가능하다. 다음 tick 이 다시 대사한다.
                return PortfolioContinuationResult(
                    "PENDING_RECONCILIATION", planned_count, completed
                )
            completed += 1
            current = self._repository.current_execution(claim=claim)
        return PortfolioContinuationResult("COMPLETE", planned_count, completed)

    def _record_order_book(
        self, claim: RuntimeClaim, ordinal: int, port: object, symbol: str
    ) -> None:
        """제출 순간의 호가를 원장에 남긴다. 실패는 삼킨다 - 원장이 거래를 막을 수 없다."""

        snapshot_of = getattr(port, "portfolio_order_book", None)
        recorder = getattr(self._repository, "record_book", None)
        if not callable(snapshot_of) or not callable(recorder):
            return
        try:
            book = snapshot_of(symbol)
        except Exception as error:
            print(
                f"AUTOMATION_PORTFOLIO_BOOK=UNAVAILABLE error={type(error).__name__}",
                flush=True,
            )
            return
        if book is None:
            return
        try:
            recorder(claim=claim, ordinal=ordinal, book=book)
        except Exception as error:
            # 저장소가 터져도 세션은 계속된다. 원장이 거래를 막을 수 없어야 한다.
            print(
                f"AUTOMATION_PORTFOLIO_BOOK=FAILED error={type(error).__name__}",
                flush=True,
            )

    def _record_candidate_drop(self, claim: RuntimeClaim, symbol: str, reason: str) -> None:
        """후보가 탈락한 사유를 퍼널에 남긴다. 진단이라 실패해도 세션을 멈추지 않는다."""

        recorder = getattr(self._repository, "record_stage_outcomes", None)
        if not callable(recorder):
            return
        try:
            recorder(claim, symbol, reason)
        except Exception as error:
            print(
                f"AUTOMATION_PORTFOLIO_CANDIDATE_DROP=FAILED error={type(error).__name__}",
                flush=True,
            )

    def _plan(
        self,
        *,
        claim: RuntimeClaim,
        state: dict[str, Any],
        sources: dict[str, object],
        port: PortfolioRuntimePort,
        remaining: int,
    ) -> tuple[PortfolioPlan, PortfolioSessionBinding] | None:
        # 오늘 산 것을 목표비중 초과라는 이유로 같은 날 되팔지 않는다. 날짜 비교는
        # 여기서 하고 계획 함수는 순수하게 둔다. sessionDate 가 없는 옛 payload 는
        # 아무것도 "오늘 진입"으로 보지 않아 기존 동작을 유지한다.
        session_date = str(sources.get("sessionDate", "")) or None
        positions = tuple(
            PortfolioPosition(
                str(item["symbol"]),
                _int(item["quantity"]),
                _int(item["priceKrw"]),
                entered_today=(
                    session_date is not None and str(item.get("entrySession")) == session_date
                ),
            )
            for item in cast(list[dict[str, object]], sources["positions"])
        )
        vetoed = {
            str(item.get("symbol"))
            for item in cast(list[dict[str, object]], state.get("screenings", []))
            if item.get("verdict") == "VETO_BUY" and item.get("status") == "AVAILABLE"
        }
        candidates: list[PortfolioCandidate] = []
        buyable_receipts: list[tuple[int, str]] = []
        raw_signals = [
            item
            for item in cast(list[dict[str, object]], state.get("signals", []))
            if item.get("baselineSignal") == "BUY"
            and item.get("lstmSignal") != "SELL"
            and str(item.get("symbol")) not in vetoed
        ]
        raw_signals.sort(
            key=lambda item: (-_float(item.get("expectedReturn", 0)), str(item.get("symbol")))
        )
        for item in raw_signals[:5]:
            expected = _float(item.get("expectedReturn", math.nan))
            if not math.isfinite(expected) or expected <= 0:
                continue
            symbol = str(item["symbol"])
            quote = port.portfolio_quote(symbol)
            price = _limit_price(quote, "BUY")
            forecast_close = _float(item.get("forecastClose", math.nan))
            # 비용 상수는 단일 주문 엔진과 **같은 것**이어야 한다. 예전에는 여기만 0.0035 로
            # 박혀 있어, 상수를 조정하면 두 경로가 말없이 갈라졌다.
            if (
                not math.isfinite(forecast_close)
                or forecast_close / price - 1 - _ROUND_TRIP_COST_BPS / 10_000 <= 0
            ):
                # 맨 `continue` 는 후보를 아무 기록 없이 버렸다. 단일 엔진 경로에는 퍼널이
                # 붙어 있고 이쪽만 비어 있어서, 무주문 사유가 화면에 남지 않았다.
                self._record_candidate_drop(claim, symbol, "NO_REMAINING_RETURN")
                continue
            buyable = port.portfolio_buyable(symbol, price)
            receipt_id = self._repository.record_buyable(claim=claim, projection=buyable)
            amount = _int(buyable["buyableAmountKrw"])
            buyable_receipts.append((amount, receipt_id))
            principle_cap = min(
                _int(state.get("principleMaxSingleOrderKrw", amount)),
                _int(state.get("principleAssetRemainingKrw", amount)),
                amount,
            )
            candidates.append(
                PortfolioCandidate(
                    symbol,
                    price,
                    min(_int(buyable["buyableQuantity"]), principle_cap // price),
                    expected,
                )
            )
        if not buyable_receipts:
            return None
        broker_buyable_cash, buyable_receipt_id = min(buyable_receipts)
        policy = CapitalPolicy(
            reinvest_realized_pnl=bool(sources["reinvestRealizedPnl"]),
            # 상한은 사용자 원칙이 정한다. 값이 없을 때의 기본은 단일 주문 엔진과 **같아야**
            # 한다. 예전에는 여기만 5 였고 엔진은 10 이라, 옛 payload 로 도는 세션에서
            # 보유 6종목이면 엔진은 사고 포트폴리오는 못 사는 상태가 됐다.
            max_open_positions=_int(sources.get("maxOpenPositions", _MAX_OPEN_POSITIONS)),
            max_orders_per_session=remaining,
        )
        capital = CapitalSnapshot(
            _int(sources["configuredCapitalKrw"]),
            _int(sources["realizedPnlSinceTransitionKrw"]),
            broker_buyable_cash,
            _int(sources["botPositionMarketValueKrw"]),
            _int(sources["reservedBuyCashKrw"]),
        )
        plan = plan_portfolio_orders(
            policy=policy,
            capital=capital,
            positions=positions,
            candidates=tuple(candidates),
            forced_exit_symbols=frozenset(
                str(item["symbol"])
                for item in cast(list[dict[str, object]], sources["positions"])
                if item.get("status") == "EXIT_PENDING"
            ),
            principle_orders_remaining=remaining,
        )
        binding = PortfolioSessionBinding(
            automation_policy_id=str(sources["automationPolicyId"]),
            automation_policy_version=_int(sources["automationPolicyVersion"]),
            capital_policy_version=_int(sources["capitalPolicyVersion"]),
            principle_version_id=str(sources["principleVersionId"]),
            principle_version=_int(sources["principleVersion"]),
            configured_capital_krw=_int(sources["configuredCapitalKrw"]),
            realized_pnl_since_transition_krw=_int(sources["realizedPnlSinceTransitionKrw"]),
            broker_buyable_cash_krw=broker_buyable_cash,
            bot_position_market_value_krw=_int(sources["botPositionMarketValueKrw"]),
            reserved_buy_cash_krw=_int(sources["reservedBuyCashKrw"]),
            balance_observation_id=str(sources["balanceObservationId"]),
            buyable_receipt_id=buyable_receipt_id,
        )
        return plan, binding


def _intent(value: dict[str, object]) -> ExactOrderIntent:
    return ExactOrderIntent(
        symbol=str(value["symbol"]),
        side=cast(Any, str(value["side"])),
        order_type=cast(Any, str(value["orderType"])),
        quantity=_int(value["quantity"]),
        estimated_price=_int(value["estimatedPrice"]),
        estimated_amount=_int(value["estimatedAmount"]),
        timeframe=cast(Any, str(value["timeframe"])),
        strategy_id=str(value["strategyId"]),
    )


def _receipt_state(
    snapshot: ReconcileSnapshot, quantity: int
) -> Literal["PENDING_RECONCILIATION", "FILLED", "CANCELLED", "REJECTED"]:
    """브로커 영수증을 실행 상태로 옮긴다.

    예전에는 전량 체결이나 취소·거부가 아니면 무조건 예외였다. 그래서 **부분체결이
    `continue_session` 전체를 중단시켰고 이미 체결된 주식이 장부에 들어가지 않았다.**
    아직 잔량이 남아 있고 취소·거부도 아니면 그것은 "브로커에 살아 있고 결과 미확정",
    곧 `PENDING_RECONCILIATION` 이다 - 새 상태를 만들 이유가 없다.

    앞뒤가 맞지 않는 영수증(잔량 0 인데 terminal 표식도 없고 수량도 안 맞음)은 그대로
    거절한다. 사유 코드는 유지하고 적용 범위만 좁아진다.
    """

    if snapshot.cumulative_quantity == quantity and snapshot.leaves_quantity == 0:
        return "FILLED"
    if snapshot.leaves_quantity == 0 and snapshot.cancelled:
        return "CANCELLED"
    if snapshot.leaves_quantity == 0 and snapshot.rejected:
        return "REJECTED"
    if snapshot.leaves_quantity > 0 and not snapshot.cancelled and not snapshot.rejected:
        # 부분체결과 "아직 아무것도 안 붙은 살아 있는 주문" 둘 다 여기로 온다.
        # 후자도 예전에는 예외였다 - 조용한 두 번째 버그였다.
        return "PENDING_RECONCILIATION"
    raise ValueError("AUTOMATION_PORTFOLIO_TERMINAL_RECEIPT_INVALID")


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("AUTOMATION_PORTFOLIO_INTEGER_INVALID")
    return int(value)


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("AUTOMATION_PORTFOLIO_FLOAT_INVALID")
    return float(value)
