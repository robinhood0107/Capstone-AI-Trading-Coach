"""S5 실행 상태 기계 경계.

단계는 tick이 실제로 멈출 수 있는 경계만 둔다. provider별로 나누고 싶었지만
execute_bootstrap_materialization이 KRX·KIS·ECOS·bundle을 한 호출로 수행해서 그 사이에서 멈출 수
없다. 코드가 지킬 수 없는 구분을 상태로 만들면 전이 검증이 자기 모순을 잡는다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.run_state import (
    MAX_TICKS,
    RUN_STATE_FILENAME,
    RUN_STATE_HISTORY_FILENAME,
    RunPhase,
    advance_run_state,
    initial_run_state,
    read_run_state,
)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "run"
    root.mkdir(mode=0o700)
    return root


def test_missing_state_starts_at_materialization(tmp_path: Path) -> None:
    """상태 파일이 없으면 수집 전이다. 파일 부재를 오류로 만들면 첫 tick이 불가능하다."""

    state = read_run_state(run_root=_root(tmp_path))
    assert state.phase is RunPhase.MATERIALIZING
    assert state.tick == 0
    assert not state.needs_human


def test_forward_transitions_only(tmp_path: Path) -> None:
    """역행과 건너뛰기를 허용하면 tick이 이미 끝난 단계를 다시 열어 승인 호출을 태운다."""

    root = _root(tmp_path)
    state = initial_run_state()
    for phase in (RunPhase.QUALIFYING, RunPhase.SERVING):
        state = advance_run_state(
            run_root=root, current=state, phase=phase, outcome="ok"
        )
    assert state.phase is RunPhase.SERVING
    assert state.tick == 2

    # 수집으로 되돌아가는 것은 거부된다.
    with pytest.raises(LightGbmContractError, match="transition is not approved"):
        advance_run_state(
            run_root=root,
            current=state,
            phase=RunPhase.MATERIALIZING,
            outcome="rewind",
        )
    # 단계를 건너뛰는 것도 거부된다. 이 검증이 실제로 tick 구현의 건너뛰기를 잡아냈다.
    fresh = initial_run_state()
    with pytest.raises(LightGbmContractError, match="transition is not approved"):
        advance_run_state(
            run_root=root, current=fresh, phase=RunPhase.SERVING, outcome="skip"
        )


def test_staying_in_a_phase_is_allowed(tmp_path: Path) -> None:
    """한 tick이 그 단계를 다 끝내지 못하는 것은 정상이다."""

    root = _root(tmp_path)
    state = advance_run_state(
        run_root=root,
        current=initial_run_state(),
        phase=RunPhase.MATERIALIZING,
        outcome="partial",
    )
    assert state.phase is RunPhase.MATERIALIZING
    assert state.tick == 1


def test_serving_can_reenter_qualification_for_requalification(tmp_path: Path) -> None:
    """재검증 주기가 오면 SERVING에서 qualification으로 돌아간다. 그것만이 허용된 역행이다."""

    root = _root(tmp_path)
    state = initial_run_state()
    for phase in (RunPhase.QUALIFYING, RunPhase.SERVING):
        state = advance_run_state(
            run_root=root, current=state, phase=phase, outcome="ok"
        )
    state = advance_run_state(
        run_root=root, current=state, phase=RunPhase.QUALIFYING, outcome="requalify"
    )
    assert state.phase is RunPhase.QUALIFYING


def test_needs_human_is_reachable_but_not_escapable(tmp_path: Path) -> None:
    """사람이 상태를 되돌리기 전에는 스스로 빠져나오지 않는다."""

    root = _root(tmp_path)
    state = advance_run_state(
        run_root=root,
        current=initial_run_state(),
        phase=RunPhase.NEEDS_HUMAN,
        outcome="CONTRACT_VIOLATION",
    )
    assert state.needs_human
    with pytest.raises(LightGbmContractError, match="transition is not approved"):
        advance_run_state(
            run_root=root, current=state, phase=RunPhase.QUALIFYING, outcome="retry"
        )


def test_state_round_trips_and_history_is_append_only(tmp_path: Path) -> None:
    """상태가 유실돼도 무엇이 있었는지 이력에 남아야 진단이 가능하다."""

    root = _root(tmp_path)
    state = advance_run_state(
        run_root=root,
        current=initial_run_state(),
        phase=RunPhase.QUALIFYING,
        outcome="BUNDLE_SEALED",
    )
    assert read_run_state(run_root=root) == state

    advance_run_state(
        run_root=root, current=state, phase=RunPhase.SERVING, outcome="RELEASE_STAGED"
    )
    lines = (root / RUN_STATE_HISTORY_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert (first["fromPhase"], first["toPhase"]) == ("MATERIALIZING", "QUALIFYING")
    assert (second["fromPhase"], second["toPhase"]) == ("QUALIFYING", "SERVING")
    # 첫 줄은 두 번째 전이 뒤에도 그대로다.
    assert (first["tick"], second["tick"]) == (1, 2)


def test_corrupted_or_noncanonical_state_fails_closed(tmp_path: Path) -> None:
    """손상된 상태를 초기값으로 조용히 대체하면 이미 끝난 수집을 다시 연다."""

    root = _root(tmp_path)
    target = root / RUN_STATE_FILENAME

    target.write_text("{not json}", encoding="utf-8")
    with pytest.raises(LightGbmContractError, match="JSON is invalid"):
        read_run_state(run_root=root)

    target.write_text(json.dumps({"stateVersion": "s5-run-state-v1"}), encoding="utf-8")
    with pytest.raises(LightGbmContractError, match="not closed canonical JSON"):
        read_run_state(run_root=root)

    canonical = {
        "stateVersion": "s5-run-state-v1",
        "phase": "MATERIALIZING",
        "tick": 0,
        "lastOutcome": "",
    }
    target.write_text(json.dumps(canonical, indent=1), encoding="utf-8")
    with pytest.raises(LightGbmContractError, match="not canonical"):
        read_run_state(run_root=root)

    target.write_text(
        json.dumps({**canonical, "phase": "MINING_CRYPTO"}, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(LightGbmContractError, match="phase is not approved"):
        read_run_state(run_root=root)


def test_tick_budget_is_bounded(tmp_path: Path) -> None:
    """무진척 루프가 영원히 도는 것을 구조적으로 막는다."""

    root = _root(tmp_path)
    exhausted = initial_run_state()
    object.__setattr__(exhausted, "tick", MAX_TICKS)
    with pytest.raises(LightGbmContractError, match="tick budget is exhausted"):
        advance_run_state(
            run_root=root,
            current=exhausted,
            phase=RunPhase.MATERIALIZING,
            outcome="loop",
        )
