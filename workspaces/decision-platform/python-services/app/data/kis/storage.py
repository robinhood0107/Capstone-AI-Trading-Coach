from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import errno
import fcntl
import os
from pathlib import Path
import secrets
import stat
from typing import Any, cast

import pandas as pd

from app.data.kis.parsers import DailyBar
from app.data.kis.symbols import normalize_symbol

_PARQUET_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "volume", "turnover"]
_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@dataclass(frozen=True)
class UpsertResult:
    path: Path
    inserted_rows: int
    total_rows: int
    min_date: date | None
    max_date: date | None
    exact_duplicate_rows: int
    conflicting_duplicate_groups: int


class KISConflictingDuplicateError(ValueError):
    """같은 symbol/date의 값이 다르면 임의 keep-last 대신 dataset 게시를 중단한다."""

    def __init__(self, *, exact_duplicate_rows: int, conflicting_groups: int) -> None:
        super().__init__("KIS ingest contains conflicting duplicate rows")
        self.exact_duplicate_rows = exact_duplicate_rows
        self.conflicting_groups = conflicting_groups


def upsert_daily_bars(data_dir: Path, symbol: str, bars: list[DailyBar]) -> UpsertResult:
    # parquet은 S1.1의 로컬 산출물이므로 ignored data 경로 아래에만 쓴다.
    # 같은 symbol+date를 key처럼 다뤄 재실행 시 새 행만 늘어나는 DoD를 만족시킨다.
    symbol = normalize_symbol(symbol)
    filename = f"{symbol}.parquet"
    with _open_daily_directory(data_dir) as (daily_dir, directory_fd):
        existing = _read_existing(directory_fd, filename)
        _validate_existing_frame(existing, symbol)
        existing_keys = _frame_keys(existing)
        for bar in bars:
            if normalize_symbol(bar.symbol) != symbol:
                raise ValueError("KIS daily row symbol must match the target symbol")
        incoming = pd.DataFrame([asdict(bar) for bar in bars])
        if incoming.empty:
            combined = existing
            exact_duplicate_rows = 0
        else:
            incoming["date"] = pd.to_datetime(incoming["date"])
            combined = pd.concat([existing, incoming], ignore_index=True)
        if not combined.empty:
            combined, exact_duplicate_rows, conflicting_groups = _resolve_exact_duplicates(
                combined
            )
            if conflicting_groups:
                raise KISConflictingDuplicateError(
                    exact_duplicate_rows=exact_duplicate_rows,
                    conflicting_groups=conflicting_groups,
                )
        else:
            exact_duplicate_rows = 0
        _write_parquet_atomic(directory_fd, filename, combined)
        path = daily_dir / filename
        dates = pd.to_datetime(combined["date"]) if not combined.empty else pd.Series(dtype="datetime64[ns]")
        combined_keys = _frame_keys(combined)
    return UpsertResult(
        path=path,
        inserted_rows=len(combined_keys - existing_keys),
        total_rows=len(combined),
        min_date=dates.min().date() if not dates.empty else None,
        max_date=dates.max().date() if not dates.empty else None,
        exact_duplicate_rows=exact_duplicate_rows,
        conflicting_duplicate_groups=0,
    )


def missing_daily_ranges(
    data_dir: Path,
    symbol: str,
    start: date,
    end: date,
) -> list[tuple[date, date]]:
    """기존 parquet min/max의 양 끝 누락만 반환하며 내부 거래일 gap은 S1.5 품질검사로 남긴다."""
    if start > end:
        raise ValueError("backfill start must not be after end")
    symbol = normalize_symbol(symbol)
    with _open_daily_directory(data_dir) as (_, directory_fd):
        existing = _read_existing(directory_fd, f"{symbol}.parquet")
    if existing.empty:
        return [(start, end)]

    dates = pd.to_datetime(existing["date"])
    existing_start = dates.min().date()
    existing_end = dates.max().date()
    ranges: list[tuple[date, date]] = []
    if start < existing_start:
        ranges.append((start, min(end, existing_start - timedelta(days=1))))
    if end > existing_end:
        ranges.append((max(start, existing_end + timedelta(days=1)), end))
    return [(range_start, range_end) for range_start, range_end in ranges if range_start <= range_end]


def _read_existing(directory_fd: int, filename: str) -> pd.DataFrame:
    try:
        file_fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    except FileNotFoundError:
        # 빈 스키마를 고정해 첫 실행과 재실행의 concat/upsert 경로가 동일하게 동작하게 한다.
        return pd.DataFrame(columns=_PARQUET_COLUMNS)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("KIS parquet symlink is not allowed") from None
        raise
    metadata = os.fstat(file_fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        os.close(file_fd)
        raise ValueError("KIS parquet regular single-link file is required")
    with os.fdopen(file_fd, "rb") as file:
        existing = pd.read_parquet(file)
    if not existing.empty:
        existing["date"] = pd.to_datetime(existing["date"])
    return existing


@contextmanager
def dataset_lock(data_dir: Path, *, exclusive: bool) -> Iterator[None]:
    """S1.1 writer와 S1.5 reader가 같은 private file lock으로 dataset snapshot을 직렬화한다."""
    expanded = data_dir.expanduser()
    if ".." in expanded.parts:
        raise ValueError("KIS storage dot segment is not allowed")
    root = expanded if expanded.is_absolute() else Path.cwd() / expanded
    root_fd = _open_or_create_directory_tree(root)
    lock_fd = -1
    try:
        try:
            lock_fd = os.open(
                ".dataset.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=root_fd,
            )
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ValueError("KIS dataset lock symlink is not allowed") from None
            raise
        metadata = os.fstat(lock_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("KIS dataset lock must be a regular single-link file")
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        if lock_fd >= 0:
            os.close(lock_fd)
        os.close(root_fd)


@contextmanager
def _open_daily_directory(data_dir: Path) -> Iterator[tuple[Path, int]]:
    expanded = data_dir.expanduser()
    if ".." in expanded.parts:
        raise ValueError("KIS storage dot segment is not allowed")
    root = expanded if expanded.is_absolute() else Path.cwd() / expanded
    root_fd = _open_or_create_directory_tree(root)
    try:
        try:
            os.mkdir("daily", mode=0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        try:
            daily_fd = os.open("daily", _DIRECTORY_OPEN_FLAGS, dir_fd=root_fd)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ValueError("KIS daily directory symlink is not allowed") from None
            raise
        try:
            yield root / "daily", daily_fd
        finally:
            os.close(daily_fd)
    finally:
        os.close(root_fd)


def _open_or_create_directory_tree(path: Path) -> int:
    """절대경로를 `/` dirfd부터 한 component씩 열어 ancestor symlink race를 차단한다."""
    if not path.is_absolute() or path.anchor != "/":
        raise ValueError("KIS storage requires an absolute POSIX path")
    current_fd = os.open("/", _DIRECTORY_OPEN_FLAGS)
    try:
        for component in path.parts[1:]:
            try:
                next_fd = os.open(component, _DIRECTORY_OPEN_FLAGS, dir_fd=current_fd)
            except FileNotFoundError:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                try:
                    next_fd = os.open(component, _DIRECTORY_OPEN_FLAGS, dir_fd=current_fd)
                except OSError as exc:
                    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                        raise ValueError("KIS storage ancestor symlink is not allowed") from None
                    raise
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ValueError("KIS storage ancestor symlink is not allowed") from None
                raise
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _write_parquet_atomic(directory_fd: int, filename: str, frame: pd.DataFrame) -> None:
    temporary = f".{filename}.{secrets.token_hex(16)}.tmp"
    file_fd = -1
    try:
        file_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        os.fchmod(file_fd, 0o600)
        with os.fdopen(file_fd, "wb") as file:
            file_fd = -1
            frame.to_parquet(file, index=False)
            file.flush()
            os.fsync(file.fileno())
        # final symlink가 race로 생겨도 target을 따르지 않고 directory entry 자체를 원자 교체한다.
        os.replace(temporary, filename, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _validate_existing_frame(frame: pd.DataFrame, symbol: str) -> None:
    if list(frame.columns) != _PARQUET_COLUMNS:
        raise ValueError("KIS parquet schema is invalid")
    if frame.empty:
        return
    if any(str(value) != symbol for value in frame["symbol"]):
        raise ValueError("KIS parquet row symbol must match the file symbol")
    if frame.duplicated(subset=["symbol", "date"], keep=False).any():
        raise KISConflictingDuplicateError(exact_duplicate_rows=0, conflicting_groups=1)


def _frame_keys(frame: pd.DataFrame) -> set[tuple[str, date]]:
    if frame.empty:
        return set()
    return {
        (str(row.symbol), pd.Timestamp(cast(Any, row.date)).date())
        for row in frame.itertuples(index=False)
    }


def _resolve_exact_duplicates(frame: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    exact_duplicate_rows = 0
    conflicting_groups = 0
    selected_indices: list[int] = []
    value_columns = ["open", "high", "low", "close", "volume", "turnover"]
    for _, group in frame.groupby(["symbol", "date"], sort=False, dropna=False):
        normalized_values = {
            tuple(int(row[column]) for column in value_columns)
            for _, row in group.iterrows()
        }
        if len(normalized_values) > 1:
            conflicting_groups += 1
            continue
        exact_duplicate_rows += len(group) - 1
        selected_indices.append(int(group.index[0]))
    if conflicting_groups:
        return frame, exact_duplicate_rows, conflicting_groups
    resolved = (
        frame.loc[selected_indices, _PARQUET_COLUMNS]
        .sort_values(["symbol", "date"])
        .reset_index(drop=True)
    )
    return resolved, exact_duplicate_rows, 0
