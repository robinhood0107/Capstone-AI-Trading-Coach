from __future__ import annotations

import ctypes
import hashlib
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class OwnerFileIoError(ValueError):
    """owner file handle이 path/reparse/hardlink/race/size 경계를 위반했음을 나타낸다."""


@dataclass(frozen=True, slots=True)
class OwnerReadResult:
    """경로 projection 없이 regular owner file의 bounded bytes와 hash만 전달한다."""

    content: bytes
    content_sha256: str


def read_owner_regular_file(
    *,
    approved_root: Path,
    relative_path: str,
    max_bytes: int,
) -> OwnerReadResult:
    """POSIX descriptor 또는 Windows no-reparse handle로 owner file을 read-only로 읽는다."""

    if os.name == "nt":
        return _read_windows_regular_file(
            approved_root=approved_root,
            relative_path=relative_path,
            max_bytes=max_bytes,
        )
    return _read_posix_regular_file(
        approved_root=approved_root,
        relative_path=relative_path,
        max_bytes=max_bytes,
    )


def _read_posix_regular_file(
    *,
    approved_root: Path,
    relative_path: str,
    max_bytes: int,
) -> OwnerReadResult:
    # Windows에서 POSIX 전용 os flag를 module import 시 평가하지 않도록 lazy import한다.
    from app.rag.safe_io import RagSafeIoError, read_approved_regular_file

    try:
        result = read_approved_regular_file(
            approved_root=approved_root,
            relative_path=relative_path,
            max_bytes=max_bytes,
        )
    except RagSafeIoError as error:
        raise OwnerFileIoError("OWNER_FILE_UNSAFE") from error
    return OwnerReadResult(content=result.content, content_sha256=result.content_sha256)


def _validate_relative_path(value: str) -> tuple[str, ...]:
    if (
        not value
        or value.startswith("/")
        or value.endswith("/")
        or "\\" in value
        or "//" in value
        or "\x00" in value
    ):
        raise OwnerFileIoError("OWNER_FILE_UNSAFE")
    parts = tuple(PurePosixPath(value).parts)
    if not parts or any(part in {"", ".", ".."} or ":" in part for part in parts):
        raise OwnerFileIoError("OWNER_FILE_UNSAFE")
    return parts


class _FileTime(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("file_attributes", wintypes.DWORD),
        ("creation_time", _FileTime),
        ("last_access_time", _FileTime),
        ("last_write_time", _FileTime),
        ("volume_serial_number", wintypes.DWORD),
        ("file_size_high", wintypes.DWORD),
        ("file_size_low", wintypes.DWORD),
        ("number_of_links", wintypes.DWORD),
        ("file_index_high", wintypes.DWORD),
        ("file_index_low", wintypes.DWORD),
    ]


def _read_windows_regular_file(
    *,
    approved_root: Path,
    relative_path: str,
    max_bytes: int,
) -> OwnerReadResult:
    if max_bytes <= 0 or not approved_root.is_absolute():
        raise OwnerFileIoError("OWNER_FILE_UNSAFE")
    parts = _validate_relative_path(relative_path)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    root_handle = _windows_open_handle(create_file, str(approved_root), directory=True)
    file_handle: int | None = None
    try:
        root_info = _windows_file_info(kernel32, root_handle)
        if not root_info.file_attributes & 0x10 or root_info.file_attributes & 0x400:
            raise OwnerFileIoError("OWNER_FILE_UNSAFE")
        root_final = _windows_final_path(kernel32, root_handle)
        target = approved_root.joinpath(*parts)
        file_handle = _windows_open_handle(create_file, str(target), directory=False)
        before = _windows_file_info(kernel32, file_handle)
        if (
            before.file_attributes & (0x10 | 0x400)
            or before.number_of_links != 1
            or _windows_file_size(before) <= 0
            or _windows_file_size(before) > max_bytes
        ):
            raise OwnerFileIoError("OWNER_FILE_UNSAFE")
        leaf_final = _windows_final_path(kernel32, file_handle)
        if (
            os.path.commonpath((root_final.casefold(), leaf_final.casefold()))
            != root_final.casefold()
        ):
            raise OwnerFileIoError("OWNER_FILE_UNSAFE")
        payload = _windows_read_all(kernel32, file_handle, _windows_file_size(before), max_bytes)
        after = _windows_file_info(kernel32, file_handle)
        if _windows_stable_info(before) != _windows_stable_info(after):
            raise OwnerFileIoError("OWNER_FILE_RACE")
        return OwnerReadResult(
            content=payload,
            content_sha256=hashlib.sha256(payload).hexdigest(),
        )
    except OwnerFileIoError:
        raise
    except OSError as error:
        raise OwnerFileIoError("OWNER_FILE_UNSAFE") from error
    finally:
        if file_handle is not None:
            close_handle(file_handle)
        close_handle(root_handle)


def _windows_open_handle(create_file: object, path: str, *, directory: bool) -> int:
    flags = 0x00200000  # FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= 0x02000000  # FILE_FLAG_BACKUP_SEMANTICS
    handle = create_file(  # type: ignore[operator]
        path,
        0x80000000,  # GENERIC_READ
        0x00000001,  # FILE_SHARE_READ
        None,
        3,  # OPEN_EXISTING
        flags,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in {None, invalid}:
        raise OwnerFileIoError("OWNER_FILE_UNSAFE")
    return int(handle)


def _windows_file_info(kernel32: object, handle: int) -> _ByHandleFileInformation:
    info = _ByHandleFileInformation()
    get_info = kernel32.GetFileInformationByHandle  # type: ignore[attr-defined]
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]
    get_info.restype = wintypes.BOOL
    if not get_info(handle, ctypes.byref(info)):
        raise OwnerFileIoError("OWNER_FILE_UNSAFE")
    return info


def _windows_final_path(kernel32: object, handle: int) -> str:
    get_path = kernel32.GetFinalPathNameByHandleW  # type: ignore[attr-defined]
    get_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    get_path.restype = wintypes.DWORD
    required = get_path(handle, None, 0, 0)
    if required <= 0 or required > 32768:
        raise OwnerFileIoError("OWNER_FILE_UNSAFE")
    buffer = ctypes.create_unicode_buffer(required + 1)
    written = get_path(handle, buffer, required + 1, 0)
    if written <= 0 or written > required:
        raise OwnerFileIoError("OWNER_FILE_UNSAFE")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normpath(value)


def _windows_file_size(value: _ByHandleFileInformation) -> int:
    return (int(value.file_size_high) << 32) | int(value.file_size_low)


def _windows_stable_info(value: _ByHandleFileInformation) -> tuple[int, ...]:
    return (
        int(value.file_attributes),
        int(value.volume_serial_number),
        int(value.file_size_high),
        int(value.file_size_low),
        int(value.number_of_links),
        int(value.file_index_high),
        int(value.file_index_low),
        int(value.last_write_time.high),
        int(value.last_write_time.low),
    )


def _windows_read_all(kernel32: object, handle: int, expected_size: int, max_bytes: int) -> bytes:
    read_file = kernel32.ReadFile  # type: ignore[attr-defined]
    read_file.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    read_file.restype = wintypes.BOOL
    chunks: list[bytes] = []
    total = 0
    while total < expected_size:
        requested = min(65_536, expected_size - total, max_bytes + 1 - total)
        if requested <= 0:
            raise OwnerFileIoError("OWNER_FILE_UNSAFE")
        buffer = ctypes.create_string_buffer(requested)
        received = wintypes.DWORD()
        if not read_file(handle, buffer, requested, ctypes.byref(received), None):
            raise OwnerFileIoError("OWNER_FILE_UNSAFE")
        if received.value == 0:
            break
        chunks.append(buffer.raw[: received.value])
        total += received.value
    payload = b"".join(chunks)
    if len(payload) != expected_size or len(payload) > max_bytes:
        raise OwnerFileIoError("OWNER_FILE_UNSAFE")
    return payload
