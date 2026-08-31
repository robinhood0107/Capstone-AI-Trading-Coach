"""Content-free KIS_MOCK readiness/start/stop operator CLI."""

from __future__ import annotations

import argparse
import os
from datetime import UTC, date, datetime, time
from typing import Sequence
from zoneinfo import ZoneInfo

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
) -> tuple[ReadinessResult, bool]:
    result = repository.readiness(owner, target_session)
    local = _credential_configured() and _local_certification_valid()
    return result, local and result.all_ready


def _print_readiness(
    result: ReadinessResult,
    *,
    local_ready: bool,
    target_session: date,
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
    print("PROVIDER_CALLS=0")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    repository = _repository()
    owner = _owner()
    planner = XkrxBoundaryPlanner()
    target = _target_session(datetime.now(UTC), planner)
    readiness, ready = _readiness(repository, owner, target)
    if args.command == "readiness":
        _print_readiness(readiness, local_ready=ready, target_session=target)
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
                _print_readiness(readiness, local_ready=False, target_session=target)
                return 1
            if not replayed:
                raise AutomationRuntimeError("AUTOMATION_START_WITHOUT_READINESS")
            print("MOCK_START=NO_OP_ALREADY_ARMED")
            print("MOCK_START_REPLAYED=TRUE")
            print(f"CURRENT_CONTROL_VERSION={version}")
            print("PROVIDER_CALLS=0")
            return 0
        _print_readiness(readiness, local_ready=ready, target_session=target)
        _, version, replayed = repository.start(owner, target, readiness.current_control_version)
        print("MOCK_START=PASS")
        print(f"MOCK_START_REPLAYED={str(replayed).upper()}")
        print(f"CURRENT_CONTROL_VERSION={version}")
        print("PROVIDER_CALLS=0")
        return 0
    version, replayed = repository.stop(owner, readiness.current_control_version)
    print("MOCK_STOP=PASS")
    print(f"MOCK_STOP_REPLAYED={str(replayed).upper()}")
    print(f"CURRENT_CONTROL_VERSION={version}")
    print("OUTSTANDING_RECONCILIATION=PRESERVED")
    print("PROVIDER_CALLS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
