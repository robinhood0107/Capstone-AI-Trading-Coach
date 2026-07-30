from __future__ import annotations

import errno
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# FIFO/device leaf가 fstat 기반 regular-file 검사 전에 blocking I/O를 만들지 못하게 한다.
_FILE_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
_WRITE_FLAGS = os.O_WRONLY | os.O_TMPFILE | os.O_CLOEXEC
_SHARED_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH


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
    content_sha256: str
    device: int
    inode: int


@dataclass(frozen=True)
class SafeWriteResult:
    """approved-root 안에 no-overwrite로 publish된 새 파일의 bounded receipt."""

    absolute_path: Path
    relative_path: str
    content_sha256: str
    bytes_written: int


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
        payload, before_metadata = _read_leaf(
            directory_fd,
            components[-1],
            max_bytes=max_bytes,
        )
    finally:
        os.close(directory_fd)
    return SafeReadResult(
        absolute_path=root.joinpath(*components),
        relative_path=PurePosixPath(*components).as_posix(),
        content=payload,
        content_sha256=hashlib.sha256(payload).hexdigest(),
        device=before_metadata[0],
        inode=before_metadata[1],
    )


def write_approved_new_file(
    *,
    approved_root: Path,
    relative_path: str,
    content: bytes,
    max_bytes: int,
) -> SafeWriteResult:
    """directory-fd·anonymous inode·hard-link publish로 기존 target을 덮어쓰지 않는다.

    같은 filesystem directory 안의 `linkat` publish는 destination이 이미 있으면 원자적으로
    실패한다. 성공 뒤 file과 directory를 모두 fsync해 card/manifest receipt의 durability를
    호출자에게 넘긴다.
    """

    if max_bytes <= 0 or not isinstance(content, bytes):
        raise RagSafeIoError("RAG safe write requires bytes and a positive bound.")
    if len(content) == 0 or len(content) > max_bytes:
        raise RagSafeIoError("RAG safe write content exceeds its byte bound.")
    components = _validate_relative_components(relative_path)
    root = _require_absolute_existing_root(approved_root)
    root_fd = _open_root(root)
    try:
        directory_fd = _open_parent_directory(root_fd, components[:-1])
    finally:
        os.close(root_fd)
    try:
        _write_new_leaf(
            directory_fd,
            filename=components[-1],
            content=content,
        )
    finally:
        os.close(directory_fd)
    return SafeWriteResult(
        absolute_path=root.joinpath(*components),
        relative_path=PurePosixPath(*components).as_posix(),
        content_sha256=hashlib.sha256(content).hexdigest(),
        bytes_written=len(content),
    )


def _validate_relative_components(value: str) -> tuple[str, ...]:
    if (
        not value
        or value.startswith("/")
        or value.endswith("/")
        or "\\" in value
        or "//" in value
        or "\x00" in value
    ):
        raise RagSafeIoError("RAG safe read path must be a clean relative POSIX path.")
    raw_components = value.split("/")
    if any(component in {"", ".", ".."} for component in raw_components):
        raise RagSafeIoError("RAG safe read path must not contain dot segments.")
    path = PurePosixPath(value)
    return tuple(path.parts)


def _require_absolute_existing_root(root: Path) -> Path:
    if not root.is_absolute() or ".." in root.parts or root.anchor != "/":
        raise RagSafeIoError("RAG approved root must be an absolute filesystem path.")
    if not root.exists():
        raise RagSafeIoError("RAG approved root does not exist.")
    return root


def _open_root(root: Path) -> int:
    root_fd = -1
    try:
        root_fd = os.open(root, _DIRECTORY_FLAGS)
        _require_owned_nonwritable_directory(root_fd)
        return root_fd
    except RagSafeIoError:
        if root_fd >= 0:
            os.close(root_fd)
        raise
    except OSError as error:
        if root_fd >= 0:
            os.close(root_fd)
        if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise RagSafeIoError("RAG approved root is not a safe directory.") from None
        raise RagSafeIoError("RAG approved root could not be opened safely.") from None


def _open_parent_directory(root_fd: int, components: tuple[str, ...]) -> int:
    current_fd = os.dup(root_fd)
    try:
        for component in components:
            next_fd = -1
            try:
                next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current_fd)
                _require_owned_nonwritable_directory(next_fd)
            except RagSafeIoError:
                if next_fd >= 0:
                    os.close(next_fd)
                raise
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


def _read_leaf(
    directory_fd: int,
    filename: str,
    *,
    max_bytes: int,
) -> tuple[bytes, tuple[int, int, int, int, int, int, int, int]]:
    file_fd = -1
    try:
        file_fd = os.open(filename, _FILE_FLAGS, dir_fd=directory_fd)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise RagSafeIoError("RAG safe read target must be a regular file.")
        _require_current_owner(before)
        if before.st_mode & _SHARED_WRITE_BITS:
            raise RagSafeIoError("RAG safe read target must not be group/other writable.")
        if before.st_nlink != 1:
            raise RagSafeIoError("RAG safe read target must not be a hard link.")
        if before.st_size <= 0 or before.st_size > max_bytes:
            raise RagSafeIoError("RAG safe read target exceeds its byte bound.")
        chunks: list[bytes] = []
        total = 0
        while total <= max_bytes:
            chunk = os.read(file_fd, min(65536, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(file_fd)
        before_metadata = _stable_metadata(before)
        after_metadata = _stable_metadata(after)
        if after_metadata != before_metadata:
            raise RagSafeIoError("RAG safe read target changed during read.")
        if len(payload) != before.st_size or len(payload) > max_bytes:
            raise RagSafeIoError("RAG safe read target exceeded its byte bound.")
        return payload, before_metadata
    except RagSafeIoError:
        raise
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise RagSafeIoError("RAG safe read target is not safe.") from None
        raise RagSafeIoError("RAG safe read target could not be opened.") from None
    finally:
        if file_fd >= 0:
            os.close(file_fd)


def _stable_metadata(value: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_uid,
        value.st_gid,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _require_owned_nonwritable_directory(directory_fd: int) -> None:
    metadata = os.fstat(directory_fd)
    if not stat.S_ISDIR(metadata.st_mode):
        raise RagSafeIoError("RAG approved directory must be a directory.")
    _require_current_owner(metadata)
    if metadata.st_mode & _SHARED_WRITE_BITS:
        raise RagSafeIoError("RAG approved directory must not be group/other writable.")


def _require_current_owner(metadata: os.stat_result) -> None:
    if metadata.st_uid != os.geteuid():
        raise RagSafeIoError("RAG approved path must be owned by the current process user.")


def _write_new_leaf(
    directory_fd: int,
    *,
    filename: str,
    content: bytes,
) -> None:
    file_fd = -1
    try:
        file_fd = os.open(
            ".",
            _WRITE_FLAGS,
            0o600,
            dir_fd=directory_fd,
        )
        os.fchmod(file_fd, 0o600)
        offset = 0
        while offset < len(content):
            written = os.write(file_fd, content[offset:])
            if written <= 0:
                raise RagSafeIoError("RAG safe write did not make forward progress.")
            offset += written
        metadata = os.fstat(file_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 0
            or metadata.st_size != len(content)
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise RagSafeIoError("RAG safe write anonymous file metadata drifted.")
        original_identity = (metadata.st_dev, metadata.st_ino)
        os.fsync(file_fd)
        try:
            # 열린 fd를 source로 사용해 directory 안의 temp pathname이 교체돼도 다른 inode를
            # publish하지 않는다. Linux `/proc/self/fd` link는 원본 inode에 직접 결합된다.
            os.link(
                f"/proc/self/fd/{file_fd}",
                filename,
                dst_dir_fd=directory_fd,
                follow_symlinks=True,
            )
        except FileExistsError:
            raise RagSafeIoError("RAG safe write target already exists.") from None
        target_fd = -1
        try:
            target_fd = os.open(filename, _FILE_FLAGS, dir_fd=directory_fd)
            target_metadata = os.fstat(target_fd)
            source_metadata = os.fstat(file_fd)
            if (
                (target_metadata.st_dev, target_metadata.st_ino) != original_identity
                or (source_metadata.st_dev, source_metadata.st_ino) != original_identity
                or not stat.S_ISREG(target_metadata.st_mode)
                or target_metadata.st_uid != os.geteuid()
                or source_metadata.st_uid != os.geteuid()
                or target_metadata.st_size != len(content)
                or stat.S_IMODE(target_metadata.st_mode) != 0o600
            ):
                # pathname 기반 cleanup은 경쟁자가 바꾼 unrelated inode를 지울 수 있으므로 금지한다.
                raise RagSafeIoError("RAG safe write published inode mismatched.")
        finally:
            if target_fd >= 0:
                os.close(target_fd)
        os.fsync(directory_fd)
    except RagSafeIoError:
        raise
    except OSError as error:
        if error.errno in {errno.EEXIST, errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise RagSafeIoError("RAG safe write target is not safe.") from None
        raise RagSafeIoError("RAG safe write failed.") from None
    finally:
        if file_fd >= 0:
            os.close(file_fd)
