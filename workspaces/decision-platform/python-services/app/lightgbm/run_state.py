"""S5 자율 운영의 실행 상태. tick 하나가 어디서 이어받을지를 정하는 유일한 권위다.

지금까지 실행은 monolithic이라 끝까지 가야만 했고, 중간에 죽으면 사람이 다시 시작해야 했다.
상태를 durable하게 남기면 tick이 스스로 이어받는다.

이 파일은 회계 원장이 아니다. 누적 물리 호출 회계는 progress journal이 단독 권위이며, 상태는
"다음에 무엇을 할 차례인가"만 말한다. 그래서 상태가 유실돼도 journal에서 다시 유도할 수 있어야
하고, 상태가 journal과 어긋나면 journal이 이긴다.

ACTIVATING은 상태에 없다. pointer 전환은 계약이 수동 CAS로 고정했다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.errors import LightGbmContractError

RUN_STATE_FILENAME = "state.json"
RUN_STATE_HISTORY_FILENAME = "state-history.jsonl"
RUN_STATE_VERSION = "s5-run-state-v1"
MAX_RUN_STATE_BYTES = 64 * 1024
# tick 수는 유계여야 한다. 무진척 루프가 영원히 도는 것을 구조적으로 막는다.
MAX_TICKS = 10_000
MAX_RUN_STATE_HISTORY_BYTES = 4 * 1024 * 1024


class RunPhase(StrEnum):
    """tick이 실제로 멈출 수 있는 경계만 단계로 둔다.

    provider별로 나누고 싶지만 execute_bootstrap_materialization이 KRX·KIS·ECOS·bundle을 한
    호출로 수행한다. 코드가 지킬 수 없는 구분을 상태로 만들면 전이 검증이 자기 모순을 잡는다.
    executor를 실제로 쪼갠 뒤에 세분화한다.
    """

    MATERIALIZING = "MATERIALIZING"
    QUALIFYING = "QUALIFYING"
    SERVING = "SERVING"
    NEEDS_HUMAN = "NEEDS_HUMAN"


# 전이 표. NEEDS_HUMAN은 어디서든 갈 수 있지만 사람이 상태를 되돌리기 전에는 나올 수 없다.
_FORWARD: Mapping[RunPhase, tuple[RunPhase, ...]] = {
    RunPhase.MATERIALIZING: (RunPhase.QUALIFYING,),
    RunPhase.QUALIFYING: (RunPhase.SERVING,),
    # 재검증 주기가 오면 SERVING에서 다시 qualification으로 돌아간다. 그 외 역행은 없다.
    RunPhase.SERVING: (RunPhase.QUALIFYING,),
    RunPhase.NEEDS_HUMAN: (),
}


@dataclass(frozen=True, slots=True)
class RunState:
    """한 run의 현재 단계와 tick 수, 마지막 결과."""

    content: bytes
    sha256: str
    phase: RunPhase
    tick: int
    last_outcome: str

    @property
    def needs_human(self) -> bool:
        return self.phase is RunPhase.NEEDS_HUMAN


def initial_run_state() -> RunState:
    """수집 전 상태다. 파일이 없으면 이 값에서 시작한다."""

    return _build(phase=RunPhase.MATERIALIZING, tick=0, last_outcome="")


def read_run_state(*, run_root: Path) -> RunState:
    """상태 파일을 읽는다. 없으면 초기 상태이며, 손상은 조용히 넘기지 않는다."""

    target = run_root / RUN_STATE_FILENAME
    if not target.exists():
        return initial_run_state()
    raw = target.read_bytes()
    if len(raw) > MAX_RUN_STATE_BYTES:
        raise LightGbmContractError("run state exceeds the approved size")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LightGbmContractError("run state JSON is invalid") from error
    if not isinstance(payload, dict) or set(payload) != {
        "stateVersion",
        "phase",
        "tick",
        "lastOutcome",
    }:
        raise LightGbmContractError("run state is not closed canonical JSON")
    if payload["stateVersion"] != RUN_STATE_VERSION:
        raise LightGbmContractError("run state version is not approved")
    try:
        phase = RunPhase(str(payload["phase"]))
    except ValueError:
        raise LightGbmContractError("run state phase is not approved") from None
    tick = payload["tick"]
    if isinstance(tick, bool) or not isinstance(tick, int) or not 0 <= tick <= MAX_TICKS:
        raise LightGbmContractError("run state tick is out of bounds")
    state = _build(phase=phase, tick=tick, last_outcome=str(payload["lastOutcome"]))
    if state.content != raw:
        raise LightGbmContractError("run state is not canonical")
    return state


def advance_run_state(
    *,
    run_root: Path,
    current: RunState,
    phase: RunPhase,
    outcome: str,
    marker: str = "",
) -> RunState:
    """전이를 검증하고 상태를 원자적으로 바꾼 뒤 이력에 append한다.

    같은 phase에 머무는 것은 허용한다. 한 tick이 그 phase를 다 끝내지 못했을 뿐이기 때문이다.

    `marker`는 호출자가 이 전이에 남기는 watermark다. 무엇을 이미 처리했는지의 권위를 별도 표가
    아니라 append-only 이력에 두기 위한 것이다.
    """

    if phase is not current.phase and phase is not RunPhase.NEEDS_HUMAN:
        if phase not in _FORWARD[current.phase]:
            raise LightGbmContractError("run state transition is not approved")
    if current.tick >= MAX_TICKS:
        raise LightGbmContractError("run state tick budget is exhausted")
    updated = _build(phase=phase, tick=current.tick + 1, last_outcome=outcome)
    _append_history(
        run_root=run_root, previous=current, updated=updated, marker=marker
    )
    target = run_root / RUN_STATE_FILENAME
    temporary = run_root / f".{RUN_STATE_FILENAME}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        os.write(descriptor, updated.content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, target)
    return updated


def _append_history(
    *, run_root: Path, previous: RunState, updated: RunState, marker: str = ""
) -> None:
    """전이 이력은 append-only다. 상태 파일이 유실돼도 무엇이 있었는지 남는다."""

    # canonical_json_bytes가 마지막 newline을 이미 포함한다.
    line = canonical_json_bytes(
        {
            "stateVersion": RUN_STATE_VERSION,
            "fromPhase": str(previous.phase),
            "toPhase": str(updated.phase),
            "tick": updated.tick,
            "outcome": updated.last_outcome,
            "marker": marker[:32],
        }
    )
    target = run_root / RUN_STATE_HISTORY_FILENAME
    if target.exists() and target.stat().st_size + len(line) > MAX_RUN_STATE_HISTORY_BYTES:
        raise LightGbmContractError("run state history exceeds the approved size")
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        os.write(descriptor, line)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _build(*, phase: RunPhase, tick: int, last_outcome: str) -> RunState:
    import hashlib

    content = canonical_json_bytes(
        {
            "stateVersion": RUN_STATE_VERSION,
            "phase": str(phase),
            "tick": tick,
            "lastOutcome": last_outcome[:64],
        }
    )
    return RunState(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        phase=phase,
        tick=tick,
        last_outcome=last_outcome[:64],
    )
