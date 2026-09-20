"""다중 주문 plan과 주문별 전이를 decision_automation_runtime definer 함수로만 저장한다."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from app.p1_owner.automation_portfolio import PortfolioPlan
from app.p1_owner.automation_runtime import RuntimeClaim


class PortfolioRepositoryError(RuntimeError):
    """다중 주문 원장의 owner/claim/CAS/멱등 경계가 맞지 않는다."""


@dataclass(frozen=True, slots=True)
class PortfolioSessionBinding:
    automation_policy_id: str
    automation_policy_version: int
    capital_policy_version: int
    principle_version_id: str
    principle_version: int
    configured_capital_krw: int
    realized_pnl_since_transition_krw: int
    broker_buyable_cash_krw: int
    bot_position_market_value_krw: int
    reserved_buy_cash_krw: int
    balance_observation_id: str
    buyable_receipt_id: str


class PostgresAutomationPortfolioRepository:
    """base table 권한 없이 V163의 receipt-bound definer 함수만 호출한다."""

    def __init__(self, database_dsn: str) -> None:
        try:
            parsed = conninfo_to_dict(database_dsn)
        except psycopg.Error as error:
            raise PortfolioRepositoryError("AUTOMATION_PORTFOLIO_DSN_INVALID") from error
        if parsed.get("user") != "decision_automation_runtime" or parsed.get("host") not in {
            "postgres",
            "127.0.0.1",
            "localhost",
        }:
            raise PortfolioRepositoryError("AUTOMATION_PORTFOLIO_DSN_ROLE_INVALID")
        self._dsn = database_dsn

    def stage(
        self,
        *,
        claim: RuntimeClaim,
        binding: PortfolioSessionBinding,
        plan: PortfolioPlan,
    ) -> str:
        """계산된 자본과 exact intent들을 같은 session snapshot 아래 원자 저장한다."""

        snapshot = {
            "allocationCapKrw": plan.allocation_cap_krw,
            "automationPolicyId": binding.automation_policy_id,
            "automationPolicyVersion": binding.automation_policy_version,
            "balanceObservationId": binding.balance_observation_id,
            "botPositionMarketValueKrw": binding.bot_position_market_value_krw,
            "brokerBuyableCashKrw": binding.broker_buyable_cash_krw,
            "buyableReceiptId": binding.buyable_receipt_id,
            "capitalPolicyVersion": binding.capital_policy_version,
            "configuredCapitalKrw": binding.configured_capital_krw,
            "investableCapKrw": plan.investable_cap_krw,
            "principleVersion": binding.principle_version,
            "principleVersionId": binding.principle_version_id,
            "realizedPnlSinceTransitionKrw": binding.realized_pnl_since_transition_krw,
            "reservedBuyCashKrw": binding.reserved_buy_cash_krw,
            "targetPerPositionKrw": plan.target_per_position_krw,
            "unusedCashReason": plan.unused_cash_reason,
        }
        orders = []
        for order in plan.orders:
            intent = order.intent(claim.strategy_id)
            intent_sha256 = order.intent_sha256(claim.strategy_id)
            seed = f"{claim.run_id}|{order.ordinal}|{intent_sha256}"
            orders.append(
                {
                    "currentQuantity": order.current_quantity,
                    "exactIntent": intent,
                    "exactIntentSha256": intent_sha256,
                    "executionId": "auto_exec_" + hashlib.sha256(seed.encode()).hexdigest()[:32],
                    "idempotencyKeyHash": "sha256:"
                    + hashlib.sha256((seed + "|submit").encode()).hexdigest(),
                    "limitPriceKrw": order.limit_price_krw,
                    "ordinal": order.ordinal,
                    "phase": order.phase,
                    "quantity": order.quantity,
                    "side": order.side,
                    "symbol": order.symbol,
                    "targetQuantity": order.target_quantity,
                }
            )
        with psycopg.connect(self._dsn) as connection:
            hash_row = connection.execute(
                "SELECT encode(digest(convert_to((%s::jsonb)::text,'UTF8'),'sha256'),'hex')",
                (Jsonb(snapshot),),
            ).fetchone()
            if hash_row is None or not isinstance(hash_row[0], str):
                raise PortfolioRepositoryError("AUTOMATION_PORTFOLIO_SNAPSHOT_HASH_FAILED")
            snapshot["snapshotSha256"] = hash_row[0]
            row = connection.execute(
                "SELECT public.p1_stage_automation_portfolio_plan_v2(%s,%s,%s,%s)",
                (claim.run_id, claim.claim_token_hash, Jsonb(snapshot), Jsonb(orders)),
            ).fetchone()
        result = str(row[0]) if row else ""
        if result not in {"INSERTED", "NO_OP"}:
            raise PortfolioRepositoryError("AUTOMATION_PORTFOLIO_STAGE_FAILED")
        return result

    def begin(self, *, claim: RuntimeClaim, ordinal: int, idempotency_key_hash: str) -> bool:
        """현재 ordinal만 열며 동일 키 replay는 False로 반환한다."""

        with psycopg.connect(self._dsn) as connection:
            row = connection.execute(
                "SELECT public.p1_begin_automation_portfolio_execution_v2(%s,%s,%s,%s)",
                (claim.run_id, claim.claim_token_hash, ordinal, idempotency_key_hash),
            ).fetchone()
        result = str(row[0]) if row else ""
        if result not in {"SUBMIT", "NO_OP"}:
            raise PortfolioRepositoryError("AUTOMATION_PORTFOLIO_BEGIN_FAILED")
        return result == "SUBMIT"

    def finish(
        self,
        *,
        claim: RuntimeClaim,
        ordinal: int,
        state: Literal["PENDING_RECONCILIATION", "FILLED", "CANCELLED", "REJECTED"],
        order_id: str | None = None,
        provider_order_ref_hash: str | None = None,
        filled_quantity: int = 0,
        leaves_quantity: int = 0,
        average_fill_price_krw: int | None = None,
        provider_exec_ref_hash: str | None = None,
    ) -> bool:
        """provider outcome을 저장하되 미확정 상태는 다음 ordinal을 계속 막는다."""

        with psycopg.connect(self._dsn) as connection:
            row = connection.execute(
                "SELECT public.p1_finish_automation_portfolio_execution_v2(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    claim.run_id,
                    claim.claim_token_hash,
                    ordinal,
                    state,
                    order_id,
                    provider_order_ref_hash,
                    filled_quantity,
                    leaves_quantity,
                    average_fill_price_krw,
                    provider_exec_ref_hash,
                ),
            ).fetchone()
        result = str(row[0]) if row else ""
        if result not in {"UPDATED", "NO_OP"}:
            raise PortfolioRepositoryError("AUTOMATION_PORTFOLIO_FINISH_FAILED")
        return result == "UPDATED"

    def record_buyable(self, *, claim: RuntimeClaim, projection: dict[str, object]) -> str:
        """Spring/KIS buyable projection을 owner/account/run에 묶고 raw provider payload는 저장하지 않는다."""

        with psycopg.connect(self._dsn) as connection:
            row = connection.execute(
                "SELECT public.p1_record_automation_buyable_receipt_v1(%s,%s,%s)",
                (claim.run_id, claim.claim_token_hash, Jsonb(projection)),
            ).fetchone()
        receipt_id = str(row[0]) if row else ""
        if not receipt_id.startswith("auto_buyable_"):
            raise PortfolioRepositoryError("AUTOMATION_BUYABLE_RECEIPT_FAILED")
        return receipt_id

    def record_book(self, *, claim: RuntimeClaim, ordinal: int, book: dict[str, object]) -> int:
        """제출 순간의 최우선 호가를 실행 원장에 남긴다.

        체결 품질을 바꾸려면 근거가 있어야 하는데 지금은 아무 기록도 없다. 이것이 그 원장이다.
        **진단 기록이므로 실패해도 세션을 멈추지 않는다** - `record_stage_outcomes` 와 같은
        성질이다. 원장이 거래를 막을 수 없어야 한다.
        """

        try:
            with psycopg.connect(self._dsn) as connection:
                row = connection.execute(
                    "SELECT public.p1_record_automation_portfolio_book_v1(%s,%s,%s,%s::jsonb)",
                    (claim.run_id, claim.claim_token_hash, ordinal, json.dumps(book)),
                ).fetchone()
            return 1 if row and str(row[0]) == "UPDATED" else 0
        except Exception as error:
            print(
                f"AUTOMATION_PORTFOLIO_BOOK=FAILED error={type(error).__name__}",
                flush=True,
            )
            return 0

    def record_stage_outcomes(self, claim: RuntimeClaim, symbol: str, reason: str) -> int:
        """후보가 탈락한 사유를 퍼널 표에 남긴다.

        단일 주문 엔진 경로에는 이 기록이 붙어 있는데 포트폴리오 경로만 비어 있었다.
        그래서 2026-09-15 에 09:45/11:00 두 결정 시점이 모두 무주문으로 끝났는데 사유가
        로그에만 있고 화면에는 "주문 없이 종료"만 남았다.

        `stage` 는 V166 의 CHECK 목록에 있는 값이어야 한다 - 새 이름을 만들면 주문 경로
        한가운데서 CHECK 위반으로 터진다. 단일 엔진과 같은 `RISK_ENGINE` 을 쓴다.
        진단 기록이라 실패해도 예외를 올리지 않고 0 을 돌려준다.
        """

        payload = [
            {
                "stage": "RISK_ENGINE",
                "symbol": symbol,
                "outcome": "DROPPED",
                "reasonCode": reason,
            }
        ]
        with psycopg.connect(self._dsn) as connection:
            row = connection.execute(
                "SELECT public.p1_record_automation_stage_outcomes_v1(%s,%s,%s::jsonb)",
                (claim.run_id, claim.claim_token_hash, json.dumps(payload)),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def load_sources(self, *, claim: RuntimeClaim) -> dict[str, object]:
        """DB가 owner/account/source receipt를 대조해 만든 bounded planning input만 반환한다."""

        with psycopg.connect(self._dsn) as connection:
            row = connection.execute(
                "SELECT public.p1_read_automation_portfolio_sources_v1(%s,%s)",
                (claim.run_id, claim.claim_token_hash),
            ).fetchone()
        try:
            value = json.loads(str(row[0])) if row else None
        except json.JSONDecodeError as error:
            raise PortfolioRepositoryError("AUTOMATION_PORTFOLIO_SOURCES_INVALID") from error
        if not isinstance(value, dict):
            raise PortfolioRepositoryError("AUTOMATION_PORTFOLIO_SOURCES_INVALID")
        return value

    def current_execution(self, *, claim: RuntimeClaim) -> dict[str, object] | None:
        """재기동 시 SUBMITTING을 재전송하지 않고 PENDING order만 대사하도록 현재 ordinal을 읽는다."""

        with psycopg.connect(self._dsn) as connection:
            row = connection.execute(
                "SELECT public.p1_read_automation_portfolio_execution_v1(%s,%s)",
                (claim.run_id, claim.claim_token_hash),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        try:
            value = json.loads(str(row[0]))
        except json.JSONDecodeError as error:
            raise PortfolioRepositoryError("AUTOMATION_PORTFOLIO_EXECUTION_INVALID") from error
        if not isinstance(value, dict):
            raise PortfolioRepositoryError("AUTOMATION_PORTFOLIO_EXECUTION_INVALID")
        return value
