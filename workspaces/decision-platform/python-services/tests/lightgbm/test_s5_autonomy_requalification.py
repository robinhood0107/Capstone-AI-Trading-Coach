"""재검증 트리거와 last-good 서빙 경계.

model gate 실패가 종단이면 데이터가 쌓여도 모델이 영원히 나오지 않는다. 그러나 매 tick마다 다시
학습하면 계산과 seal이 무의미하게 쌓인다. 그래서 트리거는 유계여야 하고, 무엇을 이미 학습했는지의
권위는 append-only 상태 이력이다.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.lightgbm import tick_cli
from app.lightgbm.run_state import (
    RUN_STATE_HISTORY_FILENAME,
    RunPhase,
    advance_run_state,
    initial_run_state,
    read_run_state,
)
from app.lightgbm.pit_calendar import corrected_calendar
from app.lightgbm.source_bundle import SourceChunkReceipt
from app.lightgbm.temporal import (
    AvailabilityBasis,
    RevisionBasis,
    TemporalQuality,
    TemporalReceipt,
    next_session_evidence_clock,
)
from app.lightgbm.training_append import append_daily_session


def _sessions(*, count: int, start: date = date(2026, 9, 1)) -> tuple[date, ...]:
    """실제 XKRX 거래일만 쓴다. 임의 날짜에는 휴일이 섞여 evidence clock이 거부한다."""

    calendar = corrected_calendar()
    session = calendar.date_to_session(start.isoformat(), direction="next")
    output: list[date] = []
    for _ in range(count):
        output.append(session.date())
        session = calendar.next_session(session)
    return tuple(output)


def _run_root(tmp_path: Path) -> Path:
    root = tmp_path / "run"
    root.mkdir(mode=0o700)
    return root


def _append(run_root: Path, daily_source: Path, session: date) -> None:
    payload = session.isoformat().encode()
    digest = hashlib.sha256(payload).hexdigest()
    receipt = TemporalReceipt(
        source_id="KIS",
        operation_id="FHKST03010100",
        observation_date=session,
        retrieved_at=datetime(2026, 8, 20, tzinfo=UTC),
        availability_basis=AvailabilityBasis.PROJECT_FIXED_LAG,
        revision_basis=RevisionBasis.CONTENT_SNAPSHOT,
        request_sha256="1" * 64,
        snapshot_sha256=digest,
        temporal_quality=TemporalQuality.RECONSTRUCTED_FIXED_LAG,
        policy_effective_at=next_session_evidence_clock(session),
    )
    chunk = SourceChunkReceipt(
        source_id="KIS",
        operation_id="FHKST03010100",
        query_key=f"daily:000001:{session.isoformat()}:{session.isoformat()}",
        content_sha256=digest,
        row_count=1,
        byte_count=len(payload),
        temporal=receipt,
    )
    target = daily_source / chunk.relative_path
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_bytes(payload)
    os.chmod(target, 0o600)
    append_daily_session(
        run_root=run_root,
        daily_source_root=daily_source,
        session_date=session,
        daily_state_sha256="a" * 64,
        effective_month=session.strftime("%Y-%m"),
        chunks=[chunk],
    )


def _serving(run_root: Path):  # type: ignore[no-untyped-def]
    state = advance_run_state(
        run_root=run_root,
        current=initial_run_state(),
        phase=RunPhase.QUALIFYING,
        outcome="BUNDLE_SEALED",
    )
    return advance_run_state(
        run_root=run_root,
        current=state,
        phase=RunPhase.SERVING,
        outcome="QUALIFICATION_CALIBRATION_FAILED",
    )


def test_no_append_means_steady_state(tmp_path: Path) -> None:
    """새 데이터가 없으면 다시 학습할 이유가 없다."""

    run_root = _run_root(tmp_path)
    assert tick_cli.requalification_decision(run_root=run_root) is None


def test_threshold_opens_requalification_once_not_every_tick(tmp_path: Path) -> None:
    """매 tick마다 다시 학습하면 계산과 seal이 무의미하게 쌓인다.

    무엇을 이미 학습했는지는 append-only 이력의 watermark가 권위다. watermark를 남기지 않으면
    임계치를 넘긴 뒤 영원히 다시 걸린다.
    """

    run_root = _run_root(tmp_path)
    daily_source = tmp_path / "daily" / "source"
    daily_source.mkdir(mode=0o700, parents=True)
    state = _serving(run_root)

    sessions = list(_sessions(count=tick_cli.REQUALIFICATION_SESSION_THRESHOLD))
    for index, session in enumerate(sessions, start=1):
        _append(run_root, daily_source, session)
        decision = tick_cli.requalification_decision(run_root=run_root)
        if index < tick_cli.REQUALIFICATION_SESSION_THRESHOLD:
            assert decision is None
        else:
            assert decision == ("REQUALIFY_SESSION_THRESHOLD", sessions[-1])

    # tick이 전이하면서 watermark를 남긴다.
    code = tick_cli._run_phase(run_root=run_root, packet=None, state=state)  # type: ignore[arg-type]
    assert code == tick_cli.EXIT_PROGRESS
    assert read_run_state(run_root=run_root).phase is RunPhase.QUALIFYING
    assert tick_cli.last_qualified_session(run_root=run_root) == sessions[-1]

    # 같은 데이터로는 다시 열리지 않는다.
    assert tick_cli.requalification_decision(run_root=run_root) is None

    history = (run_root / RUN_STATE_HISTORY_FILENAME).read_text(encoding="utf-8")
    marked = [
        json.loads(line)
        for line in history.splitlines()
        if line.strip() and json.loads(line).get("marker")
    ]
    assert marked[-1]["marker"] == sessions[-1].isoformat()
    assert marked[-1]["toPhase"] == "QUALIFYING"


def test_month_boundary_opens_requalification_below_the_threshold(
    tmp_path: Path,
) -> None:
    """월이 바뀌면 임계치 미만이어도 다시 검증한다. universe 구성이 바뀌는 경계다."""

    run_root = _run_root(tmp_path)
    daily_source = tmp_path / "daily" / "source"
    daily_source.mkdir(mode=0o700, parents=True)
    state = _serving(run_root)

    september = _sessions(count=1, start=date(2026, 9, 28))[0]
    _append(run_root, daily_source, september)
    advance_run_state(
        run_root=run_root,
        current=state,
        phase=RunPhase.QUALIFYING,
        outcome="REQUALIFY_SESSION_THRESHOLD",
        marker=september.isoformat(),
    )
    assert tick_cli.last_qualified_session(run_root=run_root) == september

    october = _sessions(count=1, start=date(2026, 10, 1))[0]
    assert october.strftime("%Y-%m") != september.strftime("%Y-%m")
    _append(run_root, daily_source, october)
    decision = tick_cli.requalification_decision(run_root=run_root)
    assert decision == ("REQUALIFY_MONTH_BOUNDARY", october)


def test_same_month_below_threshold_stays_quiet(tmp_path: Path) -> None:
    """같은 달에서 몇 세션 늘어난 것만으로 다시 학습하지 않는다."""

    run_root = _run_root(tmp_path)
    daily_source = tmp_path / "daily" / "source"
    daily_source.mkdir(mode=0o700, parents=True)
    state = _serving(run_root)

    same_month = _sessions(count=5, start=date(2026, 9, 1))
    _append(run_root, daily_source, same_month[0])
    advance_run_state(
        run_root=run_root,
        current=state,
        phase=RunPhase.QUALIFYING,
        outcome="REQUALIFY_SESSION_THRESHOLD",
        marker=same_month[0].isoformat(),
    )
    for session in same_month[1:]:
        _append(run_root, daily_source, session)
    assert tick_cli.requalification_decision(run_root=run_root) is None


def test_gate_failure_returns_to_serving_without_touching_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gate 실패는 계약 위반이 아니라 정상 상태다. 활성 pointer는 건드리지 않는다.

    last-good이 없으면 ABSTAIN이 유지된다. 그것이 지금의 정직한 상태다.
    """

    run_root = _run_root(tmp_path)
    state = advance_run_state(
        run_root=run_root,
        current=initial_run_state(),
        phase=RunPhase.QUALIFYING,
        outcome="BUNDLE_SEALED",
    )
    monkeypatch.setattr(
        tick_cli, "_qualify", lambda **_: "QUALIFICATION_CALIBRATION_FAILED"
    )
    code = tick_cli._run_phase(run_root=run_root, packet=None, state=state)  # type: ignore[arg-type]
    assert code == tick_cli.EXIT_PROGRESS
    updated = read_run_state(run_root=run_root)
    assert updated.phase is RunPhase.SERVING
    assert updated.last_outcome == "QUALIFICATION_CALIBRATION_FAILED"
    # 활성화 단계는 상태 기계에 없다. 계약이 수동 CAS로 고정했다.
    assert "ACTIVATING" not in {str(item) for item in RunPhase}
