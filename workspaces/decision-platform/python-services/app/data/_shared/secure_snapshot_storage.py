from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_SNAPSHOT_PATH_PATTERN = re.compile(
    r"ecos/[0-9]{4}/[0-9]{2}/[0-9]{2}/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-"
    r"[0-9a-f]{12}/snapshot\.json"
)


class SecureSnapshotStorageError(ValueError):
    """snapshot 경로·hash·filesystem 불변식 위반을 내부 절대경로 없이 보고한다."""


@dataclass(frozen=True)
class PublishedSourceSnapshot:
    snapshot_path: Path
    manifest_path: Path
    snapshot_sha256: str


def publish_source_snapshot(
    *,
    root: Path,
    snapshot_path: str,
    snapshot_bytes: bytes,
    manifest_bytes: bytes,
) -> PublishedSourceSnapshot:
    """snapshot을 먼저 durable write하고 manifest를 마지막 commit marker로 no-replace 게시한다.

    root부터 leaf까지 directory fd와 `O_NOFOLLOW`를 사용하며 기존 파일은 덮어쓰지 않는다.
    manifest 게시 전 crash가 나면 snapshot orphan만 남고 consumer는 이를 완성 artifact로 보지 않는다.
    """
    components = _validated_snapshot_components(snapshot_path)
    digest = hashlib.sha256(snapshot_bytes).hexdigest()
    _validate_manifest(manifest_bytes, snapshot_path=snapshot_path, snapshot_sha256=digest)
    root_path = _absolute_root(root)

    root_fd = _open_or_create_absolute_tree(root_path)
    try:
        leaf_fd = _open_or_create_children(root_fd, components[:-1])
    finally:
        os.close(root_fd)

    try:
        _write_exclusive(leaf_fd, "snapshot.json", snapshot_bytes)
        os.fsync(leaf_fd)
        _publish_manifest(leaf_fd, manifest_bytes)
        os.fsync(leaf_fd)
    finally:
        os.close(leaf_fd)

    leaf_path = root_path.joinpath(*components[:-1])
    return PublishedSourceSnapshot(
        snapshot_path=leaf_path / "snapshot.json",
        manifest_path=leaf_path / "manifest.json",
        snapshot_sha256=digest,
    )


def _validated_snapshot_components(value: str) -> tuple[str, ...]:
    if (
        not value
        or value.startswith("/")
        or "//" in value
        or "\\" in value
        or _SNAPSHOT_PATH_PATTERN.fullmatch(value) is None
    ):
        raise SecureSnapshotStorageError("snapshot path is invalid")
    path = PurePosixPath(value)
    if any(component in {"", ".", ".."} for component in path.parts):
        raise SecureSnapshotStorageError("snapshot path is invalid")
    return tuple(path.parts)


def _validate_manifest(content: bytes, *, snapshot_path: str, snapshot_sha256: str) -> None:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise SecureSnapshotStorageError("snapshot manifest was invalid") from None
    if not isinstance(payload, dict):
        raise SecureSnapshotStorageError("snapshot manifest was invalid")
    mapping = cast(dict[object, object], payload)
    manifest_path = mapping.get("snapshotPath")
    manifest_hash = mapping.get("snapshotSha256")
    if manifest_path != snapshot_path:
        raise SecureSnapshotStorageError("snapshot manifest path did not match")
    if manifest_hash != snapshot_sha256:
        raise SecureSnapshotStorageError("snapshot manifest hash did not match")


def _absolute_root(root: Path) -> Path:
    expanded = root.expanduser()
    if ".." in expanded.parts:
        raise SecureSnapshotStorageError("snapshot root path is not safe")
    return expanded if expanded.is_absolute() else Path.cwd() / expanded


def _open_or_create_absolute_tree(path: Path) -> int:
    if not path.is_absolute() or path.anchor != "/":
        raise SecureSnapshotStorageError("snapshot root path is not safe")
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


def _open_or_create_child(parent_fd: int, component: str) -> int:
    try:
        os.mkdir(component, mode=0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileExistsError:
        pass
    except OSError:
        raise SecureSnapshotStorageError("snapshot storage path is not safe") from None
    try:
        return os.open(component, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise SecureSnapshotStorageError("snapshot storage symlink path is not safe") from None
        raise SecureSnapshotStorageError("snapshot storage path is not safe") from None


def _write_exclusive(directory_fd: int, filename: str, content: bytes) -> None:
    file_fd = -1
    try:
        file_fd = os.open(
            filename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
        os.fchmod(file_fd, 0o600)
        with os.fdopen(file_fd, "wb") as file:
            file_fd = -1
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
    except FileExistsError:
        raise SecureSnapshotStorageError(f"snapshot artifact already exists: {filename}") from None
    except OSError:
        raise SecureSnapshotStorageError("snapshot artifact path is not safe") from None
    finally:
        if file_fd >= 0:
            os.close(file_fd)


def _publish_manifest(directory_fd: int, content: bytes) -> None:
    temporary = f".manifest.{secrets.token_hex(16)}.tmp"
    _write_exclusive(directory_fd, temporary, content)
    try:
        os.link(
            temporary,
            "manifest.json",
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        os.fsync(directory_fd)
    except FileExistsError:
        raise SecureSnapshotStorageError("snapshot manifest already exists") from None
    except OSError:
        raise SecureSnapshotStorageError("snapshot manifest publish failed") from None
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
