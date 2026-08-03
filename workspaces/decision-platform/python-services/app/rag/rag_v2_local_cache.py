from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

_MANAGED_CACHE_DIRECTORIES = (
    "cache",
    "download-staging",
    "oa-materialized",
    "oa-raw",
    "tmp",
)
_MAX_CACHE_DEPTH = 16
_MAX_CACHE_ENTRIES = 100_000
_SHARED_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH
_POSIX_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW


class RagV2LocalCacheError(ValueError):
    """fixed local cache tree가 symlink/reparse point 또는 ownership 경계를 위반했음을 나타낸다."""


@dataclass(frozen=True, slots=True)
class RagV2LocalCacheReceipt:
    """경로와 파일명을 노출하지 않는 local cache cleanup 결과다."""

    removed_entries: int


def clean_local_rag_cache(*, local_root: Path) -> RagV2LocalCacheReceipt:
    """approved local root 아래의 재생성 가능 cache directory만 fail-closed로 제거한다.

    owner control record, approved original document, BGE packet은 fixed cleanup set에 없으므로 이
    함수가 건드리지 않는다. symlink/reparse point나 unexpected leaf가 있으면 삭제 전에 중단한다.
    """

    if not local_root.is_absolute() or ".." in local_root.parts:
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE")
    try:
        root_metadata = local_root.lstat()
    except FileNotFoundError:
        return RagV2LocalCacheReceipt(removed_entries=0)
    except OSError as error:
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE") from error
    _validate_directory_metadata(root_metadata)
    if os.name == "nt":
        return _clean_windows(local_root)
    return _clean_posix(local_root)


def _clean_posix(local_root: Path) -> RagV2LocalCacheReceipt:
    root_fd = -1
    try:
        root_fd = os.open(local_root, _POSIX_DIRECTORY_FLAGS)
        _validate_directory_metadata(os.fstat(root_fd))
        preflight_count = sum(
            _preflight_posix(parent_fd=root_fd, name=name, depth=0)
            for name in _MANAGED_CACHE_DIRECTORIES
        )
        if preflight_count > _MAX_CACHE_ENTRIES:
            raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE")
        removed = sum(
            _delete_posix(parent_fd=root_fd, name=name, depth=0)
            for name in _MANAGED_CACHE_DIRECTORIES
        )
        os.fsync(root_fd)
        return RagV2LocalCacheReceipt(removed_entries=removed)
    except RagV2LocalCacheError:
        raise
    except OSError as error:
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE") from error
    finally:
        if root_fd >= 0:
            os.close(root_fd)


def _preflight_posix(*, parent_fd: int, name: str, depth: int) -> int:
    metadata = _lstat_at(parent_fd, name)
    if metadata is None:
        return 0
    _validate_cache_metadata(metadata)
    if stat.S_ISREG(metadata.st_mode):
        return 1
    if depth >= _MAX_CACHE_DEPTH:
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE")
    directory_fd = _open_directory_at(parent_fd, name)
    try:
        children = sorted(os.listdir(directory_fd))
        total = 1
        for child in children:
            if child in {"", ".", ".."}:
                raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE")
            total += _preflight_posix(parent_fd=directory_fd, name=child, depth=depth + 1)
            if total > _MAX_CACHE_ENTRIES:
                raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE")
        return total
    finally:
        os.close(directory_fd)


def _delete_posix(*, parent_fd: int, name: str, depth: int) -> int:
    metadata = _lstat_at(parent_fd, name)
    if metadata is None:
        return 0
    _validate_cache_metadata(metadata)
    if stat.S_ISREG(metadata.st_mode):
        os.unlink(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return 1
    if depth >= _MAX_CACHE_DEPTH:
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE")
    directory_fd = _open_directory_at(parent_fd, name)
    try:
        children = sorted(os.listdir(directory_fd))
        removed = 1
        for child in children:
            if child in {"", ".", ".."}:
                raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE")
            removed += _delete_posix(parent_fd=directory_fd, name=child, depth=depth + 1)
    finally:
        os.close(directory_fd)
    os.rmdir(name, dir_fd=parent_fd)
    os.fsync(parent_fd)
    return removed


def _lstat_at(parent_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE") from error


def _open_directory_at(parent_fd: int, name: str) -> int:
    try:
        descriptor = os.open(name, _POSIX_DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE") from error
    try:
        _validate_directory_metadata(os.fstat(descriptor))
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _clean_windows(local_root: Path) -> RagV2LocalCacheReceipt:
    managed_paths = tuple(local_root / name for name in _MANAGED_CACHE_DIRECTORIES)
    preflight_count = sum(_preflight_windows(path, depth=0) for path in managed_paths)
    if preflight_count > _MAX_CACHE_ENTRIES:
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE")
    return RagV2LocalCacheReceipt(
        removed_entries=sum(_delete_windows(path, depth=0) for path in managed_paths),
    )


def _preflight_windows(path: Path, *, depth: int) -> int:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return 0
    except OSError as error:
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE") from error
    _validate_cache_metadata(metadata)
    if stat.S_ISREG(metadata.st_mode):
        return 1
    if depth >= _MAX_CACHE_DEPTH:
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE")
    try:
        children = sorted((Path(entry.path) for entry in os.scandir(path)), key=lambda item: item.name)
    except OSError as error:
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE") from error
    total = 1
    for child in children:
        total += _preflight_windows(child, depth=depth + 1)
        if total > _MAX_CACHE_ENTRIES:
            raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE")
    return total


def _delete_windows(path: Path, *, depth: int) -> int:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return 0
    except OSError as error:
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE") from error
    _validate_cache_metadata(metadata)
    if stat.S_ISREG(metadata.st_mode):
        try:
            path.unlink()
        except OSError as error:
            raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE") from error
        return 1
    if depth >= _MAX_CACHE_DEPTH:
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE")
    try:
        children = sorted((Path(entry.path) for entry in os.scandir(path)), key=lambda item: item.name)
    except OSError as error:
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE") from error
    removed = 1
    for child in children:
        removed += _delete_windows(child, depth=depth + 1)
    try:
        path.rmdir()
    except OSError as error:
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE") from error
    return removed


def _validate_directory_metadata(metadata: os.stat_result) -> None:
    if not stat.S_ISDIR(metadata.st_mode) or _is_link_or_reparse(metadata):
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE")
    if os.name != "nt" and (
        metadata.st_uid != os.geteuid() or metadata.st_mode & _SHARED_WRITE_BITS
    ):
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE")


def _validate_cache_metadata(metadata: os.stat_result) -> None:
    if _is_link_or_reparse(metadata) or not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE")
    if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE")
    if os.name != "nt" and (
        metadata.st_uid != os.geteuid() or metadata.st_mode & _SHARED_WRITE_BITS
    ):
        raise RagV2LocalCacheError("LOCAL_CACHE_UNSAFE")


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(reparse_point and attributes & reparse_point)
