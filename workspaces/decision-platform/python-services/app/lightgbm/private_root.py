"""S5 production artifact root의 exact owner-private directory 경계."""

from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path

from app.lightgbm.errors import LightGbmContractError


def require_private_root(root: Path) -> None:
    """절대경로의 모든 component를 no-symlink로 확인하고 leaf mode를 0700으로 고정한다."""

    if not root.is_absolute() or ".." in root.parts or root.anchor != "/":
        raise LightGbmContractError("S5 source root must be an absolute normalized path")
    current = Path(root.anchor)
    for component in root.parts[1:]:
        current /= component
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise LightGbmContractError("S5 source root contains a symlink")
    metadata = root.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise LightGbmContractError("S5 source root must be an owner-owned 0700 directory")


def acquire_run_lock(run_root: Path) -> int:
    """한 bootstrap/resume만 provider handoff를 수행하도록 run 단위 exclusive lock을 잡는다."""

    return _acquire_private_lock(
        root=run_root,
        filename=".bootstrap.lock",
        active_error="S5 bootstrap run is already active",
    )


def acquire_bootstrap_root_lock(root: Path) -> int:
    """서로 다른 packet/run이 같은 approved root의 provider budget을 병렬 소비하지 못하게 한다."""

    return _acquire_private_lock(
        root=root,
        filename=".bootstrap-root.lock",
        active_error="S5 bootstrap root is already active",
    )


def _acquire_private_lock(*, root: Path, filename: str, active_error: str) -> int:
    """Owner-private root의 고정 lock inode만 nonblocking exclusive로 연다."""

    require_private_root(root)
    descriptor = os.open(
        root / filename,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise LightGbmContractError("S5 bootstrap lock file is not owner-private")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LightGbmContractError(active_error) from None
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def require_private_regular_file(path: Path, *, expected_device: int, expected_inode: int) -> None:
    """bounded reader가 읽은 동일 inode가 owner-only 0600 regular file인지 재확인한다."""

    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or (metadata.st_dev, metadata.st_ino) != (expected_device, expected_inode)
        ):
            raise LightGbmContractError("S5 production file is not owner-private")
    finally:
        os.close(descriptor)


def release_run_lock(descriptor: int) -> None:
    """잠금을 해제하고 descriptor를 닫으며 lock 파일 자체는 감사 경계로 보존한다."""

    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
