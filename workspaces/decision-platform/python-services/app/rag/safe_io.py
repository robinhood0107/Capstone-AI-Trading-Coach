from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC


class RagSafeIoError(ValueError):
    """RAG ingest 파일 경계가 approved-root·regular-file·byte-bound 계약을 위반할 때 발생한다."""


@dataclass(frozen=True)
class SafeReadResult:
    """approved-root 내부 정규 파일 read 결과.

    content는 caller가 지정한 max_bytes 이하이고, absolute_path는 audit/debug용으로만 쓰며
    로그나 외부 API payload에 직접 싣지 않는다.
    """

    absolute_path: Path
    relative_path: str
    content: bytes


def read_approved_regular_file(
    *,
    approved_root: Path,
    relative_path: str,
    max_bytes: int,
) -> SafeReadResult:
    """directory-fd와 O_NOFOLLOW로 approved-root 아래 정규 파일만 bounded read한다.

    S4.2 ingest는 문서 parser보다 앞에서 symlink/path traversal/oversize를 잘라야 하므로
    이 함수는 MIME·schema 같은 형식별 판단을 하지 않고 저수준 filesystem 불변식만 책임진다.
    """

    if max_bytes <= 0:
        raise RagSafeIoError("RAG safe read byte bound must be positive.")
    components = _validate_relative_components(relative_path)
    root = _require_absolute_existing_root(approved_root)
    root_fd = _open_root(root)
    try:
        directory_fd = _open_parent_directory(root_fd, components[:-1])
    finally:
        os.close(root_fd)
    try:
        payload = _read_leaf(directory_fd, components[-1], max_bytes=max_bytes)
    finally:
        os.close(directory_fd)
    return SafeReadResult(
        absolute_path=root.joinpath(*components),
        relative_path=PurePosixPath(*components).as_posix(),
        content=payload,
    )


def _validate_relative_components(value: str) -> tuple[str, ...]:
    if not value or value.startswith("/") or "\\" in value or "//" in value:
        raise RagSafeIoError("RAG safe read path must be a clean relative POSIX path.")
    path = PurePosixPath(value)
    if any(component in {"", ".", ".."} for component in path.parts):
        raise RagSafeIoError("RAG safe read path must not contain dot segments.")
    return tuple(path.parts)


def _require_absolute_existing_root(root: Path) -> Path:
    expanded = root.expanduser()
    candidate = expanded if expanded.is_absolute() else Path.cwd() / expanded
    if ".." in candidate.parts or candidate.anchor != "/":
        raise RagSafeIoError("RAG approved root must be an absolute filesystem path.")
    if not candidate.exists():
        raise RagSafeIoError("RAG approved root does not exist.")
    return candidate


def _open_root(root: Path) -> int:
    try:
        return os.open(root, _DIRECTORY_FLAGS)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise RagSafeIoError("RAG approved root is not a safe directory.") from None
        raise RagSafeIoError("RAG approved root could not be opened safely.") from None


def _open_parent_directory(root_fd: int, components: tuple[str, ...]) -> int:
    current_fd = os.dup(root_fd)
    try:
        for component in components:
            try:
                next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current_fd)
            except OSError as error:
                if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
                    raise RagSafeIoError("RAG safe read parent path is not safe.") from None
                raise RagSafeIoError("RAG safe read parent path could not be opened.") from None
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _read_leaf(directory_fd: int, filename: str, *, max_bytes: int) -> bytes:
    file_fd = -1
    try:
        file_fd = os.open(filename, _FILE_FLAGS, dir_fd=directory_fd)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise RagSafeIoError("RAG safe read target must be a regular file.")
        if before.st_size <= 0 or before.st_size > max_bytes:
            raise RagSafeIoError("RAG safe read target exceeds its byte bound.")
        payload = os.read(file_fd, max_bytes + 1)
        after = os.fstat(file_fd)
        if after.st_ino != before.st_ino or after.st_size != before.st_size:
            raise RagSafeIoError("RAG safe read target changed during read.")
        if len(payload) != before.st_size or len(payload) > max_bytes:
            raise RagSafeIoError("RAG safe read target exceeded its byte bound.")
        return payload
    except RagSafeIoError:
        raise
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise RagSafeIoError("RAG safe read target is not safe.") from None
        raise RagSafeIoError("RAG safe read target could not be opened.") from None
    finally:
        if file_fd >= 0:
            os.close(file_fd)
