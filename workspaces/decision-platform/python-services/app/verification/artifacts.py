"""Owner-private immutable packet and report artifacts for P1 verification."""

from __future__ import annotations

import os
from pathlib import Path
import stat
from datetime import UTC, datetime
from typing import Mapping, cast

from app.data._shared.canonical_json import canonical_json_bytes
from app.data._shared.bounded_json import BoundedJsonError, BoundedJsonLimits, parse_bounded_json_bytes
from app.verification.models import VerificationReport
from app.verification.packet import P1VerificationPacket, packet_from_dict


class VerificationArtifactError(RuntimeError):
    """A verification artifact path or canonical document is unsafe."""


_JSON_LIMITS = BoundedJsonLimits(
    max_bytes=1_000_000,
    max_depth=16,
    max_list_items=1_024,
    max_object_keys=256,
    max_text_codepoints=262_144,
    max_text_bytes=1_000_000,
    max_number_characters=64,
)


def ensure_owner_private_directory(path: Path) -> Path:
    """Create or validate an absolute non-symlink directory with mode 0700."""

    if not path.is_absolute():
        raise VerificationArtifactError("P1 verification artifact root must be absolute")
    if not path.exists():
        parent = path.parent.resolve(strict=True)
        path.mkdir(mode=0o700)
        _fsync_directory(parent)
    resolved = path.resolve(strict=True)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise VerificationArtifactError("P1 verification artifact root must be a directory")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise VerificationArtifactError("P1 verification artifact root must be owner-private")
    return resolved


def publish_packet(root: Path, packet: P1VerificationPacket) -> Path:
    return _publish_immutable(
        root,
        f"packet-{packet.packet_sha256}.json",
        canonical_json_bytes(packet.to_dict()),
    )


def publish_report(root: Path, report: VerificationReport) -> Path:
    report.validate()
    return _publish_immutable(
        root,
        f"report-{report.to_dict()['evidenceSha256']}.json",
        canonical_json_bytes(report.to_dict()),
    )


def claim_packet_execution(
    root: Path,
    packet: P1VerificationPacket,
    *,
    claimed_at: datetime,
) -> Path:
    """Consume one live packet exactly once before constructing provider clients."""

    if claimed_at.tzinfo is None:
        raise VerificationArtifactError("P1 verification claim time must be timezone aware")
    content = canonical_json_bytes(
        {
            "claimedAt": claimed_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "contractId": "p1-verification-execution-claim.v1",
            "headSha": packet.head_sha,
            "packetSha256": packet.packet_sha256,
            "profile": "PROVIDER_READ_SMOKE",
        }
    )
    directory = ensure_owner_private_directory(root)
    filename = f"claim-{packet.packet_sha256}.json"
    directory_fd = os.open(
        directory,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        try:
            file_fd = os.open(
                filename,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            raise VerificationArtifactError(
                "P1 verification packet was already claimed"
            ) from None
        with os.fdopen(file_fd, "wb", closefd=True) as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return directory / filename


def read_packet(path: Path) -> P1VerificationPacket:
    value = _read_canonical(path)
    packet = packet_from_dict(value)
    if path.name.startswith("packet-") and path.name != f"packet-{packet.packet_sha256}.json":
        raise VerificationArtifactError("P1 verification packet filename hash mismatch")
    return packet


def read_report(path: Path) -> VerificationReport:
    return VerificationReport.from_dict(dict(_read_canonical(path)))


def _publish_immutable(root: Path, filename: str, content: bytes) -> Path:
    directory = ensure_owner_private_directory(root)
    directory_fd = os.open(
        directory,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        try:
            file_fd = os.open(
                filename,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            existing = _read_regular(directory / filename)
            if existing != content:
                raise VerificationArtifactError(
                    "P1 verification immutable artifact conflicts with existing bytes"
                ) from None
        else:
            with os.fdopen(file_fd, "wb", closefd=True) as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return directory / filename


def _read_canonical(path: Path) -> Mapping[str, object]:
    content = _read_regular(path)
    try:
        value = parse_bounded_json_bytes(content, limits=_JSON_LIMITS)
    except BoundedJsonError as error:
        raise VerificationArtifactError("P1 verification artifact is invalid JSON") from error
    if not isinstance(value, dict) or canonical_json_bytes(value) != content:
        raise VerificationArtifactError("P1 verification artifact is not canonical JSON")
    return cast(Mapping[str, object], value)


def _read_regular(path: Path) -> bytes:
    if not path.is_absolute():
        raise VerificationArtifactError("P1 verification artifact path is unsafe")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise VerificationArtifactError("P1 verification artifact is unavailable") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise VerificationArtifactError("P1 verification artifact must be owner-private")
        if info.st_size > _JSON_LIMITS.max_bytes:
            raise VerificationArtifactError("P1 verification artifact exceeds size limit")
        chunks: list[bytes] = []
        remaining = _JSON_LIMITS.max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > _JSON_LIMITS.max_bytes:
            raise VerificationArtifactError("P1 verification artifact exceeds size limit")
        return content
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
