from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from app.data._shared.source_snapshot_models import SourceSnapshotManifest
from app.data.ecos.storage import serialize_ecos_snapshot
from app.data.naver.storage import serialize_naver_snapshot

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_MANIFEST_MAX_BYTES = 256 * 1024
_SNAPSHOT_MAX_BYTES = {"ecos": 2 * 1024 * 1024, "naver": 4 * 1024 * 1024}
_RETENTION_DAYS = {"ecos": 365, "naver": 30}
_DELETE_LIMIT = 1_000
_YEAR_PATTERN = re.compile(r"[0-9]{4}")
_MONTH_PATTERN = re.compile(r"(?:0[1-9]|1[0-2])")
_DAY_PATTERN = re.compile(r"(?:0[1-9]|[12][0-9]|3[01])")
_RUN_ID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


@dataclass
class _Summary:
    scanned: int = 0
    eligible: int = 0
    deleted: int = 0
    skipped: int = 0


def main(argv: list[str] | None = None) -> int:
    """검증된 source snapshot만 보존기한에 따라 dry-run 또는 제한 삭제한다."""
    parser = argparse.ArgumentParser(description="source snapshot retention")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    summary = _run_retention(root=args.root, run_date=args.as_of, apply=args.apply)
    mode = "apply" if args.apply else "dry-run"
    print(
        f"source snapshot retention mode={mode} scanned={summary.scanned} "
        f"eligible={summary.eligible} deleted={summary.deleted} skipped={summary.skipped}"
    )
    return 0


def _run_retention(*, root: Path, run_date: date, apply: bool) -> _Summary:
    summary = _Summary()
    root_fd = _open_root_directory(root)
    if root_fd is None:
        return summary
    try:
        _walk_directory(
            directory_fd=root_fd,
            components=(),
            run_date=run_date,
            apply=apply,
            summary=summary,
        )
    finally:
        os.close(root_fd)
    return summary


def _open_root_directory(root: Path) -> int | None:
    """root의 모든 구성요소를 no-follow로 열어 경로 바꿔치기를 차단한다."""
    try:
        absolute = root.expanduser()
    except (KeyError, RuntimeError):
        return None
    if not absolute.is_absolute():
        absolute = Path.cwd() / absolute
    parts = absolute.parts
    if not parts or parts[0] != "/":
        return None

    try:
        current_fd = os.open("/", _DIRECTORY_FLAGS)
    except OSError:
        return None
    try:
        for component in parts[1:]:
            if component in {"", ".", ".."}:
                os.close(current_fd)
                return None
            next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
    except OSError:
        os.close(current_fd)
        return None
    return current_fd


def _walk_directory(
    *,
    directory_fd: int,
    components: tuple[str, ...],
    run_date: date,
    apply: bool,
    summary: _Summary,
) -> bool:
    if len(components) == 5:
        return _process_leaf(
            leaf_fd=directory_fd,
            components=components,
            run_date=run_date,
            apply=apply,
            summary=summary,
        )

    try:
        names = sorted(os.listdir(directory_fd))
    except OSError:
        return False

    for name in names:
        if not _is_allowed_component(len(components), name):
            continue
        child_fd = _open_child_directory(directory_fd, name)
        if child_fd is None:
            continue
        try:
            stop = _walk_directory(
                directory_fd=child_fd,
                components=(*components, name),
                run_date=run_date,
                apply=apply,
                summary=summary,
            )
        finally:
            os.close(child_fd)

        # source 최상위는 유지하고, 하위 partition은 비어 있을 때만 제거한다.
        if apply and components:
            _remove_empty_directory(directory_fd, name)
        if stop:
            return True
    return False


def _is_allowed_component(depth: int, name: str) -> bool:
    if depth == 0:
        return name in _RETENTION_DAYS
    patterns = (_YEAR_PATTERN, _MONTH_PATTERN, _DAY_PATTERN, _RUN_ID_PATTERN)
    return patterns[depth - 1].fullmatch(name) is not None


def _open_child_directory(parent_fd: int, name: str) -> int | None:
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError:
        return None


def _process_leaf(
    *,
    leaf_fd: int,
    components: tuple[str, ...],
    run_date: date,
    apply: bool,
    summary: _Summary,
) -> bool:
    source, year, month, day, run_id = components
    summary.scanned += 1

    try:
        partition_date = date(int(year), int(month), int(day))
    except ValueError:
        summary.skipped += 1
        return False

    manifest_bytes = _read_regular_file(leaf_fd, "manifest.json", maximum_bytes=_MANIFEST_MAX_BYTES)
    if manifest_bytes is None:
        summary.skipped += 1
        return False
    try:
        manifest = SourceSnapshotManifest.model_validate_json(manifest_bytes)
    except (ValidationError, ValueError):
        summary.skipped += 1
        return False

    expected_snapshot_path = f"{source}/{year}/{month}/{day}/{run_id}/snapshot.json"
    if not _manifest_matches_partition(
        manifest,
        source=source,
        partition_date=partition_date,
        expected_snapshot_path=expected_snapshot_path,
    ):
        summary.skipped += 1
        return False

    snapshot_bytes = _read_regular_file(
        leaf_fd,
        "snapshot.json",
        maximum_bytes=_SNAPSHOT_MAX_BYTES[source],
    )
    if snapshot_bytes is None or not _snapshot_matches_manifest(snapshot_bytes, manifest):
        summary.skipped += 1
        return False

    age_days = (run_date - manifest.as_of).days
    if age_days <= manifest.retention_days:
        return False
    summary.eligible += 1

    if not apply:
        return False
    if summary.deleted >= _DELETE_LIMIT:
        return True

    # manifest가 commit marker이므로 먼저 제거해 불완전 snapshot의 재사용을 막는다.
    if not _unlink_at(leaf_fd, "manifest.json"):
        summary.skipped += 1
        return False
    if not _unlink_at(leaf_fd, "snapshot.json"):
        summary.skipped += 1
        return False
    summary.deleted += 1
    return summary.deleted >= _DELETE_LIMIT


def _manifest_matches_partition(
    manifest: SourceSnapshotManifest,
    *,
    source: str,
    partition_date: date,
    expected_snapshot_path: str,
) -> bool:
    if (
        manifest.source != source
        or manifest.as_of != partition_date
        or manifest.snapshot_path != expected_snapshot_path
        or manifest.retention_days != _RETENTION_DAYS[source]
    ):
        return False
    if source == "ecos":
        return manifest.provider_profile == "ecos" and manifest.operation == "ecos-macro-collect"
    return (
        manifest.provider_profile in {"naver-legacy", "naver-api-hub"}
        and manifest.operation == "naver-news-metadata-collect"
    )


def _snapshot_matches_manifest(snapshot_bytes: bytes, manifest: SourceSnapshotManifest) -> bool:
    if hashlib.sha256(snapshot_bytes).hexdigest() != manifest.snapshot_sha256:
        return False
    try:
        payload = json.loads(snapshot_bytes)
        canonical_bytes = (
            serialize_ecos_snapshot(payload)
            if manifest.source == "ecos"
            else serialize_naver_snapshot(payload)
        )
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError, RecursionError):
        return False
    # hash만 일치하는 임의 JSON이나 의미는 같지만 비정규화된 bytes를 삭제 대상으로 삼지 않는다.
    return canonical_bytes == snapshot_bytes


def _read_regular_file(parent_fd: int, name: str, *, maximum_bytes: int) -> bytes | None:
    try:
        file_fd = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    except OSError:
        return None
    try:
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum_bytes:
            return None
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining > 0:
            chunk = os.read(file_fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > maximum_bytes:
            return None
        return content
    except OSError:
        return None
    finally:
        os.close(file_fd)


def _unlink_at(parent_fd: int, name: str) -> bool:
    try:
        os.unlink(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except OSError:
        return False
    return True


def _remove_empty_directory(parent_fd: int, name: str) -> None:
    try:
        os.rmdir(name, dir_fd=parent_fd)
    except OSError:
        return


if __name__ == "__main__":
    raise SystemExit(main())
