from __future__ import annotations

import math

import pytest

from app.p1_owner.automation_portfolio import (
    CapitalPolicy,
    CapitalSnapshot,
    PortfolioAutomationError,
    PortfolioCandidate,
    PortfolioExecution,
    PortfolioExecutionJournal,
    PortfolioPosition,
    plan_portfolio_orders,
)


def test_reinvestment_and_three_order_budget_reserve_cash_without_sell_proceeds() -> None:
    plan = plan_portfolio_orders(
        policy=CapitalPolicy(reinvest_realized_pnl=True),
        capital=CapitalSnapshot(
            configured_capital_krw=50_000_000,
            realized_pnl_since_transition_krw=1_000_000,
            broker_buyable_cash_krw=30_000_000,
            bot_position_market_value_krw=20_000_000,
        ),
        positions=(PortfolioPosition("005930", 400, 50_000),),
        candidates=(
            PortfolioCandidate("000660", 200_000, 50, 0.03),
            PortfolioCandidate("035420", 100_000, 100, 0.02),
        ),
    )
    assert (
        plan.allocation_cap_krw == 50_000_000
    )  # 계좌 증거가 5천만원이라 확정손익 전액을 꾸미지 않는다.
    assert [item.phase for item in plan.orders] == ["REDUCE", "ENTRY", "ENTRY"]
    assert (
        sum(item.estimated_amount_krw for item in plan.orders if item.side == "BUY") <= 30_000_000
    )


def test_reinvestment_off_and_ineligible_or_manual_positions_are_not_added() -> None:
    plan = plan_portfolio_orders(
        policy=CapitalPolicy(reinvest_realized_pnl=False),
        capital=CapitalSnapshot(50_000_000, 5_000_000, 50_000_000, 0),
        positions=(),
        candidates=(
            PortfolioCandidate("005930", 50_000, 100, -0.01),
            PortfolioCandidate("000660", 200_000, 0, 0.03),
        ),
    )
    assert plan.allocation_cap_krw == 50_000_000
    assert plan.orders == () and plan.unused_cash_reason == "NO_ELIGIBLE_ADJUSTMENT"
    with pytest.raises(PortfolioAutomationError, match="bot-owned"):
        plan_portfolio_orders(
            policy=CapitalPolicy(),
            capital=CapitalSnapshot(10_000_000, 0, 10_000_000, 100_000),
            positions=(PortfolioPosition("005930", 2, 50_000, bot_owned=False),),
            candidates=(),
        )


def test_unresolved_order_blocks_planning_and_ambiguous_submit_blocks_next_order() -> None:
    with pytest.raises(PortfolioAutomationError, match="reconcile first"):
        plan_portfolio_orders(
            policy=CapitalPolicy(),
            capital=CapitalSnapshot(10_000_000, 0, 10_000_000, 0),
            positions=(),
            candidates=(),
            unresolved_order=True,
        )
    plan = plan_portfolio_orders(
        policy=CapitalPolicy(),
        capital=CapitalSnapshot(10_000_000, 0, 10_000_000, 0),
        positions=(),
        candidates=(
            PortfolioCandidate("005930", 50_000, 20, 0.03),
            PortfolioCandidate("000660", 100_000, 10, 0.02),
        ),
    )
    journal = PortfolioExecutionJournal([PortfolioExecution(order) for order in plan.orders])
    key = "sha256:" + "a" * 64
    assert journal.begin_submit(ordinal=1, idempotency_key_hash=key)
    journal.record_ambiguous(ordinal=1)
    with pytest.raises(PortfolioAutomationError, match="not current"):
        journal.begin_submit(ordinal=2, idempotency_key_hash="sha256:" + "b" * 64)
    assert journal.begin_submit(ordinal=1, idempotency_key_hash=key) is False
    journal.resolve(ordinal=1, outcome="REJECTED")
    assert journal.begin_submit(ordinal=2, idempotency_key_hash="sha256:" + "b" * 64)


def test_forced_exit_bypasses_rebalance_minimum_and_cannot_be_rebought() -> None:
    plan = plan_portfolio_orders(
        policy=CapitalPolicy(),
        capital=CapitalSnapshot(1_000_000, 0, 950_000, 5_000),
        positions=(PortfolioPosition("005930", 1, 5_000),),
        candidates=(PortfolioCandidate("005930", 5_000, 10, 0.05),),
        forced_exit_symbols=frozenset({"005930"}),
    )
    assert [(item.phase, item.side, item.quantity) for item in plan.orders] == [("EXIT", "SELL", 1)]


@pytest.mark.parametrize("expected_return", [math.inf, -math.inf, math.nan])
def test_non_finite_forecast_never_authorizes_a_buy(expected_return: float) -> None:
    plan = plan_portfolio_orders(
        policy=CapitalPolicy(),
        capital=CapitalSnapshot(1_000_000, 0, 1_000_000, 0),
        positions=(),
        candidates=(PortfolioCandidate("005930", 50_000, 10, expected_return),),
    )
    assert plan.orders == ()


def test_reinvestment_off_still_debits_realized_losses() -> None:
    plan = plan_portfolio_orders(
        policy=CapitalPolicy(reinvest_realized_pnl=False),
        capital=CapitalSnapshot(1_000_000, -200_000, 2_000_000, 0),
        positions=(),
        candidates=(),
    )
    assert plan.allocation_cap_krw == 800_000


def test_duplicate_candidates_and_boolean_numeric_inputs_are_rejected() -> None:
    with pytest.raises(PortfolioAutomationError, match="distinct"):
        plan_portfolio_orders(
            policy=CapitalPolicy(),
            capital=CapitalSnapshot(1_000_000, 0, 1_000_000, 0),
            positions=(),
            candidates=(
                PortfolioCandidate("005930", 50_000, 10, 0.02),
                PortfolioCandidate("005930", 50_000, 10, 0.01),
            ),
        )
    with pytest.raises(PortfolioAutomationError, match="candidate sizing"):
        PortfolioCandidate("005930", True, 10, 0.02)
