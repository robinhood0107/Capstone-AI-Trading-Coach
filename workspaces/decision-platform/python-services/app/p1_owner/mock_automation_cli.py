"""Content-free KIS_MOCK readiness/start/stop operator CLI."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, date, datetime, time
from typing import Sequence
from zoneinfo import ZoneInfo

from psycopg import Error as PsycopgError
from psycopg.rows import dict_row

from app.p1_owner.automation import AccountLineageSnapshot, ReconcileSnapshot
from app.p1_owner.automation_runtime import (
    AutomationRuntimeError,
    PostgresAutomationRuntimeRepository,
    ReadinessResult,
    XkrxBoundaryPlanner,
)

_KST = ZoneInfo("Asia/Seoul")
_OPEN_BOUNDARY = time(9, 30)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P1 KIS_MOCK automation control")
    parser.add_argument("command", choices=("readiness", "start", "stop"))
    return parser.parse_args(argv)


def _repository() -> PostgresAutomationRuntimeRepository:
    dsn = os.environ.get("P1_AUTOMATION_DATABASE_DSN", "").strip()
    repository = PostgresAutomationRuntimeRepository(dsn)
    repository.preflight()
    return repository


def _owner() -> str:
    value = os.environ.get("P1_AUTOMATION_OWNER_USER_ID", "").strip()
    if not value:
        raise AutomationRuntimeError("AUTOMATION_OWNER_MISSING")
    return value


def _credential_configured() -> bool:
    return os.environ.get("KIS_MOCK_CONFIGURED", "false").lower() == "true"


def _local_certification_valid() -> bool:
    return os.environ.get("P1_LOCAL_CERTIFICATION_VALID", "false").lower() == "true"


def _target_session(now: datetime, planner: XkrxBoundaryPlanner) -> date:
    local = now.astimezone(_KST)
    candidate = planner.current_or_next_session(local)
    if candidate == local.date() and local.timetz().replace(tzinfo=None) >= _OPEN_BOUNDARY:
        return planner.next_session(candidate)
    return candidate


def _readiness(
    repository: PostgresAutomationRuntimeRepository,
    owner: str,
    target_session: date,
) -> tuple[ReadinessResult, bool, int]:
    provider_calls = 0
    if _credential_configured() and _local_certification_valid():
        provider_calls = _auto_reconcile(repository, owner)
    result = repository.readiness(owner, target_session)
    local = _credential_configured() and _local_certification_valid()
    return result, local and result.all_ready, provider_calls


def _auto_reconcile(repository: PostgresAutomationRuntimeRepository, owner: str) -> int:
    with repository._connect(row_factory=dict_row) as connection, connection.cursor() as cursor:
        cursor.execute("select * from p1_read_automation_recovery_candidate_v1(%s)", (owner,))
        candidate = cursor.fetchone()
    if candidate is None:
        print("AUTOMATION_AUTO_RECONCILE=NOT_REQUIRED")
        return 0
    source = None
    try:
        from app.p1_owner.automation_runtime_live import KisAutomationExecutionSource

        source = KisAutomationExecutionSource()
        try:
            execution = source.read(
                str(candidate["order_id"]),
                str(candidate["account_id"]),
                candidate["session_date"],
            )
        except AutomationRuntimeError as error:
            if str(error) != "AUTOMATION_ORDER_REFERENCE_UNAVAILABLE":
                raise
            execution = source.recover_filled_buy(
                str(candidate["symbol"]),
                int(candidate["filled_quantity"]),
                int(candidate["average_fill_price_krw"]),
                candidate["session_date"],
            )
        if not isinstance(execution, ReconcileSnapshot) or not execution.resolved:
            print("AUTOMATION_AUTO_RECONCILE=DEFERRED_EXECUTION_UNRESOLVED")
            return source.physical_call_count
        if (
            execution.cumulative_quantity != int(candidate["filled_quantity"])
            or execution.leaves_quantity != 0
            or execution.average_fill_price_krw != int(candidate["average_fill_price_krw"])
        ):
            print("AUTOMATION_AUTO_RECONCILE=DEFERRED_EXECUTION_MISMATCH")
            return source.physical_call_count
        balance = source.balance(str(candidate["account_id"]))
        AccountLineageSnapshot.from_projection(balance)
        projection = dict(balance)
        projection["schemaVersion"] = "2"
        with repository._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select p1_complete_automation_recovery_v1(%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (
                    owner,
                    candidate["run_id"],
                    candidate["position_id"],
                    candidate["order_id"],
                    execution.cumulative_quantity,
                    execution.average_fill_price_krw,
                    json.dumps(projection, sort_keys=True, separators=(",", ":")),
                ),
            )
            row = cursor.fetchone()
            if row is None or not isinstance(row[0], int):
                raise AutomationRuntimeError("AUTOMATION_RECOVERY_WRITE_FAILED")
        print("AUTOMATION_AUTO_RECONCILE=PASS")
        print(f"AUTOMATION_AUTO_RECONCILE_SEQUENCE={row[0]}")
        print("AUTOMATION_AUTO_RECONCILE_OPERATIONS=EXECUTION_READ,BALANCE_READ")
        return source.physical_call_count
    except (PsycopgError, RuntimeError, OSError, TypeError, ValueError):
        print("AUTOMATION_AUTO_RECONCILE=DEFERRED_PROVIDER_UNAVAILABLE")
        return source.physical_call_count if source is not None else 0
    finally:
        if source is not None:
            source.close()


def _print_readiness(
    result: ReadinessResult,
    *,
    local_ready: bool,
    target_session: date,
    provider_calls: int,
) -> None:
    markers = {
        "ACCOUNT_BASELINE_CAPTURED": result.markers["account_baseline_matches"],
        "CERTIFICATION_VALID": result.markers["certification_valid"]
        and _local_certification_valid(),
        "CLEAN_RELEASE_SOURCE_BINDING": result.markers["release_source_bound"],
        "CREDENTIAL_CONFIGURED": _credential_configured(),
        "CURRENT_CONTROL_CONFIGURED": result.markers["control_configured"],
        "KILL_SWITCH_INACTIVE": result.markers["kill_switch_inactive"],
        "PRINCIPLE_CURRENT": result.markers["principle_current"],
        "REAL_TEAM_B_POINTER": result.markers["real_team_b_ready"],
        "TARGET_SESSION_AVAILABLE": result.markers["target_available"],
        "UNRESOLVED_DRIFT_ORDER_ZERO": result.markers["unresolved_state_clear"],
    }
    for name, passed in markers.items():
        print(f"{name}={'PASS' if passed else 'FAIL'}")
    print(f"CURRENT_CONTROL_VERSION={result.current_control_version}")
    print(f"NEXT_XKRX_SESSION={target_session.isoformat()}")
    print(f"MOCK_READINESS={'PASS' if local_ready else 'FAIL'}")
    print(f"PROVIDER_CALLS={provider_calls}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    repository = _repository()
    owner = _owner()
    planner = XkrxBoundaryPlanner()
    target = _target_session(datetime.now(UTC), planner)
    if args.command == "stop":
        readiness = repository.readiness(owner, target)
        version, replayed = repository.stop(owner, readiness.current_control_version)
        print("MOCK_STOP=PASS")
        print(f"MOCK_STOP_REPLAYED={str(replayed).upper()}")
        print(f"CURRENT_CONTROL_VERSION={version}")
        print("OUTSTANDING_RECONCILIATION=PRESERVED")
        print("PROVIDER_CALLS=0")
        return 0
    readiness, ready, provider_calls = _readiness(repository, owner, target)
    if args.command == "readiness":
        _print_readiness(
            readiness,
            local_ready=ready,
            target_session=target,
            provider_calls=provider_calls,
        )
        return 0 if ready else 1
    if args.command == "start":
        if not ready:
            try:
                _, version, replayed = repository.start(
                    owner,
                    target,
                    readiness.current_control_version,
                )
            except Exception:
                _print_readiness(
                    readiness,
                    local_ready=False,
                    target_session=target,
                    provider_calls=provider_calls,
                )
                return 1
            if not replayed:
                raise AutomationRuntimeError("AUTOMATION_START_WITHOUT_READINESS")
            print("MOCK_START=NO_OP_ALREADY_ARMED")
            print("MOCK_START_REPLAYED=TRUE")
            print(f"CURRENT_CONTROL_VERSION={version}")
            print(f"PROVIDER_CALLS={provider_calls}")
            return 0
        _print_readiness(
            readiness,
            local_ready=ready,
            target_session=target,
            provider_calls=provider_calls,
        )
        _, version, replayed = repository.start(owner, target, readiness.current_control_version)
        print("MOCK_START=PASS")
        print(f"MOCK_START_REPLAYED={str(replayed).upper()}")
        print(f"CURRENT_CONTROL_VERSION={version}")
        print(f"PROVIDER_CALLS={provider_calls}")
        return 0
    raise AssertionError("unreachable automation command")


if __name__ == "__main__":
    raise SystemExit(main())
