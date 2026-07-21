from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import errno
import hashlib
from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
from typing import Literal
from uuid import UUID

import pyarrow.parquet as pq
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.kis.accounting import CollectionRunSummary
from app.data.kis.symbols import normalize_symbol


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_PARQUET_COLUMNS = ("symbol", "date", "open", "high", "low", "close", "volume", "turnover")
_RELATIVE_IDENTIFIER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,238}[A-Za-z0-9])?")
_MAX_PARQUET_FILE_BYTES = 16 * 1024 * 1024
_MAX_TOTAL_INPUT_BYTES = 512 * 1024 * 1024
_MAX_ROWS = 2_000_000
_MAX_SYMBOLS = 500
_MAX_SUMMARY_BYTES = 256 * 1024
_MAX_MANIFEST_BYTES = 256 * 1024


class KISRunArtifactError(ValueError):
    """run artifact의 path/hash/mode 불변식 위반을 로컬 절대경로 없이 보고한다."""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class ArtifactReference(_FrozenModel):
    identifier: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("identifier")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        _validated_components(value)
        return value


class DatasetFileInventory(_FrozenModel):
    path: str
    symbol: str = Field(pattern=r"^[0-9]{6}$")
    byte_size: StrictInt = Field(alias="byteSize", ge=1, le=_MAX_PARQUET_FILE_BYTES)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    row_count: StrictInt = Field(alias="rowCount", ge=0, le=_MAX_ROWS)
    min_date: date | None = Field(alias="minDate")
    max_date: date | None = Field(alias="maxDate")
    columns: tuple[str, ...]

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        components = _validated_components(value)
        if len(components) != 2 or components[0] != "daily":
            raise ValueError("dataset file path must be canonical")
        return value

    @model_validator(mode="after")
    def _validate_inventory(self) -> "DatasetFileInventory":
        if self.path != f"daily/{self.symbol}.parquet":
            raise ValueError("dataset file path must match symbol")
        if self.columns != _PARQUET_COLUMNS:
            raise ValueError("dataset file columns must match canonical schema")
        if self.row_count == 0 and (self.min_date is not None or self.max_date is not None):
            raise ValueError("empty dataset file cannot have a date range")
        if self.row_count > 0 and (
            self.min_date is None
            or self.max_date is None
            or self.min_date > self.max_date
        ):
            raise ValueError("dataset file date range is invalid")
        return self


class SuccessfulDatasetManifest(_FrozenModel):
    """S1.5 consumer가 lock 아래에서 재검증할 exact successful dataset inventory다."""

    schema_version: StrictInt = Field(default=1, alias="schemaVersion", ge=1, le=1)
    dataset_manifest_id: UUID = Field(alias="datasetManifestId")
    created_at: datetime = Field(alias="createdAt")
    status: Literal["SUCCESS"] = "SUCCESS"
    adjustment_mode: Literal["ADJUSTED", "UNADJUSTED"] = Field(alias="adjustmentMode")
    universe_manifest: ArtifactReference = Field(alias="universeManifest")
    collection_run: ArtifactReference = Field(alias="collectionRun")
    file_count: StrictInt = Field(alias="fileCount", ge=1, le=_MAX_SYMBOLS)
    row_count: StrictInt = Field(alias="rowCount", ge=0, le=_MAX_ROWS)
    files: tuple[DatasetFileInventory, ...] = Field(min_length=1, max_length=_MAX_SYMBOLS)

    @field_validator("dataset_manifest_id")
    @classmethod
    def _require_uuid4(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("datasetManifestId must be UUIDv4")
        return value

    @field_validator("created_at")
    @classmethod
    def _require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("createdAt must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _validate_counts_and_order(self) -> "SuccessfulDatasetManifest":
        if self.file_count != len(self.files):
            raise ValueError("dataset fileCount must match files")
        if self.row_count != sum(item.row_count for item in self.files):
            raise ValueError("dataset rowCount must match files")
        symbols = tuple(item.symbol for item in self.files)
        if len(set(symbols)) != len(symbols):
            raise ValueError("dataset files must use unique symbols")
        if symbols != tuple(sorted(symbols)):
            raise ValueError("dataset files must use stable symbol ordering")
        return self


@dataclass(frozen=True)
class PublishedKISArtifact:
    path: Path
    identifier: str
    sha256: str

    @property
    def reference(self) -> ArtifactReference:
        return ArtifactReference(identifier=self.identifier, sha256=self.sha256)


def publish_collection_summary(
    root: Path,
    summary: CollectionRunSummary,
) -> PublishedKISArtifact:
    """sanitized aggregate summary를 UUID/date partition에 immutable mode 0600으로 게시한다."""
    day = summary.completed_at.astimezone(UTC).date()
    identifier = (
        f"collection-runs/{day:%Y/%m/%d}/{summary.collection_run_id}/summary.json"
    )
    content = canonical_json_bytes(summary.model_dump(mode="json", by_alias=True))
    if len(content) > _MAX_SUMMARY_BYTES:
        raise KISRunArtifactError("collection summary exceeded the size limit")
    path = _write_exclusive_relative(root, identifier, content)
    return PublishedKISArtifact(
        path=path,
        identifier=identifier,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def reference_input_artifact(root: Path, identifier: str) -> ArtifactReference:
    """allowlisted root 내부 regular single-link input의 exact SHA-256만 provenance로 반환한다."""
    content, _ = _read_regular_relative(
        root,
        identifier,
        max_bytes=_MAX_PARQUET_FILE_BYTES,
    )
    return ArtifactReference(
        identifier=identifier,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def inventory_daily_dataset(
    root: Path,
    symbols: tuple[str, ...],
) -> tuple[DatasetFileInventory, ...]:
    """daily directory의 exact symbol file set을 bounded projection으로 검증한다."""
    normalized = tuple(sorted({normalize_symbol(symbol) for symbol in symbols}))
    if not normalized or len(normalized) > _MAX_SYMBOLS or len(normalized) != len(symbols):
        raise KISRunArtifactError("dataset symbol inventory is invalid")
    root_path = _absolute_root(root)
    root_fd = _open_or_create_absolute_tree(root_path)
    try:
        daily_fd = _open_existing_children(root_fd, ("daily",))
    finally:
        os.close(root_fd)
    try:
        expected_names = {f"{symbol}.parquet" for symbol in normalized}
        actual_names = {
            name
            for name in os.listdir(daily_fd)
            if name.endswith(".parquet")
        }
    finally:
        os.close(daily_fd)

    entries = tuple(_inventory_parquet(root_path, symbol) for symbol in normalized)
    if actual_names != expected_names:
        raise KISRunArtifactError("dataset file inventory did not match symbols")
    if sum(item.byte_size for item in entries) > _MAX_TOTAL_INPUT_BYTES:
        raise KISRunArtifactError("dataset input exceeded the byte limit")
    if sum(item.row_count for item in entries) > _MAX_ROWS:
        raise KISRunArtifactError("dataset input exceeded the row limit")
    return entries


def build_dataset_manifest(
    *,
    dataset_manifest_id: UUID,
    created_at: datetime,
    adjustment_mode: Literal["ADJUSTED", "UNADJUSTED"],
    universe_manifest: ArtifactReference,
    collection_run: ArtifactReference,
    files: tuple[DatasetFileInventory, ...],
) -> SuccessfulDatasetManifest:
    """검증된 input reference와 exact inventory로만 success manifest를 구성한다."""
    return SuccessfulDatasetManifest(
        datasetManifestId=dataset_manifest_id,
        createdAt=created_at,
        adjustmentMode=adjustment_mode,
        universeManifest=universe_manifest,
        collectionRun=collection_run,
        fileCount=len(files),
        rowCount=sum(item.row_count for item in files),
        files=files,
    )


def publish_successful_dataset_manifest(
    root: Path,
    manifest: SuccessfulDatasetManifest,
) -> PublishedKISArtifact:
    """현재 files/input hash를 다시 확인한 뒤 manifest와 latest-success pointer 순으로 게시한다."""
    current_files = inventory_daily_dataset(
        root,
        tuple(item.symbol for item in manifest.files),
    )
    if current_files != manifest.files:
        raise KISRunArtifactError("dataset file hash did not match manifest")
    if reference_input_artifact(root, manifest.universe_manifest.identifier) != (
        manifest.universe_manifest
    ):
        raise KISRunArtifactError("universe manifest hash did not match")
    if reference_input_artifact(root, manifest.collection_run.identifier) != manifest.collection_run:
        raise KISRunArtifactError("collection summary hash did not match")

    day = manifest.created_at.astimezone(UTC).date()
    identifier = (
        f"datasets/{day:%Y/%m/%d}/{manifest.dataset_manifest_id}/manifest.json"
    )
    content = canonical_json_bytes(manifest.model_dump(mode="json", by_alias=True))
    if len(content) > _MAX_MANIFEST_BYTES:
        raise KISRunArtifactError("dataset manifest exceeded the size limit")
    path = _write_exclusive_relative(root, identifier, content)
    published = PublishedKISArtifact(
        path=path,
        identifier=identifier,
        sha256=hashlib.sha256(content).hexdigest(),
    )
    pointer = canonical_json_bytes(
        {
            "schemaVersion": 1,
            "datasetManifest": published.reference.model_dump(mode="json"),
        }
    )
    _atomic_replace_relative(root, "datasets/latest-success-manifest.json", pointer)
    return published


def _inventory_parquet(root: Path, symbol: str) -> DatasetFileInventory:
    identifier = f"daily/{symbol}.parquet"
    content, metadata = _read_regular_relative(
        root,
        identifier,
        max_bytes=_MAX_PARQUET_FILE_BYTES,
    )
    try:
        # 검증한 fd의 exact bytes를 파싱해 path 재개방에 따른 symlink/replace TOCTOU를 만들지 않는다.
        parquet = pq.ParquetFile(BytesIO(content))  # type: ignore[no-untyped-call]
        if tuple(parquet.schema_arrow.names) != _PARQUET_COLUMNS:
            raise KISRunArtifactError("dataset parquet schema is invalid")
        projection = parquet.read(columns=["symbol", "date"])  # type: ignore[no-untyped-call]
    except KISRunArtifactError:
        raise
    except Exception:
        raise KISRunArtifactError("dataset parquet could not be read") from None
    symbols = projection.column("symbol").to_pylist()
    raw_dates = projection.column("date").to_pylist()
    if any(value != symbol for value in symbols):
        raise KISRunArtifactError("dataset parquet symbol did not match file")
    dates: list[date] = []
    for value in raw_dates:
        if isinstance(value, datetime):
            dates.append(value.date())
        elif isinstance(value, date):
            dates.append(value)
        else:
            raise KISRunArtifactError("dataset parquet date was invalid")
    return DatasetFileInventory(
        path=identifier,
        symbol=symbol,
        byteSize=metadata.st_size,
        sha256=hashlib.sha256(content).hexdigest(),
        rowCount=parquet.metadata.num_rows,
        minDate=min(dates) if dates else None,
        maxDate=max(dates) if dates else None,
        columns=_PARQUET_COLUMNS,
    )


def _read_regular_relative(
    root: Path,
    identifier: str,
    *,
    max_bytes: int,
) -> tuple[bytes, os.stat_result]:
    components = _validated_components(identifier)
    root_fd = _open_or_create_absolute_tree(_absolute_root(root))
    try:
        parent_fd = _open_existing_children(root_fd, components[:-1])
    finally:
        os.close(root_fd)
    file_fd = -1
    try:
        try:
            file_fd = os.open(
                components[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
        except OSError:
            raise KISRunArtifactError("input artifact path is not safe") from None
        metadata = os.fstat(file_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > max_bytes
        ):
            raise KISRunArtifactError("input artifact regular single-link limit failed")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(file_fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > max_bytes:
            raise KISRunArtifactError("input artifact exceeded the byte limit")
        return content, metadata
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        os.close(parent_fd)


def _write_exclusive_relative(root: Path, identifier: str, content: bytes) -> Path:
    components = _validated_components(identifier)
    root_path = _absolute_root(root)
    root_fd = _open_or_create_absolute_tree(root_path)
    try:
        parent_fd = _open_or_create_children(root_fd, components[:-1])
    finally:
        os.close(root_fd)
    file_fd = -1
    try:
        try:
            file_fd = os.open(
                components[-1],
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=parent_fd,
            )
            os.fchmod(file_fd, 0o600)
            with os.fdopen(file_fd, "wb") as output:
                file_fd = -1
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.fsync(parent_fd)
        except FileExistsError:
            raise KISRunArtifactError("run artifact already exists") from None
        except KISRunArtifactError:
            raise
        except OSError:
            raise KISRunArtifactError("run artifact path is not safe") from None
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        os.close(parent_fd)
    return root_path.joinpath(*components)


def _atomic_replace_relative(root: Path, identifier: str, content: bytes) -> Path:
    components = _validated_components(identifier)
    root_path = _absolute_root(root)
    root_fd = _open_or_create_absolute_tree(root_path)
    try:
        parent_fd = _open_or_create_children(root_fd, components[:-1])
    finally:
        os.close(root_fd)
    temporary = f".{components[-1]}.{secrets.token_hex(16)}.tmp"
    file_fd = -1
    try:
        _reject_unsafe_existing(parent_fd, components[-1])
        file_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent_fd,
        )
        os.fchmod(file_fd, 0o600)
        with os.fdopen(file_fd, "wb") as output:
            file_fd = -1
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(
            temporary,
            components[-1],
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        os.fsync(parent_fd)
    except KISRunArtifactError:
        raise
    except OSError:
        raise KISRunArtifactError("run artifact pointer publish failed") from None
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        except OSError:
            pass
        os.close(parent_fd)
    return root_path.joinpath(*components)


def _validated_components(identifier: str) -> tuple[str, ...]:
    if (
        _RELATIVE_IDENTIFIER.fullmatch(identifier) is None
        or identifier.startswith("/")
        or identifier.endswith("/")
        or "//" in identifier
        or "\\" in identifier
    ):
        raise KISRunArtifactError("run artifact identifier is invalid")
    raw_components = tuple(identifier.split("/"))
    if any(item in {"", ".", ".."} for item in raw_components):
        raise KISRunArtifactError("run artifact identifier is invalid")
    components = tuple(PurePosixPath(identifier).parts)
    if not components or components != raw_components:
        raise KISRunArtifactError("run artifact identifier is invalid")
    return components


def _absolute_root(root: Path) -> Path:
    expanded = root.expanduser()
    if ".." in expanded.parts:
        raise KISRunArtifactError("run artifact root is not safe")
    absolute = expanded if expanded.is_absolute() else Path.cwd() / expanded
    if absolute.anchor != "/":
        raise KISRunArtifactError("run artifact root is not safe")
    return absolute


def _open_or_create_absolute_tree(path: Path) -> int:
    current_fd = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in path.parts[1:]:
            next_fd = _open_or_create_child(current_fd, component)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _open_or_create_children(parent_fd: int, components: tuple[str, ...]) -> int:
    current_fd = os.dup(parent_fd)
    try:
        for component in components:
            next_fd = _open_or_create_child(current_fd, component)
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
                raise KISRunArtifactError("input artifact path is not safe") from None
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _open_or_create_child(parent_fd: int, component: str) -> int:
    created = False
    try:
        os.mkdir(component, mode=0o700, dir_fd=parent_fd)
        created = True
        os.fsync(parent_fd)
    except FileExistsError:
        pass
    except OSError:
        raise KISRunArtifactError("run artifact directory is not safe") from None
    try:
        child_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        # /tmp 같은 기존 ancestor의 mode를 바꾸지 않고, 이 publisher가 만든 directory만 0700으로 고정한다.
        if created:
            os.fchmod(child_fd, 0o700)
        return child_fd
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise KISRunArtifactError("run artifact symlink directory is not safe") from None
        raise KISRunArtifactError("run artifact directory is not safe") from None


def _reject_unsafe_existing(directory_fd: int, filename: str) -> None:
    try:
        metadata = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError:
        raise KISRunArtifactError("run artifact pointer path is not safe") from None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise KISRunArtifactError("run artifact pointer must be a regular single-link file")
