from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
import errno
import hashlib
from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

import exchange_calendars as xcals
import pandas as pd
import pyarrow.parquet as pq
from pydantic import ValidationError

from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_bytes,
)
from app.data.kis.accounting import CollectionRunSummary
from app.data.kis.run_artifacts import DatasetFileInventory, SuccessfulDatasetManifest
from app.data.kis.storage import dataset_lock
from app.data.quality.models import (
    AnalysisContext,
    ManifestReference,
    SymbolDataset,
)
from app.data.quality.policy import (
    CANONICAL_DAILY_COLUMNS,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_INPUT_MANIFEST_BYTES,
    MAX_ROWS,
    MAX_SESSIONS,
    MAX_SYMBOLS,
    MAX_TOTAL_INPUT_BYTES,
)


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_RELATIVE_IDENTIFIER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,238}[A-Za-z0-9])?")
_MANIFEST_JSON_LIMITS = BoundedJsonLimits(
    max_bytes=MAX_INPUT_MANIFEST_BYTES,
    max_depth=12,
    max_list_items=2_100,
    max_object_keys=64,
    max_text_codepoints=4_096,
    max_text_bytes=16_384,
    max_number_characters=32,
)


class KISQualityInputError(ValueError):
    """입력 snapshot의 path/hash/schema/cap 오류를 로컬 경로나 원문 없이 보고한다."""


@dataclass(frozen=True)
class QualityReadLimits:
    max_symbols: int = MAX_SYMBOLS
    max_sessions: int = MAX_SESSIONS
    max_files: int = MAX_FILES
    max_rows: int = MAX_ROWS
    max_file_bytes: int = MAX_FILE_BYTES
    max_total_bytes: int = MAX_TOTAL_INPUT_BYTES
    batch_rows: int = 8_192

    def __post_init__(self) -> None:
        pairs = (
            (self.max_symbols, MAX_SYMBOLS),
            (self.max_sessions, MAX_SESSIONS),
            (self.max_files, MAX_FILES),
            (self.max_rows, MAX_ROWS),
            (self.max_file_bytes, MAX_FILE_BYTES),
            (self.max_total_bytes, MAX_TOTAL_INPUT_BYTES),
        )
        if any(value <= 0 or value > hard_cap for value, hard_cap in pairs):
            raise ValueError("quality read limit must be positive and no higher than its hard cap")
        if self.batch_rows <= 0 or self.batch_rows > 65_536:
            raise ValueError("quality batch row limit is invalid")


@dataclass(frozen=True)
class LoadedQualitySnapshot:
    context: AnalysisContext
    datasets: tuple[SymbolDataset, ...]


@dataclass(frozen=True)
class CompletedSessions:
    sessions: tuple[date, ...]
    expected_last_completed_session: date


def load_quality_snapshot(
    *,
    root: Path,
    universe_identifier: str,
    dataset_identifier: str,
    collection_identifier: str | None,
    window_start: date,
    window_end: date,
    evaluated_at: datetime,
    software_revision: str,
    limits: QualityReadLimits | None = None,
    deadline_check: Callable[[], None] | None = None,
) -> LoadedQualitySnapshot:
    """shared lock 안에서 successful manifest와 exact Parquet bytes를 모두 재검증해 읽는다.

    이 adapter는 HTTP/provider client를 import하지 않으며 bytes를 immutable Python records로 투영한 뒤
    lock을 해제하므로 metric core가 mutable Parquet을 다시 열지 않는다.
    """
    active_limits = limits or QualityReadLimits()
    check_deadline = deadline_check or _no_deadline
    root_path = _absolute_root(root)
    try:
        check_deadline()
        with dataset_lock(root_path, exclusive=False):
            dataset_bytes, _ = _read_regular_relative(
                root_path,
                dataset_identifier,
                max_bytes=MAX_INPUT_MANIFEST_BYTES,
                deadline_check=check_deadline,
            )
            dataset_manifest = _parse_dataset_manifest(dataset_bytes)
            _verify_dataset_identifier(dataset_identifier, dataset_manifest)
            if dataset_manifest.file_count > active_limits.max_files:
                raise KISQualityInputError("dataset file limit exceeded")
            if dataset_manifest.row_count > active_limits.max_rows:
                raise KISQualityInputError("dataset row limit exceeded")

            universe_bytes, _ = _read_regular_relative(
                root_path,
                universe_identifier,
                max_bytes=MAX_INPUT_MANIFEST_BYTES,
                deadline_check=check_deadline,
            )
            universe_reference = ManifestReference(
                identifier=universe_identifier,
                sha256=hashlib.sha256(universe_bytes).hexdigest(),
            )
            if universe_reference != _manifest_reference(dataset_manifest.universe_manifest):
                raise KISQualityInputError("input artifact provenance did not match")
            symbols = _parse_universe_symbols(universe_bytes, active_limits.max_symbols)
            if set(symbols) != {item.symbol for item in dataset_manifest.files}:
                raise KISQualityInputError("dataset inventory did not match universe")

            collection_reference: ManifestReference | None = None
            collection_summary: CollectionRunSummary | None = None
            if collection_identifier is not None:
                collection_bytes, _ = _read_regular_relative(
                    root_path,
                    collection_identifier,
                    max_bytes=MAX_INPUT_MANIFEST_BYTES,
                    deadline_check=check_deadline,
                )
                collection_reference = ManifestReference(
                    identifier=collection_identifier,
                    sha256=hashlib.sha256(collection_bytes).hexdigest(),
                )
                if collection_reference != _manifest_reference(dataset_manifest.collection_run):
                    raise KISQualityInputError("input artifact provenance did not match")
                collection_summary = _parse_collection_summary(collection_bytes)
                _verify_collection_identifier(collection_identifier, collection_summary)

            calendar = completed_xkrx_sessions(
                window_start=window_start,
                window_end=window_end,
                evaluated_at=evaluated_at,
            )
            check_deadline()
            if len(calendar.sessions) > active_limits.max_sessions:
                raise KISQualityInputError("XKRX session limit exceeded")
            expected_names = {f"{item.symbol}.parquet" for item in dataset_manifest.files}
            if _daily_parquet_names(root_path) != expected_names:
                raise KISQualityInputError("dataset inventory did not match manifest")

            if sum(item.byte_size for item in dataset_manifest.files) > active_limits.max_total_bytes:
                raise KISQualityInputError("dataset total byte limit exceeded")
            datasets: list[SymbolDataset] = []
            total_rows = 0
            for inventory in dataset_manifest.files:
                check_deadline()
                if inventory.byte_size > active_limits.max_file_bytes:
                    raise KISQualityInputError("dataset file byte limit exceeded")
                content, metadata = _read_regular_relative(
                    root_path,
                    inventory.path,
                    max_bytes=active_limits.max_file_bytes,
                    deadline_check=check_deadline,
                )
                if (
                    metadata.st_size != inventory.byte_size
                    or hashlib.sha256(content).hexdigest() != inventory.sha256
                ):
                    raise KISQualityInputError("dataset inventory hash did not match")
                dataset = _read_parquet_dataset(
                    content,
                    inventory,
                    batch_rows=active_limits.batch_rows,
                    deadline_check=check_deadline,
                )
                total_rows += len(dataset.rows)
                if total_rows > active_limits.max_rows:
                    raise KISQualityInputError("dataset row limit exceeded")
                datasets.append(dataset)

            dataset_reference = ManifestReference(
                identifier=dataset_identifier,
                sha256=hashlib.sha256(dataset_bytes).hexdigest(),
            )
            context = AnalysisContext(
                evaluated_at=evaluated_at,
                software_revision=software_revision,
                window_start=window_start,
                window_end=window_end,
                expected_last_completed_xkrx_session=(
                    calendar.expected_last_completed_session
                ),
                sessions=calendar.sessions,
                universe_symbols=tuple(symbols),
                universe_manifest=universe_reference,
                dataset_manifest=dataset_reference,
                collection_run=collection_reference,
                collection_summary=collection_summary,
                dataset_file_count=dataset_manifest.file_count,
            )
            return LoadedQualitySnapshot(context=context, datasets=tuple(datasets))
    except KISQualityInputError:
        raise
    except (BoundedJsonError, ValidationError, ValueError, OSError, OverflowError):
        raise KISQualityInputError("quality input validation failed") from None


def completed_xkrx_sessions(
    *,
    window_start: date,
    window_end: date,
    evaluated_at: datetime,
) -> CompletedSessions:
    """evaluatedAt까지 실제 close가 끝난 XKRX session만 양 끝 포함 window로 반환한다."""
    if window_start > window_end:
        raise ValueError("analysis window is invalid")
    if evaluated_at.tzinfo is None:
        raise ValueError("evaluatedAt must be timezone-aware")
    calendar: Any = xcals.get_calendar("XKRX")
    labels = calendar.sessions_in_range(pd.Timestamp(window_start), pd.Timestamp(window_end))
    completed = tuple(
        label.date()
        for label in labels
        if calendar.session_close(label).to_pydatetime() <= evaluated_at
    )
    if not completed:
        raise ValueError("analysis window contains no completed XKRX session")
    return CompletedSessions(
        sessions=completed,
        expected_last_completed_session=completed[-1],
    )


def _parse_dataset_manifest(content: bytes) -> SuccessfulDatasetManifest:
    payload = parse_bounded_json_bytes(content, limits=_MANIFEST_JSON_LIMITS)
    return SuccessfulDatasetManifest.model_validate(payload)


def _parse_collection_summary(content: bytes) -> CollectionRunSummary:
    payload = parse_bounded_json_bytes(content, limits=_MANIFEST_JSON_LIMITS)
    return CollectionRunSummary.model_validate(payload)


def _parse_universe_symbols(content: bytes, max_symbols: int) -> tuple[str, ...]:
    payload = parse_bounded_json_bytes(content, limits=_MANIFEST_JSON_LIMITS)
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise KISQualityInputError("universe manifest schema was invalid")
    raw_symbols = payload.get("symbols")
    if not isinstance(raw_symbols, list) or not 1 <= len(raw_symbols) <= max_symbols:
        raise KISQualityInputError("universe symbol limit was invalid")
    symbols: list[str] = []
    for item in raw_symbols:
        if not isinstance(item, dict):
            raise KISQualityInputError("universe symbol entry was invalid")
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit():
            raise KISQualityInputError("universe symbol entry was invalid")
        symbols.append(symbol)
    if len(set(symbols)) != len(symbols):
        raise KISQualityInputError("universe symbols were duplicated")
    return tuple(symbols)


def _read_parquet_dataset(
    content: bytes,
    inventory: DatasetFileInventory,
    *,
    batch_rows: int,
    deadline_check: Callable[[], None],
) -> SymbolDataset:
    try:
        parquet = pq.ParquetFile(BytesIO(content))  # type: ignore[no-untyped-call]
        if tuple(parquet.schema_arrow.names) != CANONICAL_DAILY_COLUMNS:
            raise KISQualityInputError("dataset Parquet schema did not match")
        if parquet.metadata.num_rows != inventory.row_count:
            raise KISQualityInputError("dataset inventory row count did not match")
        rows: list[Mapping[str, object]] = []
        observed_dates: list[date] = []
        for batch in parquet.iter_batches(  # type: ignore[no-untyped-call]
            batch_size=batch_rows,
            columns=list(CANONICAL_DAILY_COLUMNS),
        ):
            deadline_check()
            columns = batch.to_pydict()
            for index in range(batch.num_rows):
                row = {name: columns[name][index] for name in CANONICAL_DAILY_COLUMNS}
                raw_date = row["date"]
                if isinstance(raw_date, datetime):
                    row["date"] = raw_date.date()
                if row["symbol"] != inventory.symbol or type(row["date"]) is not date:
                    raise KISQualityInputError("dataset row identity did not match")
                observed_dates.append(row["date"])
                rows.append(row)
        observed_min = min(observed_dates) if observed_dates else None
        observed_max = max(observed_dates) if observed_dates else None
        if observed_min != inventory.min_date or observed_max != inventory.max_date:
            raise KISQualityInputError("dataset inventory date range did not match")
        return SymbolDataset(
            symbol=inventory.symbol,
            columns=CANONICAL_DAILY_COLUMNS,
            rows=tuple(rows),
        )
    except KISQualityInputError:
        raise
    except (OSError, ValueError, TypeError, OverflowError):
        raise KISQualityInputError("dataset Parquet was invalid") from None


def _manifest_reference(value: Any) -> ManifestReference:
    return ManifestReference(identifier=value.identifier, sha256=value.sha256)


def _verify_dataset_identifier(
    identifier: str,
    manifest: SuccessfulDatasetManifest,
) -> None:
    expected = (
        f"datasets/{manifest.created_at:%Y/%m/%d}/"
        f"{manifest.dataset_manifest_id}/manifest.json"
    )
    if identifier != expected:
        raise KISQualityInputError("dataset manifest identity did not match")


def _verify_collection_identifier(
    identifier: str,
    summary: CollectionRunSummary,
) -> None:
    completed_at = summary.completed_at.astimezone(UTC)
    expected = (
        f"collection-runs/{completed_at:%Y/%m/%d}/"
        f"{summary.collection_run_id}/summary.json"
    )
    if identifier != expected:
        raise KISQualityInputError("collection run identity did not match")


def _daily_parquet_names(root: Path) -> set[str]:
    root_fd = _open_absolute_tree(root)
    try:
        try:
            daily_fd = os.open("daily", _DIRECTORY_FLAGS, dir_fd=root_fd)
        except OSError:
            raise KISQualityInputError("dataset inventory directory was invalid") from None
    finally:
        os.close(root_fd)
    try:
        return {name for name in os.listdir(daily_fd) if name.endswith(".parquet")}
    finally:
        os.close(daily_fd)


def _read_regular_relative(
    root: Path,
    identifier: str,
    *,
    max_bytes: int,
    deadline_check: Callable[[], None],
) -> tuple[bytes, os.stat_result]:
    components = _validated_components(identifier)
    root_fd = _open_absolute_tree(root)
    try:
        parent_fd = _open_existing_children(root_fd, components[:-1])
    finally:
        os.close(root_fd)
    file_fd = -1
    try:
        try:
            file_fd = os.open(
                components[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                dir_fd=parent_fd,
            )
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
                raise KISQualityInputError("input artifact path was invalid") from None
            raise KISQualityInputError("input artifact was unavailable") from None
        metadata = os.fstat(file_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o777 != 0o600
        ):
            raise KISQualityInputError("input artifact regular single-link mode failed")
        if metadata.st_size > max_bytes:
            raise KISQualityInputError("input artifact byte limit exceeded")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            deadline_check()
            chunk = os.read(file_fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > max_bytes:
            raise KISQualityInputError("input artifact byte limit exceeded")
        if len(content) != metadata.st_size:
            raise KISQualityInputError("input artifact changed during read")
        return content, metadata
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        os.close(parent_fd)


def _validated_components(identifier: str) -> tuple[str, ...]:
    if (
        _RELATIVE_IDENTIFIER.fullmatch(identifier) is None
        or identifier.startswith("/")
        or identifier.endswith("/")
        or "//" in identifier
        or "\\" in identifier
    ):
        raise KISQualityInputError("input artifact identifier was invalid")
    raw_components = tuple(identifier.split("/"))
    if any(item in {"", ".", ".."} for item in raw_components):
        raise KISQualityInputError("input artifact identifier was invalid")
    components = tuple(PurePosixPath(identifier).parts)
    if not components or components != raw_components:
        raise KISQualityInputError("input artifact identifier was invalid")
    return components


def _absolute_root(root: Path) -> Path:
    expanded = root.expanduser()
    if ".." in expanded.parts:
        raise KISQualityInputError("quality input root was invalid")
    absolute = expanded if expanded.is_absolute() else Path.cwd() / expanded
    if absolute.anchor != "/":
        raise KISQualityInputError("quality input root was invalid")
    return absolute


def _open_absolute_tree(path: Path) -> int:
    current_fd = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in path.parts[1:]:
            try:
                next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current_fd)
            except OSError:
                raise KISQualityInputError("input artifact root path was invalid") from None
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _open_existing_children(parent_fd: int, components: tuple[str, ...]) -> int:
    current_fd = os.dup(parent_fd)
    try:
        for component in components:
            try:
                next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current_fd)
            except OSError:
                raise KISQualityInputError("input artifact path was invalid") from None
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _no_deadline() -> None:
    return None
