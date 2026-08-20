"""일일 수집분을 학습 데이터셋에 누적하는 append 저장소.

`DailyInferenceState`는 추론용 60/20-session bounded snapshot이라 학습 길이 역사를 담지 않고,
daily run은 자체 source manifest를 봉인하지 않는다(bootstrap manifest SHA를 참조만 한다). 그래서
학습용 누적은 별도 저장소가 필요하다.

packet window는 건드리지 않는다. window를 옮기면 KIS query 신원이 전부 바뀌어
(`symbol:start:cursor_end:page`) 승인 상한만큼 재수집이 필요해진다. append는 window 밖 세션을
별도 이름공간(`daily:`)으로 쌓아 그 문제를 우회한다.

chunk는 참조가 아니라 복사한다. daily run root를 경로로 참조하면 owner-private root 컨테인먼트가
깨지고 safe_io의 root-relative 읽기를 쓸 수 없다. 세션당 chunk는 수십 개라 비용이 무의미하다.

index는 append-only다. 어떤 세션이 어떤 daily state에서 왔는지가 학습 window 유도의 유일한
권위이며, 그 이력을 고쳐 쓰면 데이터셋이 조용히 달라진다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.bootstrap_journal import BootstrapJournal
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.pit_calendar import PitSessionWindow
from app.lightgbm.source_bundle import SOURCE_CHUNK_BYTE_CAPS, SourceChunkReceipt
from app.lightgbm.temporal import next_session_evidence_clock
from app.rag.safe_io import (
    RagSafeIoError,
    read_approved_regular_file,
    write_approved_new_file,
)

APPEND_DIRECTORY = "append"
APPEND_INDEX_FILENAME = "index.jsonl"
APPEND_INDEX_VERSION = "s5-training-append-index-v1"
MAX_APPEND_INDEX_BYTES = 4 * 1024 * 1024
# 한 세션이 남길 수 있는 chunk 수 상한이다. daily 승인 상한(41 calls)보다 클 이유가 없다.
MAX_CHUNKS_PER_SESSION = 41


@dataclass(frozen=True, slots=True)
class AppendedSession(Mapping[str, object]):
    """append된 한 세션의 신원. 학습 window 유도가 이 목록만 본다."""

    session_date: date
    daily_state_sha256: str
    effective_month: str
    chunk_digests: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "indexVersion": APPEND_INDEX_VERSION,
            "sessionDate": self.session_date.isoformat(),
            "dailyStateSha256": self.daily_state_sha256,
            "effectiveMonth": self.effective_month,
            "chunkDigests": list(self.chunk_digests),
        }

    def __getitem__(self, key: str) -> object:
        return self.as_dict()[key]

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.as_dict())

    def __len__(self) -> int:
        return len(self.as_dict())


def append_daily_session(
    *,
    run_root: Path,
    daily_source_root: Path,
    session_date: date,
    daily_state_sha256: str,
    effective_month: str,
    chunks: Sequence[SourceChunkReceipt],
) -> AppendedSession:
    """일일 수집 chunk를 학습 저장소로 복사하고 index에 한 줄 남긴다.

    같은 세션을 다시 append하면 내용이 같을 때만 통과한다. daily state가 다르면 같은 날짜에 서로
    다른 증거가 들어온 것이므로 거부한다.
    """

    if not chunks:
        raise LightGbmContractError("training append requires at least one chunk")
    if len(chunks) > MAX_CHUNKS_PER_SESSION:
        raise LightGbmContractError("training append chunk count exceeds the daily bound")

    append_root = run_root / APPEND_DIRECTORY
    _ensure_private_directory(append_root)
    _ensure_private_directory(append_root / "chunks")

    existing = {item.session_date: item for item in read_append_index(run_root=run_root)}
    prior = existing.get(session_date)

    digests: list[str] = []
    for chunk in sorted(chunks, key=lambda item: item.content_sha256):
        payload = read_approved_regular_file(
            approved_root=daily_source_root,
            relative_path=chunk.relative_path,
            max_bytes=SOURCE_CHUNK_BYTE_CAPS[chunk.source_id],
        )
        if payload.content_sha256 != chunk.content_sha256:
            raise LightGbmContractError("appended chunk digest mismatches its receipt")
        _publish_exact(
            root=append_root,
            relative_path=chunk.relative_path,
            content=payload.content,
            max_bytes=SOURCE_CHUNK_BYTE_CAPS[chunk.source_id],
        )
        digests.append(chunk.content_sha256)

    entry = AppendedSession(
        session_date=session_date,
        daily_state_sha256=daily_state_sha256,
        effective_month=effective_month,
        chunk_digests=tuple(digests),
    )
    if prior is not None:
        if prior != entry:
            raise LightGbmContractError(
                "appended session conflicts with the sealed index entry"
            )
        return entry
    _append_index_line(append_root=append_root, entry=entry)
    return entry


def read_append_index(*, run_root: Path) -> tuple[AppendedSession, ...]:
    """append된 세션 목록을 세션 순으로 준다. 손상은 조용히 넘기지 않는다."""

    target = run_root / APPEND_DIRECTORY / APPEND_INDEX_FILENAME
    if not target.exists():
        return ()
    raw = read_approved_regular_file(
        approved_root=run_root / APPEND_DIRECTORY,
        relative_path=APPEND_INDEX_FILENAME,
        max_bytes=MAX_APPEND_INDEX_BYTES,
    )
    entries: list[AppendedSession] = []
    for line in raw.content.decode("utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError as error:
            raise LightGbmContractError("training append index JSON is invalid") from error
        entries.append(_parse_entry(payload))
    ordered = tuple(sorted(entries, key=lambda item: item.session_date))
    if len({item.session_date for item in ordered}) != len(ordered):
        raise LightGbmContractError("training append index has duplicate sessions")
    return ordered


def appended_sessions(*, run_root: Path) -> tuple[date, ...]:
    """학습 window 유도가 쓰는 세션 목록이다."""

    return tuple(item.session_date for item in read_append_index(run_root=run_root))


def _parse_entry(payload: object) -> AppendedSession:
    if not isinstance(payload, dict) or set(payload) != {
        "indexVersion",
        "sessionDate",
        "dailyStateSha256",
        "effectiveMonth",
        "chunkDigests",
    }:
        raise LightGbmContractError("training append index entry is not closed")
    if payload["indexVersion"] != APPEND_INDEX_VERSION:
        raise LightGbmContractError("training append index version is not approved")
    digests = payload["chunkDigests"]
    if (
        not isinstance(digests, list)
        or not digests
        or len(digests) > MAX_CHUNKS_PER_SESSION
        or any(not _is_sha256(item) for item in digests)
    ):
        raise LightGbmContractError("training append chunk digests are invalid")
    try:
        session_date = date.fromisoformat(str(payload["sessionDate"]))
    except ValueError:
        raise LightGbmContractError("training append session date is invalid") from None
    if not _is_sha256(payload["dailyStateSha256"]):
        raise LightGbmContractError("training append daily state digest is invalid")
    month = str(payload["effectiveMonth"])
    if len(month) != 7 or month[4] != "-":
        raise LightGbmContractError("training append effective month is invalid")
    return AppendedSession(
        session_date=session_date,
        daily_state_sha256=str(payload["dailyStateSha256"]),
        effective_month=month,
        chunk_digests=tuple(str(item) for item in digests),
    )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _append_index_line(*, append_root: Path, entry: AppendedSession) -> None:
    line = canonical_json_bytes(entry.as_dict())
    target = append_root / APPEND_INDEX_FILENAME
    if target.exists() and target.stat().st_size + len(line) > MAX_APPEND_INDEX_BYTES:
        raise LightGbmContractError("training append index exceeds the approved size")
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


def _publish_exact(
    *, root: Path, relative_path: str, content: bytes, max_bytes: int
) -> None:
    try:
        result = write_approved_new_file(
            approved_root=root,
            relative_path=relative_path,
            content=content,
            max_bytes=max_bytes,
        )
        os.chmod(result.absolute_path, 0o600, follow_symlinks=False)
    except RagSafeIoError as error:
        existing = read_approved_regular_file(
            approved_root=root,
            relative_path=relative_path,
            max_bytes=max_bytes,
        )
        if existing.content != content:
            raise LightGbmContractError("appended chunk conflicts with sealed bytes") from error


def _ensure_private_directory(path: Path) -> None:
    import stat

    if os.path.lexists(path):
        metadata = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise LightGbmContractError("training append directory is not owner-private")
        return
    path.mkdir(mode=0o700, parents=True)
def derive_training_window(
    *, packet_window: PitSessionWindow, appended: Sequence[date]
) -> PitSessionWindow:
    """packet window에 append 세션을 붙이고 가장 오래된 것부터 잘라 창을 굴린다.

    exact raw/eligible session 수는 walk-forward 계약이므로 유지한다. append가 없으면 packet
    window를 그대로 돌려주어 기존 경로가 바뀌지 않는다.

    `latest_completed`와 `cutoff`는 새 마지막 raw session에서 다시 유도한다. cutoff를 그대로 두면
    append된 세션의 label maturity가 cutoff보다 늦어 PIT 경계가 깨진다.
    """

    extra = tuple(sorted({day for day in appended if day > packet_window.raw_sessions[-1]}))
    if not extra:
        return packet_window

    raw_count = len(packet_window.raw_sessions)
    eligible_count = len(packet_window.eligible_sessions)
    raw = (*packet_window.raw_sessions, *extra)[-raw_count:]
    # eligible은 raw의 앞쪽 warm-up과 뒤쪽 label tail을 뺀 구간이다. 그 폭은 계약이 고정한다.
    tail = raw_count - eligible_count - _warmup_span(packet_window)
    eligible = raw[_warmup_span(packet_window) : raw_count - tail]
    if len(raw) != raw_count or len(eligible) != eligible_count:
        raise LightGbmContractError("derived training window drifted from approved dimensions")
    latest = raw[-1]
    return PitSessionWindow(
        cutoff=next_session_evidence_clock(latest),
        latest_completed=latest,
        raw_sessions=raw,
        eligible_sessions=eligible,
    )


def _warmup_span(window: PitSessionWindow) -> int:
    """packet window가 실제로 쓴 warm-up 폭을 그 window에서 되읽는다."""

    return window.raw_sessions.index(window.eligible_sessions[0])
def append_from_daily_run(
    *,
    run_root: Path,
    daily_run_root: Path,
    session_date: date,
    daily_state_sha256: str,
    effective_month: str,
) -> AppendedSession:
    """daily run의 journal이 봉인한 chunk를 학습 저장소로 누적한다.

    journal이 어떤 chunk가 실제로 봉인됐는지의 권위다. 결과 객체에 목록을 얹지 않고 그 권위를
    다시 읽는다. 값이 보존되지 않는 성공(access token)은 chunk가 없으므로 자연히 제외된다.
    """

    daily_source_root = daily_run_root / "source"
    journal = BootstrapJournal(daily_source_root)
    chunks = [
        attempt.chunk
        for attempt in journal.attempts
        if attempt.state == "SUCCEEDED" and attempt.chunk is not None
    ]
    if not chunks:
        raise LightGbmContractError("daily run sealed no chunk to append")
    return append_daily_session(
        run_root=run_root,
        daily_source_root=daily_source_root,
        session_date=session_date,
        daily_state_sha256=daily_state_sha256,
        effective_month=effective_month,
        chunks=[chunk for chunk in chunks if chunk is not None],
    )
