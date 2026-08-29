"""컨테이너 안에서 도는 관통 테스트의 손. 호스트 orchestrator가 단계별로 호출한다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다.

왜 컨테이너 안이어야 하나. `AutomationRuntimeBridgeController`가 loopback이 아닌 `remoteAddr`를
거부하고(`:116`) 클라이언트가 `http://127.0.0.1:8080`을 하드코딩한다. 또 bridge 공유 비밀과
automation DSN이 secret 파일에만 있다. `decision-platform`만 bridge와 postgres에 동시에 닿는다.

가짜는 KIS 두 port뿐이다. bridge·RiskEngine·Brokerage·DB는 전부 실제 경로를 탄다.

실행:
  P1_FULL_PIPELINE_E2E=1 python -m e2e.container_driver import-bundle --session 2026-09-01 \
    --buy 005930 --ordinal 1
  P1_FULL_PIPELINE_E2E=1 python -m e2e.container_driver drive --session 2026-09-01
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, time
from typing import Any, Final

import psycopg

from app.p1_owner.automation_runtime import (
    PersistentAutomationRunner,
    PostgresAutomationRuntimeRepository,
    XkrxBoundaryPlanner,
    _claim_hash,
)
from app.p1_owner.automation_runtime_live import (
    _KST,
    FailClosedVertexVetoTransport,
    LiveAutomationPort,
    SpringAutomationBridgeClient,
)
from . import team_b_bundle
from .kis_fakes import AccountLedger, LedgerExecutionSource, LedgerQuoteSource

_OPT_IN: Final = "P1_FULL_PIPELINE_E2E"
_FIRST_TICK: Final = time(9, 31)
_MAX_TICKS: Final = 24
_TERMINAL: Final = frozenset(
    {
        "COMPLETED",
        "CANCELLED_UNFILLED",
        "HALTED",
        "NEWS_VETOED",
        "SKIPPED_HOLIDAY",
        "SKIPPED_LATE_START",
        "SKIPPED_NO_ACTION",
        "SKIPPED_DATA_UNAVAILABLE",
        "SKIPPED_KILL_SWITCH",
        "SKIPPED_DISARMED",
        "SKIPPED_STALE_INPUT",
    }
)

# 호가 격자 위의 값이다. 70,000은 tick 100의 배수이고 매수 지정가는 70,100이 된다.
FIXTURE_PRICES: Final[dict[str, int]] = {
    team_b_bundle.TRADED_SYMBOL: 70_000,
    team_b_bundle.SECONDARY_SYMBOL: 180_000,
}
INITIAL_CASH_KRW: Final = 100_000_000


class DriverError(RuntimeError):
    """드라이버가 계약을 만족하지 못했다."""


def _require_opt_in() -> None:
    if os.environ.get(_OPT_IN) != "1":
        raise DriverError(f"{_OPT_IN}=1 must be set explicitly")


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise DriverError(f"{name} is unavailable inside the container")
    return value


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write("P1_E2E_RECEIPT " + json.dumps(payload, sort_keys=True) + "\n")
    sys.stdout.flush()


def _import_bundle(args: argparse.Namespace) -> int:
    packet, packet_sha256 = team_b_bundle.build_packet(
        session_date=date.fromisoformat(args.session),
        buy_symbols=frozenset(args.buy or ()),
        sell_symbols=frozenset(args.sell or ()),
        ordinal=args.ordinal,
    )
    imported = team_b_bundle.import_packet(
        database_dsn=_env("ASYNC_WORKER_DATABASE_DSN"),
        packet=packet,
        packet_sha256=packet_sha256,
    )
    _emit(
        {
            "phase": "import-bundle",
            "outcome": imported.outcome,
            "bundleSha256": imported.bundle_sha256,
            "artifactId": imported.artifact_id,
            "runId": imported.run_id,
            "packetSha256": packet_sha256,
            "sessionDate": args.session,
        }
    )
    return 0


def _probe_bridge(args: argparse.Namespace) -> int:
    """구동 전에 bridge의 brokerage 다리를 한 번 두드려 본다.

    이 다리가 막혀 있으면 run은 항상 `AUTOMATION_BRIDGE_FAILED`로 죽는데, 그 예외에는 이유가
    없다. 여기서 먼저 확인해 판정표에 원인을 남긴다.
    """

    secret = _env("AUTOMATION_RUNTIME_SHARED_SECRET")
    bridge = SpringAutomationBridgeClient(secret)
    payload = {
        "accountId": args.account,
        "estimatedPrice": args.price,
        "symbol": args.symbol,
    }
    try:
        data = bridge.command("BUYABLE", _env("P1_AUTOMATION_OWNER_USER_ID"), payload)
    except Exception as error:  # noqa: BLE001 - 원인 분류가 이 단계의 목적이다
        _emit({"phase": "probe-bridge", "reachable": False, "error": str(error)})
        return 1
    _emit({"phase": "probe-bridge", "reachable": True, "buyable": data})
    return 0


def _drive(args: argparse.Namespace) -> int:
    session = date.fromisoformat(args.session)
    dsn = _env("P1_AUTOMATION_DATABASE_DSN")
    secret = _env("AUTOMATION_RUNTIME_SHARED_SECRET")
    repository = PostgresAutomationRuntimeRepository(dsn)
    runner = PersistentAutomationRunner(repository)
    planner = XkrxBoundaryPlanner()

    claim = repository.claim(session, _claim_hash(secret.encode(), session))
    if claim is None:
        _emit({"phase": "drive", "sessionDate": args.session, "claimed": False})
        raise DriverError("no runtime claim is available for this session")

    ledger = AccountLedger(
        account_id=claim.account_id,
        cash_krw=args.cash,
        positions=dict(json.loads(args.positions)) if args.positions else {},
        market_prices=dict(FIXTURE_PRICES),
    )
    quote_source = LedgerQuoteSource(FIXTURE_PRICES)
    execution_source = LedgerExecutionSource(
        ledger, connect=lambda: psycopg.connect(dsn, connect_timeout=5)
    )
    state = repository.read_state(claim)
    port = LiveAutomationPort(
        claim,
        state,
        SpringAutomationBridgeClient(secret),
        quote_source,
        execution_source,
        FailClosedVertexVetoTransport(),
    )

    clock = datetime.combine(session, _FIRST_TICK, _KST)
    transitions: list[str] = [str(state["state"])]
    index = int(state["checkpointVersion"])
    try:
        for _ in range(_MAX_TICKS):
            state = repository.read_state(claim)
            current = str(state["state"])
            if current in _TERMINAL:
                break
            wakeup = planner.next_wakeup(clock, current)
            clock = max(clock, wakeup)
            index += 1
            result = runner.run_tick(
                claim=claim,
                tick_id=f"{claim.run_id}:boundary:{index}",
                now=clock,
                port=port,
            )
            transitions.append(str(result["state"]))
            if str(result["state"]) in _TERMINAL:
                break
        else:
            raise DriverError("the run did not reach a terminal state within the tick budget")
    finally:
        port.close()

    final_state = transitions[-1]
    if final_state != "HALTED" and args.roll:
        try:
            repository.roll_schedule(
                claim.user_id,
                claim.session_date,
                planner.next_session(claim.session_date),
                claim.control_version,
            )
        except Exception as error:  # noqa: BLE001
            _emit({"phase": "drive", "rollError": type(error).__name__})

    _emit(
        {
            "phase": "drive",
            "sessionDate": args.session,
            "claimed": True,
            "runId": claim.run_id,
            "finalState": final_state,
            "transitions": transitions,
            "orderId": port.order_id,
            "decisionId": port.decision_id,
            "physicalCalls": port.physical_calls,
            "submitCalls": port.submit_calls,
            "quoteCalls": quote_source.calls,
            "ledgerCashKrw": ledger.cash_krw,
            "ledgerPositions": dict(sorted(ledger.positions.items())),
            "filledOrders": [
                {
                    "orderId": order.order_id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "quantity": order.quantity,
                    "priceKrw": order.price_krw,
                }
                for order in execution_source.filled_orders
            ],
        }
    )
    return 0 if final_state == "COMPLETED" else 1


def main(argv: list[str]) -> int:
    _require_opt_in()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    bundle = sub.add_parser("import-bundle")
    bundle.add_argument("--session", required=True)
    bundle.add_argument("--buy", action="append")
    bundle.add_argument("--sell", action="append")
    bundle.add_argument("--ordinal", type=int, required=True)
    bundle.set_defaults(handler=_import_bundle)

    probe = sub.add_parser("probe-bridge")
    probe.add_argument("--account", required=True)
    probe.add_argument("--symbol", default=team_b_bundle.TRADED_SYMBOL)
    probe.add_argument("--price", type=int, default=70_100)
    probe.set_defaults(handler=_probe_bridge)

    drive = sub.add_parser("drive")
    drive.add_argument("--session", required=True)
    drive.add_argument("--cash", type=int, default=INITIAL_CASH_KRW)
    drive.add_argument("--positions", default="")
    drive.add_argument("--roll", action="store_true")
    drive.set_defaults(handler=_drive)

    args = parser.parse_args(argv[1:])
    try:
        return int(args.handler(args))
    except DriverError as error:
        _emit({"phase": args.command, "error": str(error)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
