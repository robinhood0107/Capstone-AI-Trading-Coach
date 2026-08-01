from __future__ import annotations

import errno
import os
import secrets
import stat
from pathlib import Path, PurePosixPath


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
_GENERATED_MODE = 0o644


class GeneratedArtifactWriteError(OSError):
    """Generated artifact output path가 repo 경계를 벗어나거나 unsafe leaf일 때 발생한다."""


def write_generated_artifact(
    repo_root: Path,
    relative_path: str,
    content: bytes,
) -> None:
    """repo-relative generated artifact를 no-follow parent 안에서만 원자 교체한다.

    Contract generator는 사람이 `--write`로 반복 실행하는 도구라 기존 파일 갱신은 유지해야 한다.
    대신 parent chain을 dirfd/O_NOFOLLOW로 열고 leaf가 symlink, directory, hardlink이면 거부해
    checkout 밖 inode를 덮는 실수를 차단한다.
    """

    if not isinstance(content, bytes) or not content:
        raise GeneratedArtifactWriteError("generated artifact content must be non-empty bytes")
    components = _validate_relative_components(relative_path)
    root_fd = _open_root(repo_root)
    try:
        directory_fd = _open_parent_directory(root_fd, components[:-1], create=True)
    finally:
        os.close(root_fd)
    try:
        _write_leaf(directory_fd, components[-1], content)
    finally:
        os.close(directory_fd)


def write_generated_path(repo_root: Path, output_path: Path, content: bytes) -> None:
    """absolute Path output을 repo-relative로 검증한 뒤 안전 writer에 위임한다."""

    root = repo_root if repo_root.is_absolute() else repo_root.resolve(strict=False)
    path = output_path if output_path.is_absolute() else root / output_path
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise GeneratedArtifactWriteError("generated artifact output escaped repo root") from error
    write_generated_artifact(root, relative.as_posix(), content)


def _validate_relative_components(value: str) -> tuple[str, ...]:
    if (
        not value
        or value.startswith("/")
        or value.endswith("/")
        or "\\" in value
        or "//" in value
        or "\x00" in value
    ):
        raise GeneratedArtifactWriteError("generated artifact path must be clean relative POSIX")
    parts = PurePosixPath(value).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise GeneratedArtifactWriteError("generated artifact path must not contain dot segments")
    return tuple(parts)


def _open_root(repo_root: Path) -> int:
    if not repo_root.is_absolute():
        raise GeneratedArtifactWriteError("repo root must be absolute")
    try:
        status = repo_root.lstat()
    except OSError as error:
        raise GeneratedArtifactWriteError("repo root is not accessible") from error
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
        raise GeneratedArtifactWriteError("repo root must be a non-symlink directory")
    try:
        return os.open(repo_root, _DIRECTORY_FLAGS)
    except OSError as error:
        raise GeneratedArtifactWriteError("repo root could not be opened safely") from error


def _open_parent_directory(
    root_fd: int,
    components: tuple[str, ...],
    *,
    create: bool,
) -> int:
    current_fd = os.dup(root_fd)
    try:
        for component in components:
            if create:
                try:
                    os.mkdir(component, mode=0o755, dir_fd=current_fd)
                except FileExistsError:
                    pass
            next_fd = -1
            try:
                next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current_fd)
            except OSError as error:
                if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
                    raise GeneratedArtifactWriteError(
                        "generated artifact parent must be a non-symlink directory"
                    ) from None
                raise GeneratedArtifactWriteError(
                    "generated artifact parent could not be opened safely"
                ) from error
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _write_leaf(directory_fd: int, filename: str, content: bytes) -> None:
    _validate_replace_target(directory_fd, filename)
    temporary_name = f".{filename}.{secrets.token_hex(16)}.tmp"
    temporary_created = False
    temporary_identity: tuple[int, int] | None = None
    try:
        temporary_fd = os.open(
            temporary_name,
            _WRITE_FLAGS,
            _GENERATED_MODE,
            dir_fd=directory_fd,
        )
        temporary_created = True
        try:
            os.fchmod(temporary_fd, _GENERATED_MODE)
            _write_all(temporary_fd, content)
            metadata = os.fstat(temporary_fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size != len(content)
                or stat.S_IMODE(metadata.st_mode) != _GENERATED_MODE
            ):
                raise GeneratedArtifactWriteError(
                    "generated artifact temporary metadata drifted"
                )
            temporary_identity = (metadata.st_dev, metadata.st_ino)
            os.fsync(temporary_fd)
        finally:
            os.close(temporary_fd)

        _validate_replace_target(directory_fd, filename)
        os.replace(temporary_name, filename, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary_created = False
        _verify_published_leaf(
            directory_fd,
            filename=filename,
            expected_identity=temporary_identity,
            expected_size=len(content),
        )
        os.fsync(directory_fd)
    except GeneratedArtifactWriteError:
        raise
    except OSError as error:
        if error.errno in {errno.EEXIST, errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise GeneratedArtifactWriteError("generated artifact target is not safe") from None
        raise GeneratedArtifactWriteError("generated artifact write failed") from error
    finally:
        if temporary_created and temporary_identity is not None:
            _unlink_temporary_if_same(
                directory_fd,
                temporary_name=temporary_name,
                expected_identity=temporary_identity,
            )


def _validate_replace_target(directory_fd: int, filename: str) -> None:
    target_fd = -1
    try:
        target_fd = os.open(filename, _FILE_FLAGS, dir_fd=directory_fd)
    except FileNotFoundError:
        return
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise GeneratedArtifactWriteError("generated artifact target is not safe") from None
        raise GeneratedArtifactWriteError("generated artifact target could not be opened safely") from error
    try:
        metadata = os.fstat(target_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise GeneratedArtifactWriteError("generated artifact target must be regular")
        if metadata.st_nlink != 1:
            raise GeneratedArtifactWriteError("generated artifact target must not be a hardlink")
    finally:
        if target_fd >= 0:
            os.close(target_fd)


def _write_all(file_fd: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(file_fd, content[offset:])
        if written <= 0:
            raise GeneratedArtifactWriteError("generated artifact write did not progress")
        offset += written


def _verify_published_leaf(
    directory_fd: int,
    *,
    filename: str,
    expected_identity: tuple[int, int] | None,
    expected_size: int,
) -> None:
    file_fd = -1
    try:
        file_fd = os.open(filename, _FILE_FLAGS, dir_fd=directory_fd)
        metadata = os.fstat(file_fd)
        if (
            expected_identity is None
            or (metadata.st_dev, metadata.st_ino) != expected_identity
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size != expected_size
            or stat.S_IMODE(metadata.st_mode) != _GENERATED_MODE
        ):
            raise GeneratedArtifactWriteError("generated artifact published inode mismatched")
    except GeneratedArtifactWriteError:
        raise
    except OSError as error:
        raise GeneratedArtifactWriteError(
            "generated artifact target could not be verified"
        ) from error
    finally:
        if file_fd >= 0:
            os.close(file_fd)


def _unlink_temporary_if_same(
    directory_fd: int,
    *,
    temporary_name: str,
    expected_identity: tuple[int, int],
) -> None:
    file_fd = -1
    try:
        file_fd = os.open(temporary_name, _FILE_FLAGS, dir_fd=directory_fd)
        metadata = os.fstat(file_fd)
        if (metadata.st_dev, metadata.st_ino) == expected_identity:
            os.unlink(temporary_name, dir_fd=directory_fd)
    except OSError:
        return
    finally:
        if file_fd >= 0:
            os.close(file_fd)
