#!/usr/bin/env python3
"""Small fail-closed guard for P1 release archives and local state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import sys
import tarfile
from typing import Any, BinaryIO, NoReturn, cast


_DIGEST = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_SAFE_ARCHIVE_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,239}")
_MAX_STATE_BYTES = 4 * 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


def _fail(message: str) -> NoReturn:
    raise ValueError(message)


def _components(path: Path) -> tuple[int, list[str]]:
    absolute = path.absolute()
    parts = list(absolute.parts)
    if not parts or parts[0] != os.sep:
        _fail("absolute path required")
    return os.open(os.sep, os.O_RDONLY | _DIRECTORY), parts[1:]


def _open_directory(path: Path) -> int:
    descriptor, parts = _components(path)
    try:
        for part in parts:
            next_descriptor = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_regular_nofollow(path: Path) -> int:
    parent = _open_directory(path.absolute().parent)
    try:
        return os.open(path.name, os.O_RDONLY | _NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)


def _require_owned_directory(descriptor: int, mode: int = 0o700) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
        _fail("directory ownership")
    if stat.S_IMODE(metadata.st_mode) != mode:
        _fail("directory mode")


def _open_state(path: Path) -> int:
    descriptor = _open_directory(path)
    try:
        _require_owned_directory(descriptor)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _safe_relative(value: str) -> tuple[str, ...]:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        _fail("unsafe relative path")
    return path.parts


def _open_relative_parent(state_descriptor: int, relative: str) -> tuple[int, str]:
    parts = _safe_relative(relative)
    descriptor = os.dup(state_descriptor)
    try:
        for part in parts[:-1]:
            next_descriptor = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            _require_owned_directory(descriptor)
        return descriptor, parts[-1]
    except BaseException:
        os.close(descriptor)
        raise


def _read_all(stream: BinaryIO, limit: int = _MAX_STATE_BYTES) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        _fail("input too large")
    return data


def _atomic_write(state_descriptor: int, relative: str, data: bytes, mode: int) -> None:
    parent, name = _open_relative_parent(state_descriptor, relative)
    temporary = f".p1-{secrets.token_hex(16)}"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            mode,
            dir_fd=parent,
        )
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.fchmod(descriptor, mode)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
        check = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=parent)
        try:
            metadata = os.fstat(check)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
                _fail("state file ownership")
            if stat.S_IMODE(metadata.st_mode) != mode:
                _fail("state file mode")
        finally:
            os.close(check)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def state_init(path: Path) -> None:
    parent = path.absolute().parent
    name = path.absolute().name
    parent_descriptor = _open_directory(parent)
    try:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_descriptor)
        except FileExistsError:
            _fail("state already exists")
        state_descriptor = os.open(
            name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=parent_descriptor
        )
        try:
            _require_owned_directory(state_descriptor)
            os.mkdir("secrets", 0o700, dir_fd=state_descriptor)
            secrets_descriptor = os.open(
                "secrets",
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
                dir_fd=state_descriptor,
            )
            try:
                _require_owned_directory(secrets_descriptor)
            finally:
                os.close(secrets_descriptor)
            os.fsync(state_descriptor)
        finally:
            os.close(state_descriptor)
    finally:
        os.close(parent_descriptor)


def state_check(path: Path) -> None:
    descriptor = _open_state(path)
    try:
        secrets_descriptor = os.open(
            "secrets", os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=descriptor
        )
        try:
            _require_owned_directory(secrets_descriptor)
        finally:
            os.close(secrets_descriptor)
    finally:
        os.close(descriptor)


def state_mkdir(path: Path, relative: str) -> None:
    descriptor = _open_state(path)
    parent, name = _open_relative_parent(descriptor, relative)
    try:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
        child = os.open(name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=parent)
        try:
            _require_owned_directory(child)
        finally:
            os.close(child)
    finally:
        os.close(parent)
        os.close(descriptor)


def state_write(path: Path, relative: str, mode: int) -> None:
    if mode not in {0o600, 0o640}:
        _fail("state file mode")
    data = _read_all(sys.stdin.buffer)
    descriptor = _open_state(path)
    try:
        _atomic_write(descriptor, relative, data, mode)
    finally:
        os.close(descriptor)


def state_append(path: Path, relative: str, mode: int) -> None:
    if mode not in {0o600, 0o640}:
        _fail("state file mode")
    addition = _read_all(sys.stdin.buffer)
    descriptor = _open_state(path)
    parent, name = _open_relative_parent(descriptor, relative)
    try:
        current_descriptor = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=parent)
        try:
            metadata = os.fstat(current_descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
                _fail("state file ownership")
            if stat.S_IMODE(metadata.st_mode) != mode:
                _fail("state file mode")
            with os.fdopen(current_descriptor, "rb", closefd=False) as handle:
                current = _read_all(handle)
        finally:
            os.close(current_descriptor)
    finally:
        os.close(parent)
    try:
        if len(current) + len(addition) > _MAX_STATE_BYTES:
            _fail("state file too large")
        _atomic_write(descriptor, relative, current + addition, mode)
    finally:
        os.close(descriptor)


def state_latest_backup(path: Path) -> None:
    descriptor = _open_state(path)
    try:
        backups = os.open("backups", os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=descriptor)
        try:
            _require_owned_directory(backups)
            pattern = re.compile(r"capstone-p1-[0-9]{8}T[0-9]{6}Z\.dump")
            candidates: list[str] = []
            for name in os.listdir(backups):
                if pattern.fullmatch(name) is None:
                    continue
                metadata = os.stat(name, dir_fd=backups, follow_symlinks=False)
                if stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.geteuid():
                    candidates.append(name)
            if not candidates:
                _fail("backup not found")
            print(max(candidates))
        finally:
            os.close(backups)
    finally:
        os.close(descriptor)


def state_export(path: Path, relative: str, destination: Path, mode: int) -> None:
    if mode != 0o600:
        _fail("export mode")
    state_descriptor = _open_state(path)
    parent, name = _open_relative_parent(state_descriptor, relative)
    source = -1
    destination_parent = -1
    output = -1
    try:
        source = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=parent)
        before = os.fstat(source)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != mode
        ):
            _fail("export source boundary")
        destination_parent = _open_directory(destination.absolute().parent)
        _require_owned_directory(destination_parent)
        output = os.open(
            destination.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            mode,
            dir_fd=destination_parent,
        )
        while chunk := os.read(source, 1024 * 1024):
            view = memoryview(chunk)
            while view:
                view = view[os.write(output, view) :]
        os.fsync(output)
        after = os.fstat(source)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            _fail("export source changed")
    finally:
        for descriptor in (output, destination_parent, source, parent, state_descriptor):
            if descriptor >= 0:
                os.close(descriptor)


def _read_state_json(state_descriptor: int, relative: str) -> dict[str, Any] | None:
    parent, name = _open_relative_parent(state_descriptor, relative)
    try:
        try:
            descriptor = os.open(name, os.O_RDONLY | _NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            return None
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
                _fail("accepted release ownership")
            if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size > 4096:
                _fail("accepted release boundary")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                document = json.loads(_read_all(handle, 4096))
            if not isinstance(document, dict):
                _fail("accepted release document")
            return cast(dict[str, Any], document)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def release_accept(state: Path, manifest_path: Path) -> None:
    manifest = _read_json_nofollow(manifest_path, 1024 * 1024)
    required = {
        "releaseVersion",
        "commitSha",
        "treeSha",
        "configSha256",
        "sourceArchiveSha256",
        "imagesArchiveSha256",
        "licenseSha256",
    }
    if not required.issubset(manifest):
        _fail("release identity fields")
    version_match = _VERSION.fullmatch(str(manifest["releaseVersion"]))
    if version_match is None:
        _fail("release version")
    current = {key: manifest[key] for key in sorted(required)}
    descriptor = _open_state(state)
    try:
        accepted = _read_state_json(descriptor, "accepted-release.json")
        if accepted is not None:
            accepted_match = _VERSION.fullmatch(str(accepted.get("releaseVersion", "")))
            if accepted_match is None:
                _fail("accepted release version")
            current_version = tuple(map(int, version_match.groups()))
            accepted_version = tuple(map(int, accepted_match.groups()))
            same_version = current_version == accepted_version
            same_identity = current == accepted
            if same_version and not same_identity:
                _fail("release version collision")
            if current_version < accepted_version:
                approval = os.environ.get("P1_ALLOW_ROLLBACK_TO", "")
                expected = f"{current['releaseVersion']}@{current['commitSha']}"
                if approval != expected:
                    _fail("rollback approval required")
                return
            if same_identity:
                return
        encoded = (json.dumps(current, sort_keys=True, separators=(",", ":")) + "\n").encode()
        _atomic_write(descriptor, "accepted-release.json", encoded, 0o600)
    finally:
        os.close(descriptor)


def _read_json_nofollow(path: Path, limit: int) -> dict[str, Any]:
    descriptor = _open_regular_nofollow(path)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            _fail("JSON file boundary")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            value = json.loads(_read_all(handle, limit))
        if not isinstance(value, dict):
            _fail("JSON object required")
        return cast(dict[str, Any], value)
    finally:
        os.close(descriptor)


def _archive_entries(path: Path) -> list[dict[str, Any]]:
    descriptor = _open_regular_nofollow(path)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            _fail("archive must be regular")
        before = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        with (
            os.fdopen(os.dup(descriptor), "rb") as raw,
            tarfile.open(fileobj=raw, mode="r:") as archive,
        ):
            for member in archive:
                name = member.name.rstrip("/")
                pure = PurePosixPath(name)
                if (
                    not name
                    or "\\" in name
                    or not _SAFE_ARCHIVE_PATH.fullmatch(name)
                    or pure.is_absolute()
                    or any(part in {"", ".", ".."} for part in pure.parts)
                    or name in seen
                ):
                    _fail("unsafe or duplicate archive entry")
                seen.add(name)
                if member.isdir():
                    if member.size != 0:
                        _fail("directory archive size")
                    entries.append({"path": name, "size": 0, "type": "DIRECTORY"})
                elif member.isfile():
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        _fail("missing archive payload")
                    digest = hashlib.sha256()
                    size = 0
                    while chunk := extracted.read(1024 * 1024):
                        size += len(chunk)
                        digest.update(chunk)
                    if size != member.size:
                        _fail("archive entry size")
                    entries.append(
                        {"path": name, "sha256": digest.hexdigest(), "size": size, "type": "FILE"}
                    )
                else:
                    _fail("archive link or special entry")
        after_metadata = os.fstat(descriptor)
        after = (
            after_metadata.st_dev,
            after_metadata.st_ino,
            after_metadata.st_size,
            after_metadata.st_mtime_ns,
        )
        if before != after or not entries:
            _fail("archive changed during validation")
        return sorted(entries, key=lambda item: (item["path"], item["type"]))
    finally:
        os.close(descriptor)


def archive_inventory(path: Path, output: Path) -> None:
    entries = _archive_entries(path)
    output.write_text(
        json.dumps(entries, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )


def archive_compare(path: Path, inventory: Path) -> None:
    expected = json.loads(inventory.read_text(encoding="utf-8"))
    if expected != _archive_entries(path):
        _fail("archive inventory mismatch")


def stage_archive(source: Path, expected_digest: str, destination: Path) -> None:
    if _DIGEST.fullmatch(expected_digest) is None:
        _fail("archive digest")
    source_descriptor = _open_regular_nofollow(source)
    parent = _open_directory(destination.absolute().parent)
    target_descriptor = -1
    try:
        _require_owned_directory(parent)
        before = os.fstat(source_descriptor)
        if not stat.S_ISREG(before.st_mode):
            _fail("archive source type")
        target_descriptor = os.open(
            destination.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        digest = hashlib.sha256()
        while chunk := os.read(source_descriptor, 1024 * 1024):
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(target_descriptor, view)
                view = view[written:]
        os.fsync(target_descriptor)
        os.fchmod(target_descriptor, 0o600)
        after = os.fstat(source_descriptor)
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if identity_before != identity_after or digest.hexdigest() != expected_digest:
            _fail("archive source changed or digest mismatch")
        os.fsync(parent)
    finally:
        if target_descriptor >= 0:
            os.close(target_descriptor)
        os.close(parent)
        os.close(source_descriptor)


def _require_output_directory(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
        _fail("output directory ownership")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        _fail("output directory is writable by group or other")


def publish_directory(source: Path, destination_parent: Path, name: str) -> None:
    if name != Path(name).name or not name or name in {".", ".."}:
        _fail("publish name")
    source_parent = _open_directory(source.absolute().parent)
    destination = _open_directory(destination_parent)
    try:
        _require_output_directory(destination)
        source_descriptor = os.open(
            source.name,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
            dir_fd=source_parent,
        )
        try:
            _require_owned_directory(source_descriptor)
        finally:
            os.close(source_descriptor)
        try:
            os.stat(name, dir_fd=destination, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            _fail("publish destination exists")
        os.rename(source.name, name, src_dir_fd=source_parent, dst_dir_fd=destination)
        os.fsync(destination)
    finally:
        os.close(destination)
        os.close(source_parent)


def publish_file(source: Path, destination: Path, replace: bool) -> None:
    source_descriptor = _open_regular_nofollow(source)
    parent = _open_directory(destination.absolute().parent)
    temporary = f".p1-publish-{secrets.token_hex(16)}"
    output = -1
    try:
        _require_output_directory(parent)
        before = os.fstat(source_descriptor)
        if not stat.S_ISREG(before.st_mode):
            _fail("publish source type")
        if not replace:
            try:
                os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                _fail("publish destination exists")
        output = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        while chunk := os.read(source_descriptor, 1024 * 1024):
            view = memoryview(chunk)
            while view:
                view = view[os.write(output, view) :]
        os.fsync(output)
        os.fchmod(output, 0o600)
        after = os.fstat(source_descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            _fail("publish source changed")
        os.close(output)
        output = -1
        os.replace(temporary, destination.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        if output >= 0:
            os.close(output)
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)
        os.close(source_descriptor)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("state-init", "state-check"):
        command = subparsers.add_parser(name)
        command.add_argument("state", type=Path)
    mkdir = subparsers.add_parser("state-mkdir")
    mkdir.add_argument("state", type=Path)
    mkdir.add_argument("relative")
    write = subparsers.add_parser("state-write")
    write.add_argument("state", type=Path)
    write.add_argument("relative")
    write.add_argument("mode", type=lambda value: int(value, 8))
    append = subparsers.add_parser("state-append")
    append.add_argument("state", type=Path)
    append.add_argument("relative")
    append.add_argument("mode", type=lambda value: int(value, 8))
    latest = subparsers.add_parser("state-latest-backup")
    latest.add_argument("state", type=Path)
    export = subparsers.add_parser("state-export")
    export.add_argument("state", type=Path)
    export.add_argument("relative")
    export.add_argument("destination", type=Path)
    export.add_argument("mode", type=lambda value: int(value, 8))
    accept = subparsers.add_parser("release-accept")
    accept.add_argument("state", type=Path)
    accept.add_argument("manifest", type=Path)
    inventory = subparsers.add_parser("archive-inventory")
    inventory.add_argument("archive", type=Path)
    inventory.add_argument("output", type=Path)
    compare = subparsers.add_parser("archive-compare")
    compare.add_argument("archive", type=Path)
    compare.add_argument("inventory", type=Path)
    stage = subparsers.add_parser("stage-archive")
    stage.add_argument("source", type=Path)
    stage.add_argument("digest")
    stage.add_argument("destination", type=Path)
    publish_dir = subparsers.add_parser("publish-directory")
    publish_dir.add_argument("source", type=Path)
    publish_dir.add_argument("destination_parent", type=Path)
    publish_dir.add_argument("name")
    publish = subparsers.add_parser("publish-file")
    publish.add_argument("source", type=Path)
    publish.add_argument("destination", type=Path)
    publish.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    if args.command == "state-init":
        state_init(args.state)
    elif args.command == "state-check":
        state_check(args.state)
    elif args.command == "state-mkdir":
        state_mkdir(args.state, args.relative)
    elif args.command == "state-write":
        state_write(args.state, args.relative, args.mode)
    elif args.command == "state-append":
        state_append(args.state, args.relative, args.mode)
    elif args.command == "state-latest-backup":
        state_latest_backup(args.state)
    elif args.command == "state-export":
        state_export(args.state, args.relative, args.destination, args.mode)
    elif args.command == "release-accept":
        release_accept(args.state, args.manifest)
    elif args.command == "archive-inventory":
        archive_inventory(args.archive, args.output)
    elif args.command == "archive-compare":
        archive_compare(args.archive, args.inventory)
    elif args.command == "stage-archive":
        stage_archive(args.source, args.digest, args.destination)
    elif args.command == "publish-directory":
        publish_directory(args.source, args.destination_parent, args.name)
    elif args.command == "publish-file":
        publish_file(args.source, args.destination, args.replace)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError, tarfile.TarError) as error:
        print(f"P1_RELEASE_GUARD={type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
