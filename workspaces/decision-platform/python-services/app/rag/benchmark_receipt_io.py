from __future__ import annotations

import errno
import hashlib
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_SHARED_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH
_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,126}\.json$")
_MAX_RECEIPT_BYTES = 16 * 1024 * 1024


class BenchmarkReceiptIoError(ValueError):
    """OCR benchmark output이 approved root의 안전한 파일 경계를 벗어났음을 나타낸다."""


@dataclass(frozen=True, slots=True)
class BenchmarkReceiptWrite:
    """원문 경로를 포함하지 않는 benchmark receipt write 결과다."""

    bytes_written: int
    payload_sha256: str


def write_benchmark_receipt(
    *,
    approved_root: Path,
    relative_directory: str,
    filename: str,
    payload: bytes,
) -> BenchmarkReceiptWrite:
    """dirfd와 O_NOFOLLOW로 benchmark JSON receipt를 same-directory replace한다.

    root와 leaf 사이의 directory도 descriptor 기준으로 생성·검증하므로 사전에 심은
    symlink나 writable alias가 repository 밖의 파일로 결과를 유도할 수 없다.
    """

    components = _validate_components(relative_directory)
    if _FILENAME.fullmatch(filename) is None or filename.startswith("."):
        raise BenchmarkReceiptIoError("OCR_BENCHMARK_OUTPUT_INVALID")
    if not isinstance(payload, bytes) or not 0 < len(payload) <= _MAX_RECEIPT_BYTES:
        raise BenchmarkReceiptIoError("OCR_BENCHMARK_OUTPUT_INVALID")
    root_fd = _open_root(approved_root)
    try:
        directory_fd = _open_or_create_directories(root_fd, components)
    finally:
        os.close(root_fd)
    try:
        _validate_existing_leaf(directory_fd, filename)
        _atomic_replace(directory_fd, filename, payload)
    finally:
        os.close(directory_fd)
    return BenchmarkReceiptWrite(
        bytes_written=len(payload),
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _validate_components(value: str) -> tuple[str, ...]:
    if not value or value.startswith("/") or value.endswith("/") or "\\" in value:
        raise BenchmarkReceiptIoError("OCR_BENCHMARK_OUTPUT_INVALID")
    components = tuple(value.split("/"))
    if any(_COMPONENT.fullmatch(component) is None for component in components):
        raise BenchmarkReceiptIoError("OCR_BENCHMARK_OUTPUT_INVALID")
    return components


def _open_root(root: Path) -> int:
    if not root.is_absolute() or ".." in root.parts:
        raise BenchmarkReceiptIoError("OCR_BENCHMARK_OUTPUT_INVALID")
    descriptor = -1
    try:
        descriptor = os.open(root, _DIRECTORY_FLAGS)
        _require_safe_directory(descriptor)
        return descriptor
    except (OSError, BenchmarkReceiptIoError):
        if descriptor >= 0:
            os.close(descriptor)
        raise BenchmarkReceiptIoError("OCR_BENCHMARK_OUTPUT_UNSAFE") from None


def _open_or_create_directories(root_fd: int, components: tuple[str, ...]) -> int:
    current_fd = os.dup(root_fd)
    try:
        for component in components:
            try:
                os.mkdir(component, mode=0o700, dir_fd=current_fd)
            except FileExistsError:
                pass
            except OSError as error:
                if error.errno not in {errno.EEXIST}:
                    raise BenchmarkReceiptIoError("OCR_BENCHMARK_OUTPUT_UNSAFE") from None
            next_fd = -1
            try:
                next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current_fd)
                _require_safe_directory(next_fd)
            except (OSError, BenchmarkReceiptIoError):
                if next_fd >= 0:
                    os.close(next_fd)
                raise BenchmarkReceiptIoError("OCR_BENCHMARK_OUTPUT_UNSAFE") from None
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _require_safe_directory(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & _SHARED_WRITE_BITS
    ):
        raise BenchmarkReceiptIoError("OCR_BENCHMARK_OUTPUT_UNSAFE")


def _validate_existing_leaf(directory_fd: int, filename: str) -> None:
    descriptor = -1
    try:
        descriptor = os.open(filename, _READ_FLAGS, dir_fd=directory_fd)
    except FileNotFoundError:
        return
    except OSError:
        raise BenchmarkReceiptIoError("OCR_BENCHMARK_OUTPUT_UNSAFE") from None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & _SHARED_WRITE_BITS
        ):
            raise BenchmarkReceiptIoError("OCR_BENCHMARK_OUTPUT_UNSAFE")
    finally:
        os.close(descriptor)


def _atomic_replace(directory_fd: int, filename: str, payload: bytes) -> None:
    temporary = f".{filename}.tmp-{secrets.token_hex(12)}"
    descriptor = -1
    published = False
    try:
        descriptor = os.open(
            temporary,
            _WRITE_FLAGS,
            0o600,
            dir_fd=directory_fd,
        )
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise BenchmarkReceiptIoError("OCR_BENCHMARK_OUTPUT_UNSAFE")
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary,
            filename,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        published = True
        _validate_existing_leaf(directory_fd, filename)
        os.fsync(directory_fd)
    except (OSError, BenchmarkReceiptIoError):
        raise BenchmarkReceiptIoError("OCR_BENCHMARK_OUTPUT_UNSAFE") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not published:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise BenchmarkReceiptIoError("OCR_BENCHMARK_OUTPUT_UNSAFE")
        view = view[written:]
