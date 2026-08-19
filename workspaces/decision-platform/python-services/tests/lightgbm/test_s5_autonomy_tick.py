"""tick의 단계 전진과 실패 분류가 종료 코드·상태로 이어지는지 고정한다.

provider는 열지 않는다. 단계 전진 결정과 실패 분류만 검증한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.data.kis.accounting import KISCallBudgetExceeded
from app.data.kis.http_client import KISRetryableStatus
from app.lightgbm import tick_cli
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.outcomes import BootstrapEvidenceGap, CollectionUnit
from app.lightgbm.run_state import (
    RunPhase,
    advance_run_state,
    initial_run_state,
    read_run_state,
)


def _run_root(tmp_path: Path) -> Path:
    root = tmp_path / "run"
    root.mkdir(mode=0o700)
    return root


def _unit() -> CollectionUnit:
    return CollectionUnit(
        provider="KIS", operation_id="FHKST03010100", query_sha256="a" * 64, label="000001"
    )


def test_materializing_tick_advances_to_qualifying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """수집과 bundle이 끝나면 다음 단계는 qualification이다."""

    root = _run_root(tmp_path)
    monkeypatch.setattr(tick_cli, "_collect", lambda **_: None)
    code = tick_cli._run_phase(
        run_root=root, packet=None, state=initial_run_state()  # type: ignore[arg-type]
    )
    assert code == tick_cli.EXIT_PROGRESS
    assert read_run_state(run_root=root).phase is RunPhase.QUALIFYING


def test_qualifying_tick_reaches_serving_even_when_the_gate_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """model gate 실패는 계약 위반이 아니라 정상 상태다.

    실패를 종단으로 만들면 이전 release가 서빙 중이어도 루프가 멈춘다. last-good이 없으면
    ABSTAIN이 유지되고, 그것이 지금의 정직한 상태다.
    """

    root = _run_root(tmp_path)
    state = advance_run_state(
        run_root=root,
        current=initial_run_state(),
        phase=RunPhase.QUALIFYING,
        outcome="BUNDLE_SEALED",
    )
    monkeypatch.setattr(
        tick_cli, "_qualify", lambda **_: "QUALIFICATION_CALIBRATION_FAILED"
    )
    code = tick_cli._run_phase(run_root=root, packet=None, state=state)  # type: ignore[arg-type]
    assert code == tick_cli.EXIT_PROGRESS
    updated = read_run_state(run_root=root)
    assert updated.phase is RunPhase.SERVING
    assert updated.last_outcome == "QUALIFICATION_CALIBRATION_FAILED"


def test_serving_tick_is_a_quiet_steady_state(tmp_path: Path) -> None:
    """할 일이 없으면 조용히 무진척으로 끝난다. 재검증 주기 판정은 단계 4다."""

    root = _run_root(tmp_path)
    state = advance_run_state(
        run_root=root,
        current=advance_run_state(
            run_root=root,
            current=initial_run_state(),
            phase=RunPhase.QUALIFYING,
            outcome="BUNDLE_SEALED",
        ),
        phase=RunPhase.SERVING,
        outcome="RELEASE_STAGED",
    )
    code = tick_cli._run_phase(run_root=root, packet=None, state=state)  # type: ignore[arg-type]
    assert code == tick_cli.EXIT_NO_PROGRESS
    assert read_run_state(run_root=root).phase is RunPhase.SERVING


@pytest.mark.parametrize(
    "error",
    [
        KISRetryableStatus(503, "unavailable"),
        BootstrapEvidenceGap("gap", unit=_unit(), measured={"missingSessions": 1}),
    ],
)
def test_retryable_and_evidence_gap_stay_in_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """다음 tick에 이어갈 수 있는 실패는 단계를 넘기지도 사람을 부르지도 않는다."""

    root = _run_root(tmp_path)

    def _raise(**_: object) -> None:
        raise error

    monkeypatch.setattr(tick_cli, "_collect", _raise)
    code = tick_cli._run_phase(
        run_root=root, packet=None, state=initial_run_state()  # type: ignore[arg-type]
    )
    assert code == tick_cli.EXIT_NO_PROGRESS
    updated = read_run_state(run_root=root)
    assert updated.phase is RunPhase.MATERIALIZING
    assert not updated.needs_human


@pytest.mark.parametrize(
    "error",
    [
        KISCallBudgetExceeded("cap"),
        LightGbmContractError("contract"),
        ValueError("unclassified"),
    ],
)
def test_budget_and_contract_failures_stop_for_a_human(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """분류를 선언하지 않은 실패까지 사람을 부른다. 모르는 실패를 넘기면 예산을 태운다."""

    root = _run_root(tmp_path)

    def _raise(**_: object) -> None:
        raise error

    monkeypatch.setattr(tick_cli, "_collect", _raise)
    code = tick_cli._run_phase(
        run_root=root, packet=None, state=initial_run_state()  # type: ignore[arg-type]
    )
    assert code == tick_cli.EXIT_NEEDS_HUMAN
    assert read_run_state(run_root=root).needs_human


def test_exit_codes_are_distinct(tmp_path: Path) -> None:
    """스케줄러가 진척·무진척·사람 필요를 구분해야 watchdog이 조용할 수 있다."""

    del tmp_path
    assert len(
        {
            tick_cli.EXIT_PROGRESS,
            tick_cli.EXIT_NO_PROGRESS,
            tick_cli.EXIT_NEEDS_HUMAN,
        }
    ) == 3
