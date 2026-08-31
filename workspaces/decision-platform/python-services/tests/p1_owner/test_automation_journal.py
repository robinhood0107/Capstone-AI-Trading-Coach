"""운영자가 알아야 할 순간만 로컬로 나가고, 저널 실패가 운용을 멈추지 않는다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.p1_owner.automation_journal import AutomationJournal, notice_from_event


def _notice(event_type: str, reason: str | None = None) -> Any:
    payload: dict[str, object] = {}
    if reason is not None:
        payload["reason"] = reason
    return notice_from_event(
        {"eventType": event_type, "payload": payload},
        run_id="auto_run_" + "a" * 32,
        session_date="2026-08-28",
        state="HALTED",
    )


def test_halt_and_drift_reach_the_journal_file(tmp_path: Path) -> None:
    path = tmp_path / "automation.jsonl"
    journal = AutomationJournal(path)

    journal.notify(_notice("RUN_HALTED"))
    journal.notify(_notice("DRIFT_DETECTED", reason="ACCOUNT_DRIFT"))

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert '"eventType":"RUN_HALTED"' in lines[0]
    assert '"reason":"ACCOUNT_DRIFT"' in lines[1]


def test_routine_events_are_not_worth_waking_anyone_for(tmp_path: Path) -> None:
    path = tmp_path / "automation.jsonl"
    journal = AutomationJournal(path)

    journal.notify(_notice("RUN_TRANSITIONED"))
    journal.notify(_notice("ORDER_RESERVED"))

    assert not path.exists()


def test_a_journal_that_cannot_be_written_does_not_stop_the_run(tmp_path: Path) -> None:
    # 저널은 관측 편의지 안전장치가 아니다. 디렉터리가 없어도 예외를 올리지 않는다.
    journal = AutomationJournal(tmp_path / "missing" / "automation.jsonl")

    journal.notify(_notice("RUN_HALTED"))


def test_no_file_configured_still_emits_to_stderr(capsys: Any) -> None:
    AutomationJournal(None).notify(_notice("RUN_HALTED"))

    assert '"eventType":"RUN_HALTED"' in capsys.readouterr().err


def test_the_line_carries_no_account_or_credential_field() -> None:
    line = _notice("DRIFT_DETECTED", reason="KILL_SWITCH").line()

    assert set(__import__("json").loads(line)) == {
        "eventType",
        "occurredAt",
        "reason",
        "runId",
        "sessionDate",
        "state",
    }
