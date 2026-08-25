from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from app.data._shared import secure_snapshot_storage
from app.data._shared.secure_snapshot_storage import (
    SecureSnapshotStorageError,
    publish_source_snapshot,
)

_SNAPSHOT_PATH = "ecos/2026/07/14/00000000-0000-4000-8000-000000000001/snapshot.json"


def _manifest_bytes(snapshot_bytes: bytes, snapshot_path: str = _SNAPSHOT_PATH) -> bytes:
    payload = {
        "schemaVersion": 1,
        "source": "ecos",
        "snapshotPath": snapshot_path,
        "snapshotSha256": hashlib.sha256(snapshot_bytes).hexdigest(),
    }
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        + b"\n"
    )


def _publish(root: Path, snapshot_bytes: bytes = b'{"source":"ecos"}\n'):
    return publish_source_snapshot(
        root=root,
        snapshot_path=_SNAPSHOT_PATH,
        snapshot_bytes=snapshot_bytes,
        manifest_bytes=_manifest_bytes(snapshot_bytes),
    )


def test_publish_writes_exact_bytes_with_private_modes_and_manifest_commit_marker(
    tmp_path: Path,
) -> None:
    root = tmp_path / "snapshots"
    snapshot_bytes = b'{"source":"ecos","value":"3.5"}\n'

    published = _publish(root, snapshot_bytes)

    assert published.snapshot_path.read_bytes() == snapshot_bytes
    assert published.manifest_path.name == "manifest.json"
    assert published.manifest_path.exists()
    assert published.snapshot_sha256 == hashlib.sha256(snapshot_bytes).hexdigest()
    if os.name == "posix":
        assert published.snapshot_path.stat().st_mode & 0o777 == 0o600
        assert published.manifest_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/tmp/escaped/snapshot.json",
        "../escaped/snapshot.json",
        "ecos/2026/07/14/../escaped/snapshot.json",
        "ecos//2026/07/14/run/snapshot.json",
    ],
)
def test_absolute_traversal_and_dot_segment_paths_are_rejected(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    snapshot_bytes = b"{}\n"

    with pytest.raises(SecureSnapshotStorageError, match=r"path"):
        publish_source_snapshot(
            root=tmp_path / "snapshots",
            snapshot_path=unsafe_path,
            snapshot_bytes=snapshot_bytes,
            manifest_bytes=_manifest_bytes(snapshot_bytes, unsafe_path),
        )


@pytest.mark.parametrize("symlink_level", ["root", "parent", "snapshot", "manifest"])
def test_root_parent_and_final_symlinks_are_rejected_without_touching_target(
    tmp_path: Path,
    symlink_level: str,
) -> None:
    root = tmp_path / "snapshots"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "target"
    target.write_bytes(b"do-not-touch")
    leaf = root / Path(_SNAPSHOT_PATH).parent

    if symlink_level == "root":
        root.symlink_to(outside, target_is_directory=True)
    elif symlink_level == "parent":
        root.mkdir()
        (root / "ecos").symlink_to(outside, target_is_directory=True)
    else:
        leaf.mkdir(parents=True)
        (leaf / f"{symlink_level}.json").symlink_to(target)

    with pytest.raises(SecureSnapshotStorageError, match=r"safe|symlink|exist"):
        _publish(root)

    assert target.read_bytes() == b"do-not-touch"


def test_existing_artifact_is_never_overwritten(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    first = b'{"version":1}\n'
    _publish(root, first)

    with pytest.raises(SecureSnapshotStorageError, match=r"exist|overwrite"):
        _publish(root, b'{"version":2}\n')

    assert (root / _SNAPSHOT_PATH).read_bytes() == first


def test_manifest_path_or_hash_mismatch_is_rejected_before_write(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    snapshot_bytes = b"{}\n"
    invalid_manifest = _manifest_bytes(snapshot_bytes).replace(
        hashlib.sha256(snapshot_bytes).hexdigest().encode(),
        b"0" * 64,
    )

    with pytest.raises(SecureSnapshotStorageError, match=r"hash"):
        publish_source_snapshot(
            root=root,
            snapshot_path=_SNAPSHOT_PATH,
            snapshot_bytes=snapshot_bytes,
            manifest_bytes=invalid_manifest,
        )

    assert not (root / _SNAPSHOT_PATH).exists()


def test_manifest_publish_failure_leaves_only_an_ignored_orphan_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "snapshots"

    def fail_manifest_link(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic manifest publish failure")

    monkeypatch.setattr(secure_snapshot_storage.os, "link", fail_manifest_link)

    with pytest.raises(SecureSnapshotStorageError, match=r"publish"):
        _publish(root)

    leaf = root / Path(_SNAPSHOT_PATH).parent
    assert (leaf / "snapshot.json").exists()
    assert not (leaf / "manifest.json").exists()


def test_snapshot_manifest_and_directories_are_fsynced_before_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(secure_snapshot_storage.os, "fsync", recording_fsync)

    _publish(tmp_path / "snapshots")

    # snapshot, manifest temp, leaf directory와 상위 directory의 durable publication을 확인한다.
    assert len(calls) >= 4
