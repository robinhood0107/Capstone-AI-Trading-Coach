from __future__ import annotations

import ctypes
from dataclasses import dataclass
from datetime import UTC, datetime
import errno
import hashlib
import os
from pathlib import Path
import secrets
import stat
from collections.abc import Callable
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.quality.models import KISDataQualityReport
from app.data.quality.policy import (
    MAX_BUNDLE_MANIFEST_BYTES,
    MAX_REPORT_JSON_BYTES,
    MAX_REPORT_MARKDOWN_BYTES,
)
from app.data.quality.report import render_markdown, report_json_bytes


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_BUNDLE_FILES = ("report.json", "report.md", "manifest.json")


class QualityBundleStorageError(ValueError):
    """bundle filesystem 불변식 위반을 로컬 절대경로나 OS 원문 없이 보고한다."""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class BundleReportFile(_FrozenModel):
    name: Literal["report.json", "report.md"]
    byte_size: StrictInt = Field(alias="byteSize", ge=1, le=MAX_REPORT_JSON_BYTES)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def _validate_name_specific_cap(self) -> "BundleReportFile":
        cap = MAX_REPORT_JSON_BYTES if self.name == "report.json" else MAX_REPORT_MARKDOWN_BYTES
        if self.byte_size > cap:
            raise ValueError("bundle report file exceeded its size limit")
        return self


class QualityBundleManifest(_FrozenModel):
    """report JSON/Markdown exact bytes를 소유하는 immutable completion marker다."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    report_id: UUID = Field(alias="reportId")
    analysis_fingerprint: str = Field(alias="analysisFingerprint", pattern=r"^[a-f0-9]{64}$")
    created_at: datetime = Field(alias="createdAt")
    bundle_path: str = Field(
        alias="bundlePath",
        pattern=(
            r"^quality/[0-9]{4}/[0-9]{2}/[0-9]{2}/"
            r"[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        ),
    )
    files: tuple[BundleReportFile, ...]
    status: Literal["COMPLETE"] = "COMPLETE"

    @field_validator("report_id")
    @classmethod
    def _require_uuid5(cls, value: UUID) -> UUID:
        if value.version != 5:
            raise ValueError("bundle reportId must be UUIDv5")
        return value

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("bundle createdAt must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _validate_file_order(self) -> "QualityBundleManifest":
        if tuple(item.name for item in self.files) != ("report.json", "report.md"):
            raise ValueError("bundle files must use the canonical order")
        return self


@dataclass(frozen=True)
class PublishedQualityBundle:
    bundle_path: Path
    bundle_identifier: str
    manifest_path: Path
    manifest: QualityBundleManifest
    created: bool


def publish_quality_bundle(
    root: Path,
    report: KISDataQualityReport,
    *,
    deadline_check: Callable[[], None] | None = None,
) -> PublishedQualityBundle:
    """complete temp directory를 durable write한 뒤 final rename하고 latest를 마지막에 교체한다.

    같은 content identity는 existing bytes/mode/link를 전부 재검증한 no-op만 허용하며, 손상된 같은
    reportId는 덮어쓰지 않는다.
    """
    check_deadline = deadline_check or _no_deadline
    _run_deadline_check(check_deadline)
    json_content = report_json_bytes(report)
    markdown_content = render_markdown(report)
    _run_deadline_check(check_deadline)
    day = report.evaluated_at.astimezone(UTC).date()
    bundle_identifier = f"quality/{day:%Y/%m/%d}/{report.report_id}"
    manifest = QualityBundleManifest(
        reportId=report.report_id,
        analysisFingerprint=report.analysis_fingerprint,
        createdAt=report.evaluated_at,
        bundlePath=bundle_identifier,
        files=(
            BundleReportFile(
                name="report.json",
                byteSize=len(json_content),
                sha256=hashlib.sha256(json_content).hexdigest(),
            ),
            BundleReportFile(
                name="report.md",
                byteSize=len(markdown_content),
                sha256=hashlib.sha256(markdown_content).hexdigest(),
            ),
        ),
    )
    manifest_content = canonical_json_bytes(manifest.model_dump(mode="json", by_alias=True))
    if len(manifest_content) > MAX_BUNDLE_MANIFEST_BYTES:
        raise QualityBundleStorageError("bundle manifest exceeded the size limit")
    expected = {
        "report.json": json_content,
        "report.md": markdown_content,
        "manifest.json": manifest_content,
    }
    root_path = _absolute_root(root)
    root_fd = _open_or_create_absolute_tree(root_path)
    quality_fd = -1
    day_fd = -1
    temporary: str | None = None
    created = False
    try:
        _run_deadline_check(check_deadline)
        quality_fd = _open_or_create_output_child(root_fd, "quality")
        day_fd = _open_or_create_output_path(
            quality_fd,
            (f"{day:%Y}", f"{day:%m}", f"{day:%d}"),
        )
        final_name = str(report.report_id)
        if _existing_bundle_state(day_fd, final_name, expected):
            _run_deadline_check(check_deadline)
            _publish_latest(quality_fd, manifest_content, check_deadline)
            return _published_result(
                root_path,
                bundle_identifier,
                manifest,
                created=False,
            )

        temporary = f".quality-{secrets.token_hex(16)}.tmp"
        _create_private_directory(day_fd, temporary)
        temporary_fd = os.open(temporary, _DIRECTORY_FLAGS, dir_fd=day_fd)
        try:
            for filename in _BUNDLE_FILES:
                _run_deadline_check(check_deadline)
                _write_file(temporary_fd, filename, expected[filename])
            os.fsync(temporary_fd)
        finally:
            os.close(temporary_fd)
        try:
            _run_deadline_check(check_deadline)
            _rename_no_replace(
                day_fd,
                temporary,
                day_fd,
                final_name,
            )
            temporary = None
            created = True
            os.fsync(day_fd)
            _run_deadline_check(check_deadline)
        except OSError as error:
            if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                raise
            assert temporary is not None
            _cleanup_temporary(day_fd, temporary)
            temporary = None
            if not _existing_bundle_state(day_fd, final_name, expected):
                raise QualityBundleStorageError("existing bundle was invalid") from None
        _run_deadline_check(check_deadline)
        _publish_latest(quality_fd, manifest_content, check_deadline)
        return _published_result(
            root_path,
            bundle_identifier,
            manifest,
            created=created,
        )
    except QualityBundleStorageError:
        if day_fd >= 0 and temporary is not None:
            _cleanup_temporary(day_fd, temporary)
        raise
    except (OSError, ValueError):
        if day_fd >= 0 and temporary is not None:
            _cleanup_temporary(day_fd, temporary)
        raise QualityBundleStorageError("quality bundle publish failed") from None
    finally:
        if day_fd >= 0:
            os.close(day_fd)
        if quality_fd >= 0:
            os.close(quality_fd)
        os.close(root_fd)


def _published_result(
    root: Path,
    identifier: str,
    manifest: QualityBundleManifest,
    *,
    created: bool,
) -> PublishedQualityBundle:
    bundle_path = root.joinpath(*identifier.split("/"))
    return PublishedQualityBundle(
        bundle_path=bundle_path,
        bundle_identifier=identifier,
        manifest_path=bundle_path / "manifest.json",
        manifest=manifest,
        created=created,
    )


def _existing_bundle_state(
    day_fd: int,
    final_name: str,
    expected: dict[str, bytes],
) -> bool:
    try:
        metadata = os.stat(final_name, dir_fd=day_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError:
        raise QualityBundleStorageError("existing bundle was invalid") from None
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o777 != 0o700:
        raise QualityBundleStorageError("existing bundle was invalid")
    try:
        bundle_fd = os.open(final_name, _DIRECTORY_FLAGS, dir_fd=day_fd)
    except OSError:
        raise QualityBundleStorageError("existing bundle was invalid") from None
    try:
        if set(os.listdir(bundle_fd)) != set(_BUNDLE_FILES):
            raise QualityBundleStorageError("existing bundle was invalid")
        for filename in _BUNDLE_FILES:
            if _read_existing_file(bundle_fd, filename, len(expected[filename])) != expected[filename]:
                raise QualityBundleStorageError("existing bundle was invalid")
    finally:
        os.close(bundle_fd)
    return True


def _read_existing_file(directory_fd: int, filename: str, expected_size: int) -> bytes:
    file_fd = -1
    try:
        try:
            file_fd = os.open(
                filename,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
        except OSError:
            raise QualityBundleStorageError("existing bundle was invalid") from None
        metadata = os.fstat(file_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o777 != 0o600
            or metadata.st_size != expected_size
        ):
            raise QualityBundleStorageError("existing bundle was invalid")
        chunks: list[bytes] = []
        remaining = expected_size + 1
        while remaining > 0:
            chunk = os.read(file_fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) != expected_size:
            raise QualityBundleStorageError("existing bundle was invalid")
        return content
    finally:
        if file_fd >= 0:
            os.close(file_fd)


def _publish_latest(
    quality_fd: int,
    content: bytes,
    deadline_check: Callable[[], None],
) -> None:
    filename = "latest-manifest.json"
    try:
        metadata = os.stat(filename, dir_fd=quality_fd, follow_symlinks=False)
    except FileNotFoundError:
        metadata = None
    except OSError:
        raise QualityBundleStorageError("latest manifest target was invalid") from None
    if metadata is not None and (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o777 != 0o600
    ):
        raise QualityBundleStorageError("latest manifest target was invalid")
    temporary = f".latest-{secrets.token_hex(16)}.tmp"
    try:
        _run_deadline_check(deadline_check)
        _write_file(quality_fd, temporary, content)
        _run_deadline_check(deadline_check)
        os.replace(
            temporary,
            filename,
            src_dir_fd=quality_fd,
            dst_dir_fd=quality_fd,
        )
        os.fsync(quality_fd)
    except QualityBundleStorageError:
        raise
    except OSError:
        raise QualityBundleStorageError("latest manifest publish failed") from None
    finally:
        try:
            os.unlink(temporary, dir_fd=quality_fd)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _write_file(directory_fd: int, filename: str, content: bytes) -> None:
    file_fd = -1
    try:
        file_fd = os.open(
            filename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
        os.fchmod(file_fd, 0o600)
        with os.fdopen(file_fd, "wb") as output:
            file_fd = -1
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    finally:
        if file_fd >= 0:
            os.close(file_fd)


def _cleanup_temporary(day_fd: int, temporary: str) -> None:
    try:
        temporary_fd = os.open(temporary, _DIRECTORY_FLAGS, dir_fd=day_fd)
    except OSError:
        return
    try:
        for filename in _BUNDLE_FILES:
            try:
                os.unlink(filename, dir_fd=temporary_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass
    finally:
        os.close(temporary_fd)
    try:
        os.rmdir(temporary, dir_fd=day_fd)
    except OSError:
        pass


def _absolute_root(root: Path) -> Path:
    expanded = root.expanduser()
    if ".." in expanded.parts:
        raise QualityBundleStorageError("quality output root was invalid")
    absolute = expanded if expanded.is_absolute() else Path.cwd() / expanded
    if absolute.anchor != "/":
        raise QualityBundleStorageError("quality output root was invalid")
    return absolute


def _open_or_create_absolute_tree(path: Path) -> int:
    current_fd = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in path.parts[1:]:
            try:
                next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current_fd)
            except FileNotFoundError:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current_fd)
            except OSError:
                raise QualityBundleStorageError("quality output root path was invalid") from None
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _open_or_create_output_path(parent_fd: int, components: tuple[str, ...]) -> int:
    current_fd = os.dup(parent_fd)
    try:
        for component in components:
            next_fd = _open_or_create_output_child(current_fd, component)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _open_or_create_output_child(parent_fd: int, component: str) -> int:
    try:
        os.mkdir(component, mode=0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileExistsError:
        pass
    except OSError:
        raise QualityBundleStorageError("quality output directory was invalid") from None
    child_fd = -1
    try:
        child_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        metadata = os.fstat(child_fd)
        if not stat.S_ISDIR(metadata.st_mode):
            raise QualityBundleStorageError("quality output directory was invalid")
        os.fchmod(child_fd, 0o700)
        return child_fd
    except QualityBundleStorageError:
        if child_fd >= 0:
            os.close(child_fd)
        raise
    except OSError:
        if child_fd >= 0:
            os.close(child_fd)
        raise QualityBundleStorageError("quality output directory was invalid") from None


def _create_private_directory(parent_fd: int, name: str) -> None:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        child_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        try:
            os.fchmod(child_fd, 0o700)
        finally:
            os.close(child_fd)
        os.fsync(parent_fd)
    except OSError:
        raise QualityBundleStorageError("quality temporary directory was invalid") from None


def _rename_no_replace(
    source_directory_fd: int,
    source: str,
    target_directory_fd: int,
    target: str,
) -> None:
    """Linux renameat2 NOREPLACE로 race 중 생긴 빈 target directory도 덮어쓰지 않는다."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        source_directory_fd,
        os.fsencode(source),
        target_directory_fd,
        os.fsencode(target),
        1,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, "renameat2 failed")


def _run_deadline_check(deadline_check: Callable[[], None]) -> None:
    try:
        deadline_check()
    except ValueError:
        raise QualityBundleStorageError("quality bundle publish failed") from None


def _no_deadline() -> None:
    return None
