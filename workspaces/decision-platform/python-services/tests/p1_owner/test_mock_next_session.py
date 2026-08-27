from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

_SCRIPT = Path(__file__).parents[5] / "scripts/p1_mock_next_session.py"
_SPEC = importlib.util.spec_from_file_location("p1_mock_next_session", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
scheduler = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = scheduler
_SPEC.loader.exec_module(scheduler)

_KST = ZoneInfo("Asia/Seoul")
_HEAD = "a" * 40


class FakeRunner:
    def __init__(
        self,
        *,
        systemd_available: bool = True,
        readiness_pass: bool = True,
        start_pass: bool = True,
    ) -> None:
        self.systemd_available = systemd_available
        self.readiness_pass = readiness_pass
        self.start_pass = start_pass
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        self.calls.append(command)
        if command[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, _HEAD + "\n", "")
        if command[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[0] in {"systemctl", "systemd-run"}:
            code = 0 if self.systemd_available else 1
            return subprocess.CompletedProcess(command, code, "", "unavailable" if code else "")
        if command[-2:] == ["mock", "readiness"]:
            code = 0 if self.readiness_pass else 1
            output = (
                "MOCK_READINESS=PASS\nPROVIDER_CALLS=0\n"
                if code == 0
                else "MOCK_READINESS=FAIL\nPROVIDER_CALLS=0\n"
            )
            return subprocess.CompletedProcess(command, code, output, "")
        if command[-2:] == ["mock", "start"]:
            code = 0 if self.start_pass else 1
            output = (
                "MOCK_START=PASS\nPROVIDER_CALLS=0\n"
                if code == 0
                else "CAPSTONE_ERROR=NOT_IMPLEMENTED\n"
            )
            return subprocess.CompletedProcess(command, code, output, "")
        raise AssertionError(command)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 8, 27, 8, 0, tzinfo=_KST), date(2026, 8, 27)),
        (datetime(2026, 8, 29, 12, 0, tzinfo=_KST), date(2026, 8, 31)),
        (datetime(2026, 8, 14, 9, 0, tzinfo=_KST), date(2026, 8, 18)),
    ],
)
def test_next_session_uses_pinned_xkrx_and_skips_weekend_holiday(
    now: datetime,
    expected: date,
) -> None:
    assert scheduler.next_session(now) == expected


def test_execute_creates_one_transient_systemd_unit_and_duplicate_is_noop(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    receipt = tmp_path / "mock" / "next-session-schedule.json"
    plan = scheduler.SchedulePlan(
        session_date=date(2026, 8, 28),
        run_at=datetime(2026, 8, 28, 8, 55, tzinfo=_KST),
        unit_name="capstone-p1-mock-20260828",
        receipt_path=receipt,
        repository_head=_HEAD,
        script_sha256="b" * 64,
    )

    assert scheduler.schedule(plan, root=tmp_path, runner=runner) is True
    assert scheduler.schedule(plan, root=tmp_path, runner=runner) is False

    systemd_runs = [
        call for call in runner.calls if call[0] == "systemd-run" and "--collect" in call
    ]
    assert len(systemd_runs) == 1
    command = systemd_runs[0]
    assert "--user" in command
    assert "--collect" in command
    assert "--on-calendar=2026-08-28 08:55:00 Asia/Seoul" in command
    assert not any("cron" in value or "nohup" in value for value in command)
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert receipt.read_bytes() == scheduler._canonical(plan.receipt())


def test_systemd_unavailable_fails_closed_without_receipt(tmp_path: Path) -> None:
    runner = FakeRunner(systemd_available=False)
    plan = scheduler.SchedulePlan(
        session_date=date(2026, 8, 28),
        run_at=datetime(2026, 8, 28, 8, 55, tzinfo=_KST),
        unit_name="capstone-p1-mock-20260828",
        receipt_path=tmp_path / "mock" / "next-session-schedule.json",
        repository_head=_HEAD,
        script_sha256="b" * 64,
    )

    with pytest.raises(scheduler.ScheduleError, match="SCHEDULER_UNAVAILABLE"):
        scheduler.schedule(plan, root=tmp_path, runner=runner)
    assert not plan.receipt_path.exists()


def test_scheduled_readiness_failure_never_calls_start(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = FakeRunner(readiness_pass=False)
    receipt = _receipt(tmp_path)

    result = scheduler.run_scheduled(
        "2026-08-28",
        str(receipt),
        now=datetime(2026, 8, 28, 8, 55, tzinfo=_KST),
        root=tmp_path,
        runner=runner,
    )

    assert result == 1
    assert not any(call[-2:] == ["mock", "start"] for call in runner.calls)
    assert "PROVIDER_CALLS=0" in capsys.readouterr().out


def test_scheduled_missing_or_failed_mock_start_still_reports_provider_zero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = FakeRunner(start_pass=False)
    receipt = _receipt(tmp_path)

    result = scheduler.run_scheduled(
        "2026-08-28",
        str(receipt),
        now=datetime(2026, 8, 28, 8, 55, tzinfo=_KST),
        root=tmp_path,
        runner=runner,
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "MOCK_START=FAIL" in output
    assert "PROVIDER_CALLS=0" in output


def test_timezone_drift_is_rejected_before_readiness(tmp_path: Path) -> None:
    runner = FakeRunner()
    receipt = _receipt(tmp_path)

    with pytest.raises(scheduler.ScheduleError, match="SCHEDULE_TIMEZONE_DRIFT"):
        scheduler.run_scheduled(
            "2026-08-28",
            str(receipt),
            now=datetime(2026, 8, 28, 8, 55, tzinfo=timezone.utc),
            root=tmp_path,
            runner=runner,
        )
    assert not any(call[-2:] == ["mock", "readiness"] for call in runner.calls)


def test_schedule_code_contains_no_network_account_order_or_infinite_sleep() -> None:
    source = Path(scheduler.__file__).read_text(encoding="utf-8")
    for forbidden in ("requests.", "httpx.", "urllib.request", "while True", "nohup", "crontab"):
        assert forbidden not in source
    assert '[str(selected_root / "capstone"), "mock", "readiness"]' in source
    assert '[str(selected_root / "capstone"), "mock", "start"]' in source


def _receipt(tmp_path: Path) -> Path:
    receipt = tmp_path / "receipt.json"
    payload = {
        "contractId": scheduler._CONTRACT_ID,
        "onCalendar": "2026-08-28T08:55:00+09:00",
        "providerCalls": 0,
        "repositoryHead": _HEAD,
        "scriptSha256": __import__("hashlib")
        .sha256(Path(scheduler.__file__).read_bytes())
        .hexdigest(),
        "sessionDate": "2026-08-28",
        "unitName": "capstone-p1-mock-20260828",
    }
    receipt.write_bytes(scheduler._canonical(payload))
    os.chmod(receipt, 0o600)
    return receipt
