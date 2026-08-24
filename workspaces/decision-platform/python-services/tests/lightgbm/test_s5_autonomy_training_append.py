"""학습 append 저장소와 rolling 학습 window 경계."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.lightgbm.bootstrap_packet import author_bootstrap_packet
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.pit_calendar import PitSessionWindow
from app.lightgbm.source_bundle import SourceChunkReceipt
from app.lightgbm.temporal import (
    AvailabilityBasis,
    RevisionBasis,
    TemporalQuality,
    TemporalReceipt,
    next_session_evidence_clock,
)
from app.lightgbm.training_append import (
    APPEND_DIRECTORY,
    APPEND_INDEX_FILENAME,
    MAX_CHUNKS_PER_SESSION,
    append_daily_session,
    appended_sessions,
    derive_training_window,
    read_append_index,
)


def _private(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True)
    return path


def _chunk(source_root: Path, *, symbol: str, session: date) -> SourceChunkReceipt:
    """daily가 봉인한 chunk 하나를 흉내낸다. 내용은 임의지만 digest는 실제 bytes에서 온다."""

    payload = f"{symbol}:{session.isoformat()}".encode()
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
        query_key=f"daily:{symbol}:{session.isoformat()}:{session.isoformat()}",
        content_sha256=digest,
        row_count=1,
        byte_count=len(payload),
        temporal=receipt,
    )
    target = source_root / chunk.relative_path
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_bytes(payload)
    os.chmod(target, 0o600)
    return chunk


def test_appending_a_session_copies_chunks_and_records_one_index_line(
    tmp_path: Path,
) -> None:
    """daily run root를 경로로 참조하면 owner-private 컨테인먼트가 깨진다. 그래서 복사한다."""

    run_root = _private(tmp_path / "run")
    daily_source = _private(tmp_path / "daily" / "source")
    session = date(2026, 8, 14)
    chunks = [_chunk(daily_source, symbol=f"00000{index}", session=session) for index in range(3)]

    entry = append_daily_session(
        run_root=run_root,
        daily_source_root=daily_source,
        session_date=session,
        daily_state_sha256="a" * 64,
        effective_month="2026-08",
        chunks=chunks,
    )
    assert entry.session_date == session
    assert len(entry.chunk_digests) == 3

    append_root = run_root / APPEND_DIRECTORY
    for chunk in chunks:
        copied = append_root / chunk.relative_path
        assert copied.exists()
        assert hashlib.sha256(copied.read_bytes()).hexdigest() == chunk.content_sha256
        assert os.stat(copied).st_mode & 0o777 == 0o600

    lines = (append_root / APPEND_INDEX_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["sessionDate"] == session.isoformat()
    assert appended_sessions(run_root=run_root) == (session,)


def test_replaying_the_same_session_is_idempotent(tmp_path: Path) -> None:
    """일일 tick 재시도가 index를 두 줄로 만들면 학습 window가 조용히 달라진다."""

    run_root = _private(tmp_path / "run")
    daily_source = _private(tmp_path / "daily" / "source")
    session = date(2026, 8, 14)
    chunks = [_chunk(daily_source, symbol="000001", session=session)]
    for _ in range(2):
        append_daily_session(
            run_root=run_root,
            daily_source_root=daily_source,
            session_date=session,
            daily_state_sha256="a" * 64,
            effective_month="2026-08",
            chunks=chunks,
        )
    assert len(read_append_index(run_root=run_root)) == 1


def test_conflicting_evidence_for_one_session_is_refused(tmp_path: Path) -> None:
    """같은 날짜에 서로 다른 daily state가 오면 어느 쪽이 진실인지 알 수 없다."""

    run_root = _private(tmp_path / "run")
    daily_source = _private(tmp_path / "daily" / "source")
    session = date(2026, 8, 14)
    chunks = [_chunk(daily_source, symbol="000001", session=session)]
    append_daily_session(
        run_root=run_root,
        daily_source_root=daily_source,
        session_date=session,
        daily_state_sha256="a" * 64,
        effective_month="2026-08",
        chunks=chunks,
    )
    with pytest.raises(LightGbmContractError, match="conflicts with the sealed index"):
        append_daily_session(
            run_root=run_root,
            daily_source_root=daily_source,
            session_date=session,
            daily_state_sha256="b" * 64,
            effective_month="2026-08",
            chunks=chunks,
        )


def test_append_is_bounded_by_the_daily_call_budget(tmp_path: Path) -> None:
    """세션당 chunk가 일일 승인 상한을 넘을 수 없다."""

    run_root = _private(tmp_path / "run")
    daily_source = _private(tmp_path / "daily" / "source")
    session = date(2026, 8, 14)
    too_many = [
        _chunk(daily_source, symbol=f"{index:06d}", session=session)
        for index in range(MAX_CHUNKS_PER_SESSION + 1)
    ]
    with pytest.raises(LightGbmContractError, match="exceeds the daily bound"):
        append_daily_session(
            run_root=run_root,
            daily_source_root=daily_source,
            session_date=session,
            daily_state_sha256="a" * 64,
            effective_month="2026-08",
            chunks=too_many,
        )
    with pytest.raises(LightGbmContractError, match="at least one chunk"):
        append_daily_session(
            run_root=run_root,
            daily_source_root=daily_source,
            session_date=session,
            daily_state_sha256="a" * 64,
            effective_month="2026-08",
            chunks=[],
        )


def test_corrupted_index_fails_closed(tmp_path: Path) -> None:
    """index를 조용히 무시하면 이미 append한 세션이 학습에서 사라진다."""

    run_root = _private(tmp_path / "run")
    append_root = _private(run_root / APPEND_DIRECTORY)
    target = append_root / APPEND_INDEX_FILENAME
    target.write_text("{not json}\n", encoding="utf-8")
    os.chmod(target, 0o600)
    with pytest.raises(LightGbmContractError, match="index JSON is invalid"):
        read_append_index(run_root=run_root)


def test_training_window_rolls_forward_keeping_exact_dimensions() -> None:
    """창이 굴러도 exact raw/eligible 수는 walk-forward 계약이므로 유지된다."""

    packet = author_bootstrap_packet(cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC))
    base = packet.window
    assert len(base.raw_sessions) == 1_072
    assert len(base.eligible_sessions) == 1_007

    # append가 없으면 packet window가 그대로다. 기존 경로가 바뀌지 않는다.
    assert derive_training_window(packet_window=base, appended=()) is base
    # 이미 창 안에 있는 날짜는 창을 옮기지 않는다.
    assert derive_training_window(packet_window=base, appended=base.raw_sessions[-3:]) is base

    later = date(2026, 8, 14)
    rolled = derive_training_window(packet_window=base, appended=(later,))
    assert len(rolled.raw_sessions) == 1_072
    assert len(rolled.eligible_sessions) == 1_007
    assert rolled.raw_sessions[-1] == later
    assert rolled.latest_completed == later
    # 가장 오래된 raw session이 하나 빠진다.
    assert rolled.raw_sessions[0] == base.raw_sessions[1]
    # eligible 구간의 warm-up 폭이 유지된다.
    assert rolled.raw_sessions.index(rolled.eligible_sessions[0]) == base.raw_sessions.index(
        base.eligible_sessions[0]
    )
    # cutoff는 새 마지막 session에서 다시 유도된다. 그대로 두면 PIT 경계가 깨진다.
    assert rolled.cutoff == next_session_evidence_clock(later)
    assert rolled.cutoff > base.cutoff


def test_training_window_refuses_dimension_drift() -> None:
    """유도 결과가 승인 차원과 어긋나면 조용히 넘기지 않는다."""

    # eligible이 raw의 부분구간이 아니면 warm-up 폭을 되읽을 수 없다. 조용히 넘기지 않는다.
    inconsistent = PitSessionWindow(
        cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC),
        latest_completed=date(2026, 8, 13),
        raw_sessions=(date(2026, 8, 12), date(2026, 8, 13)),
        eligible_sessions=(date(2026, 8, 11),),
    )
    with pytest.raises(ValueError):
        derive_training_window(packet_window=inconsistent, appended=(date(2026, 8, 14),))
