from __future__ import annotations

import errno
import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.gdelt.errors import GdeltAggregateError

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "rawquery",
        "requesturl",
        "responseheaders",
        "headline",
        "title",
        "body",
        "summary",
        "url",
        "image",
        "imageurl",
        "domain",
        "accountid",
        "orderid",
        "userid",
    }
)


@dataclass(frozen=True)
class PublishedObservation:
    path: Path
    artifact_hash: str


def publish_observation(*, root: Path, observation: dict[str, object]) -> PublishedObservation:
    """sanitized canonical observation 하나를 0600 append-only artifact로 게시한다.

    root traversal은 directory fd와 O_NOFOLLOW를 사용하고 temp fsync 뒤 hard-link no-replace로
    commit하므로 symlink·overwrite·partial publication을 거부한다.
    """

    artifact_hash = observation.get("artifactHash")
    if not isinstance(artifact_hash, str) or _HASH_PATTERN.fullmatch(artifact_hash) is None:
        raise GdeltAggregateError("STORAGE_UNSAFE", "artifact hash is invalid")
    identity = dict(observation)
    identity.pop("artifactHash", None)
    expected = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    if artifact_hash != expected:
        raise GdeltAggregateError("STORAGE_UNSAFE", "artifact hash does not match")
    _reject_sensitive_keys(observation)
    available_at = observation.get("availableAt")
    if not isinstance(available_at, str):
        raise GdeltAggregateError("STORAGE_UNSAFE", "availability is invalid")
    try:
        day = datetime.strptime(available_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise GdeltAggregateError("STORAGE_UNSAFE", "availability is invalid") from None
    root_path = _absolute_root(root)
    root_fd = _open_absolute_tree(root_path)
    try:
        leaf_fd = _open_children(root_fd, (f"{day:%Y}", f"{day:%m}", f"{day:%d}"))
    finally:
        os.close(root_fd)
    filename = f"{artifact_hash}.json"
    content = canonical_json_bytes(observation)
    try:
        _publish_no_replace(leaf_fd, filename, content)
    finally:
        os.close(leaf_fd)
    path = root_path / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}" / filename
    return PublishedObservation(path=path, artifact_hash=artifact_hash)


def _reject_sensitive_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str) or _is_sensitive_key(key, nested):
                raise GdeltAggregateError("STORAGE_UNSAFE", "sensitive field is forbidden")
            _reject_sensitive_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_sensitive_keys(nested)


def _is_sensitive_key(key: str, value: object) -> bool:
    # 집계 count와 명시적인 false 표식만 article/rawProvider 접두어의 허용 예외다.
    if key == "articleCount":
        return False
    if key in {"articleMetadataStored", "rawProviderDataStored"}:
        return value is not False
    normalized = key.casefold()
    return normalized in _FORBIDDEN_METADATA_KEYS or normalized.startswith(
        ("article", "publisher", "rawprovider")
    )


def _absolute_root(root: Path) -> Path:
    if ".." in root.parts:
        raise GdeltAggregateError("STORAGE_UNSAFE", "storage root is invalid")
    return root if root.is_absolute() else Path.cwd() / root


def _open_absolute_tree(path: Path) -> int:
    if not path.is_absolute() or path.anchor != "/":
        raise GdeltAggregateError("STORAGE_UNSAFE", "storage root is invalid")
    current_fd = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in path.parts[1:]:
            next_fd = _open_child(current_fd, component)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _open_children(parent_fd: int, components: tuple[str, ...]) -> int:
    current_fd = os.dup(parent_fd)
    try:
        for component in components:
            next_fd = _open_child(current_fd, component)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _open_child(parent_fd: int, component: str) -> int:
    try:
        os.mkdir(component, mode=0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileExistsError:
        pass
    except OSError:
        raise GdeltAggregateError("STORAGE_UNSAFE", "storage path is unsafe") from None
    try:
        return os.open(component, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise GdeltAggregateError("STORAGE_UNSAFE", "storage path is unsafe") from None
        raise GdeltAggregateError("STORAGE_UNSAFE", "storage path is unavailable") from None


def _publish_no_replace(directory_fd: int, filename: str, content: bytes) -> None:
    temporary = f".observation.{secrets.token_hex(16)}.tmp"
    file_fd = -1
    try:
        file_fd = os.open(
            temporary,
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
        try:
            os.link(
                temporary,
                filename,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
            os.fsync(directory_fd)
        except FileExistsError:
            raise GdeltAggregateError("ARTIFACT_CONFLICT", "artifact already exists") from None
        except OSError:
            raise GdeltAggregateError("STORAGE_UNSAFE", "artifact publish failed") from None
    except GdeltAggregateError:
        raise
    except OSError:
        raise GdeltAggregateError("STORAGE_UNSAFE", "artifact write failed") from None
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
