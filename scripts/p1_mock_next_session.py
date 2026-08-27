#!/usr/bin/env python3
"""다음 XKRX session 08:55 KST에 mock readiness/start를 한 번만 예약한다."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Callable, Sequence, cast
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

_KST = ZoneInfo("Asia/Seoul")
_RUN_TIME = time(8, 55)
_WINDOW_START = time(8, 54, 30)
_WINDOW_END = time(8, 56, 30)
_CONTRACT_ID = "p1-mock-next-session-schedule.v1"


class ScheduleError(RuntimeError):
    """Transient schedule이 안전하게 생성 또는 실행될 수 없다."""


@dataclass(frozen=True, slots=True)
class SchedulePlan:
    session_date: date
    run_at: datetime
    unit_name: str
    receipt_path: Path
    repository_head: str
    script_sha256: str

    def receipt(self) -> dict[str, object]:
        return {
            "contractId": _CONTRACT_ID,
            "onCalendar": self.run_at.isoformat(),
            "providerCalls": 0,
            "repositoryHead": self.repository_head,
            "scriptSha256": self.script_sha256,
            "sessionDate": self.session_date.isoformat(),
            "unitName": self.unit_name,
        }


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Schedule the next P1 KIS_MOCK readiness/start")
    parser.add_argument("--execute", action="store_true", help="create the transient user timer")
    parser.add_argument("--run-scheduled", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--session", help=argparse.SUPPRESS)
    parser.add_argument("--receipt", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def repository_root() -> Path:
    root = Path(__file__).resolve().parents[1]
    if not (root / "capstone").is_file() or not (root / ".git").exists():
        raise ScheduleError("REPOSITORY_ROOT_INVALID")
    return root


def next_session(now: datetime) -> date:
    if version("exchange-calendars") != "4.13.2":
        raise ScheduleError("XKRX_CALENDAR_VERSION_DRIFT")
    local = _kst(now)
    calendar = xcals.get_calendar("XKRX")
    stamp = pd.Timestamp(local.date())
    if calendar.is_session(stamp) and local.timetz().replace(tzinfo=None) < _RUN_TIME:
        return local.date()
    if calendar.is_session(stamp):
        return cast(date, calendar.next_session(stamp).date())
    return cast(date, calendar.date_to_session(stamp, direction="next").date())


def build_plan(
    now: datetime,
    *,
    root: Path | None = None,
    runner: Runner = subprocess.run,
) -> SchedulePlan:
    selected_root = (root or repository_root()).resolve()
    session_date = next_session(now)
    state_root = Path(os.environ.get("P1_STATE_DIR", selected_root / "deploy/p1/.state-app"))
    if not state_root.is_absolute():
        state_root = (selected_root / state_root).resolve()
    receipt_path = state_root / "mock" / "next-session-schedule.json"
    script = Path(__file__).resolve()
    return SchedulePlan(
        session_date=session_date,
        run_at=datetime.combine(session_date, _RUN_TIME, _KST),
        unit_name=f"capstone-p1-mock-{session_date:%Y%m%d}",
        receipt_path=receipt_path,
        repository_head=_git_head(selected_root, runner),
        script_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
    )


def schedule(
    plan: SchedulePlan,
    *,
    root: Path,
    runner: Runner = subprocess.run,
) -> bool:
    plan.receipt_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require_private_directory(plan.receipt_path.parent)
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = os.open(plan.receipt_path, flags, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise ScheduleError("SCHEDULE_RECEIPT_UNSAFE")
        existing = os.read(descriptor, 16 * 1024)
        if existing:
            if existing != _canonical(plan.receipt()):
                raise ScheduleError("SCHEDULE_RECEIPT_CONFLICT")
            return False
        try:
            _require_user_systemd(runner)
        except ScheduleError:
            os.close(descriptor)
            descriptor = -1
            plan.receipt_path.unlink(missing_ok=True)
            raise
        command = [
            "systemd-run",
            "--user",
            "--collect",
            f"--unit={plan.unit_name}",
            f"--on-calendar={plan.session_date.isoformat()} 08:55:00 Asia/Seoul",
            "--timer-property=AccuracySec=1s",
            "--property=Type=oneshot",
            str(Path(sys.executable).resolve()),
            str(Path(__file__).resolve()),
            "--run-scheduled",
            "--session",
            plan.session_date.isoformat(),
            "--receipt",
            str(plan.receipt_path),
        ]
        result = runner(command, cwd=root, text=True, capture_output=True, check=False, timeout=15)
        if result.returncode != 0:
            os.close(descriptor)
            descriptor = -1
            plan.receipt_path.unlink(missing_ok=True)
            raise ScheduleError("SCHEDULER_UNAVAILABLE")
        content = _canonical(plan.receipt())
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, content)
        os.fsync(descriptor)
        return True
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def run_scheduled(
    session_text: str,
    receipt_text: str,
    *,
    now: datetime | None = None,
    root: Path | None = None,
    runner: Runner = subprocess.run,
) -> int:
    selected_root = (root or repository_root()).resolve()
    observed_clock = now or datetime.now().astimezone()
    _require_unambiguous_system_clock(observed_clock)
    current = _kst(observed_clock)
    try:
        session_date = date.fromisoformat(session_text)
    except ValueError as error:
        raise ScheduleError("SCHEDULE_SESSION_INVALID") from error
    if current.date() != session_date or not _WINDOW_START <= current.time().replace(tzinfo=None) <= _WINDOW_END:
        raise ScheduleError("SCHEDULE_CLOCK_WINDOW_REJECTED")
    receipt_path = Path(receipt_text)
    if not receipt_path.is_absolute():
        raise ScheduleError("SCHEDULE_RECEIPT_PATH_INVALID")
    receipt = _read_receipt(receipt_path)
    if receipt.get("sessionDate") != session_text or receipt.get("contractId") != _CONTRACT_ID:
        raise ScheduleError("SCHEDULE_RECEIPT_IDENTITY_MISMATCH")
    if receipt.get("repositoryHead") != _git_head(selected_root, runner):
        raise ScheduleError("SCHEDULE_SOURCE_DRIFT")
    if receipt.get("scriptSha256") != hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest():
        raise ScheduleError("SCHEDULE_SCRIPT_DRIFT")
    if _git_dirty(selected_root, runner):
        raise ScheduleError("SCHEDULE_DIRTY_WORKTREE")
    readiness = runner(
        [str(selected_root / "capstone"), "mock", "readiness"],
        cwd=selected_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if readiness.returncode != 0 or "MOCK_READINESS=PASS" not in readiness.stdout.splitlines():
        print("NEXT_SESSION_READINESS=FAIL")
        print("MOCK_START=NOT_RUN")
        print("PROVIDER_CALLS=0")
        return 1
    started = runner(
        [str(selected_root / "capstone"), "mock", "start"],
        cwd=selected_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    if started.returncode != 0 or "MOCK_START=PASS" not in started.stdout.splitlines():
        print("NEXT_SESSION_READINESS=PASS")
        print("MOCK_START=FAIL")
        print("PROVIDER_CALLS=0")
        return 1
    print("NEXT_SESSION_READINESS=PASS")
    print("MOCK_START=PASS")
    print("PROVIDER_CALLS=0")
    return 0


def _git_head(root: Path, runner: Runner) -> str:
    result = runner(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    value = result.stdout.strip()
    if result.returncode != 0 or not re_fullmatch_sha(value):
        raise ScheduleError("SCHEDULE_GIT_HEAD_UNAVAILABLE")
    return value


def _git_dirty(root: Path, runner: Runner) -> bool:
    result = runner(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise ScheduleError("SCHEDULE_GIT_STATUS_UNAVAILABLE")
    return bool(result.stdout)


def _require_user_systemd(runner: Runner) -> None:
    for command in (["systemctl", "--user", "show-environment"], ["systemd-run", "--user", "--version"]):
        result = runner(command, text=True, capture_output=True, check=False, timeout=5)
        if result.returncode != 0:
            raise ScheduleError("SCHEDULER_UNAVAILABLE")


def _require_private_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
        raise ScheduleError("SCHEDULE_DIRECTORY_UNSAFE")


def _read_receipt(path: Path) -> dict[str, object]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
        raise ScheduleError("SCHEDULE_RECEIPT_UNSAFE")
    content = path.read_bytes()
    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        raise ScheduleError("SCHEDULE_RECEIPT_INVALID") from error
    if not isinstance(value, dict) or content != _canonical(value):
        raise ScheduleError("SCHEDULE_RECEIPT_INVALID")
    return cast(dict[str, object], value)


def _require_unambiguous_system_clock(current: datetime) -> None:
    if current.tzinfo is None or current.utcoffset() != timedelta(hours=9) or current.fold != 0:
        raise ScheduleError("SCHEDULE_TIMEZONE_DRIFT")


def _kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ScheduleError("SCHEDULE_CLOCK_NAIVE")
    return value.astimezone(_KST)


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def re_fullmatch_sha(value: str) -> bool:
    return len(value) == 40 and all(character in "0123456789abcdef" for character in value)


def main(argv: Sequence[str] | None = None) -> int:
    args = _args(argv)
    try:
        if args.run_scheduled:
            if args.execute or not args.session or not args.receipt:
                raise ScheduleError("SCHEDULE_INTERNAL_ARGUMENTS_INVALID")
            return run_scheduled(args.session, args.receipt)
        if args.session or args.receipt:
            raise ScheduleError("SCHEDULE_ARGUMENTS_INVALID")
        root = repository_root()
        plan = build_plan(datetime.now(UTC), root=root)
        if not args.execute:
            print(_canonical(plan.receipt()).decode())
            print("NEXT_SESSION_SCHEDULE=DRY_RUN")
            print("PROVIDER_CALLS=0")
            return 0
        created = schedule(plan, root=root)
        print(f"NEXT_SESSION_SCHEDULE={'CREATED' if created else 'NO_OP'}")
        print(f"NEXT_XKRX_SESSION={plan.session_date.isoformat()}")
        print("PROVIDER_CALLS=0")
        return 0
    except ScheduleError as error:
        print(f"NEXT_SESSION_SCHEDULE=FAIL:{error}")
        print("PROVIDER_CALLS=0")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
