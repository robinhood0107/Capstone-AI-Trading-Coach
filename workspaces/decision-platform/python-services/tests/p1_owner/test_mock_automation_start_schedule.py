"""무장돼 있는데 그 세션의 예약이 없으면 성공이라고 말하지 않는다.

왜 있나
-------
2026-09-21 시연 당일 아침, 스택은 `ARMED` 였고 `CAPSTONE_AUTOMATION_RECONCILED=ARMED`
까지 찍혀 있었다. 그런데 예약 연쇄가 09-16 에서 끊겨 **그날 돌 예약이 아예 없었다.**
아무것도 매매하지 않는 상태를, 모든 층이 정상이라고 읽고 있었다.

원인은 `start` 의 한 갈래다. 이미 무장돼 있으면 `p1_start_automation_runtime_v1` 이
replayed 를 돌려주고, CLI 는 그것만 보고 `NO_OP_ALREADY_ARMED` 로 0 을 반환했다.
그 세션에 예약이 섰는지는 보지 않았다.

이 테스트는 그 갈래를 고정한다. 무장 상태에서 replayed 가 참이어도 대상 세션의 예약이
없으면 실패여야 한다 - 그래야 위층이 BLOCKED 를 읽는다.
"""

from __future__ import annotations

import pytest

from app.p1_owner import mock_automation_cli as cli
from app.p1_owner.automation_runtime import ReadinessResult

_ALL_MARKERS = (
    "control_configured",
    "certification_valid",
    "release_source_bound",
    "real_team_b_ready",
    "principle_current",
    "kill_switch_inactive",
    "account_baseline_matches",
    "unresolved_state_clear",
    "target_available",
)


class _Repository:
    """start 가 보는 것만 흉내낸다. 무장 상태의 재생(replay)을 그대로 돌려준다."""

    def __init__(self, *, target_available: bool, replayed: bool) -> None:
        self._markers = {name: True for name in _ALL_MARKERS}
        self._markers["target_available"] = target_available
        self._replayed = replayed
        self.started = False

    def readiness(self, user_id: str, target_session: object) -> ReadinessResult:
        return ReadinessResult(
            markers=dict(self._markers),
            current_control_version=16,
            all_ready=all(self._markers.values()),
        )

    def start(self, user_id: str, target_session: object, expected_version: int):
        self.started = True
        return ("auto_sched_test", expected_version, self._replayed)


@pytest.fixture(autouse=True)
def _local_preconditions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_owner", lambda: "usr_demo_user")
    monkeypatch.setattr(cli, "_credential_configured", lambda: True)
    monkeypatch.setattr(cli, "_local_certification_valid", lambda: True)
    # 자동 화해는 DB 를 직접 연다. 이 테스트가 보는 것은 그 뒤의 갈래이므로 비워 둔다.
    monkeypatch.setattr(cli, "_auto_reconcile", lambda repository, owner: 0)


def _run(monkeypatch: pytest.MonkeyPatch, repository: _Repository) -> int:
    monkeypatch.setattr(cli, "_repository", lambda: repository)
    return cli.main(["start"])


def test_armed_without_a_schedule_for_the_session_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = _Repository(target_available=False, replayed=True)

    assert _run(monkeypatch, repository) == 1

    output = capsys.readouterr().out
    assert "MOCK_START=ARMED_WITHOUT_SESSION_SCHEDULE" in output
    assert "TARGET_SESSION_AVAILABLE=FAIL" in output
    # 성공이라고 말한 적이 없어야 한다. 위층은 이 문자열을 보고 ARMED 를 찍는다.
    assert "MOCK_START=NO_OP_ALREADY_ARMED" not in output
    assert "MOCK_START=PASS" not in output


def test_a_scheduled_session_still_starts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = _Repository(target_available=True, replayed=False)

    assert _run(monkeypatch, repository) == 0

    output = capsys.readouterr().out
    assert "MOCK_START=PASS" in output
    assert "MOCK_READINESS=PASS" in output
    assert repository.started
