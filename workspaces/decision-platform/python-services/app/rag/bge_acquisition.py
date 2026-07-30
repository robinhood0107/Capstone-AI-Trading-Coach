from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
from pathlib import Path, PurePosixPath
from typing import Sequence
from urllib.parse import SplitResult, parse_qs, quote, unquote, urlsplit, urlunsplit

import httpx

from app.rag.bge_artifact import (
    APPROVED_BGE_ARTIFACT_SPEC,
    BgeArtifactError,
    BgeArtifactFile,
    BgeArtifactSpec,
    BgeVerifiedPacket,
    validate_bge_artifact_spec,
    validate_download_redirect,
    verify_bge_packet,
)

_SOURCE_HOST = "huggingface.co"
_DOWNLOAD_CHUNK_BYTES = 4 * 1024 * 1024
_REDIRECT_STATUSES = frozenset({302, 303, 307, 308})
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MODEL_PARENT = _REPO_ROOT / "huggingface_model" / "BAAI" / "bge-m3"
DEFAULT_MODEL_ROOT = _MODEL_PARENT / APPROVED_BGE_ARTIFACT_SPEC.revision
DEFAULT_MODEL_MANIFEST = _MODEL_PARENT / (
    f".{APPROVED_BGE_ARTIFACT_SPEC.revision}.approved.json"
)


class BgeAcquisitionError(ValueError):
    """승인 packet 다운로드를 partial publish 없이 typed marker로 중단한다."""


def acquire_bge_packet(
    packet_root: Path,
    *,
    manifest_path: Path,
    spec: BgeArtifactSpec = APPROVED_BGE_ARTIFACT_SPEC,
    transport: httpx.BaseTransport | None = None,
) -> BgeVerifiedPacket:
    """exact revision의 allowlisted 파일만 한 번씩 내려받아 원자적으로 publish한다.

    환경 proxy와 자동 redirect를 끄고, source 200 또는 검증된 CAS redirect 한 hop만 허용한다.
    모든 파일의 size/hash/mode 검증이 끝난 뒤 packet root를 rename하고 manifest를 마지막
    completion marker로 publish한다.
    """

    try:
        validate_bge_artifact_spec(spec)
        _validate_destination_boundary(packet_root, manifest_path=manifest_path)
    except (BgeArtifactError, OSError) as error:
        raise BgeAcquisitionError("BGE_ACQUISITION_BOUNDARY") from error

    token = secrets.token_hex(12)
    staging_root = packet_root.parent / f".{packet_root.name}.staging-{token}"
    staging_created = False
    packet_published = False
    try:
        staging_root.mkdir(mode=0o700)
        staging_created = True
        _fsync_directory(packet_root.parent)
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        with httpx.Client(
            transport=transport,
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
            headers={
                "accept": "application/octet-stream",
                "accept-encoding": "identity",
                "user-agent": "capstone-bge-artifact-acquirer/1",
            },
        ) as client:
            for entry in spec.files:
                target_path = staging_root / PurePosixPath(entry.relative_path)
                _ensure_secure_parents(staging_root, target_path.parent)
                try:
                    _download_entry(client, entry=entry, spec=spec, target_path=target_path)
                except BgeAcquisitionError as error:
                    raise BgeAcquisitionError(
                        f"DOWNLOAD_ENTRY_FAILED:{entry.relative_path}:{error}"
                    ) from error
                except (httpx.HTTPError, OSError) as error:
                    raise BgeAcquisitionError(
                        f"DOWNLOAD_ENTRY_FAILED:{entry.relative_path}:{type(error).__name__}"
                    ) from error

        receipt = verify_bge_packet(staging_root, spec=spec)
        os.rename(staging_root, packet_root)
        staging_created = False
        packet_published = True
        _fsync_directory(packet_root.parent)
        _publish_manifest(
            manifest_path,
            spec=spec,
            receipt=receipt,
        )
        return receipt
    except BgeAcquisitionError:
        raise
    except (BgeArtifactError, httpx.HTTPError, OSError, ValueError) as error:
        marker = (
            "BGE_MANIFEST_PUBLISH_FAILED"
            if packet_published and not manifest_path.exists()
            else "BGE_ACQUISITION_FAILED"
        )
        raise BgeAcquisitionError(marker) from error
    finally:
        if staging_created:
            _remove_owned_staging(staging_root, expected_parent=packet_root.parent)


def verify_bge_completion_manifest(
    packet_root: Path,
    *,
    manifest_path: Path,
    spec: BgeArtifactSpec = APPROVED_BGE_ARTIFACT_SPEC,
) -> BgeVerifiedPacket:
    """packet exact 검증과 마지막 completion marker를 함께 대조한다."""

    receipt = verify_bge_packet(packet_root, spec=spec)
    try:
        manifest_stat = manifest_path.lstat()
    except FileNotFoundError as error:
        raise BgeAcquisitionError("BGE_COMPLETION_MANIFEST_MISSING") from error
    if (
        not stat.S_ISREG(manifest_stat.st_mode)
        or stat.S_ISLNK(manifest_stat.st_mode)
        or manifest_stat.st_nlink != 1
        or stat.S_IMODE(manifest_stat.st_mode) != 0o600
        or manifest_stat.st_size <= 0
        or manifest_stat.st_size > 4_096
    ):
        raise BgeAcquisitionError("BGE_COMPLETION_MANIFEST_BOUNDARY")
    file_descriptor = os.open(manifest_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        descriptor_stat = os.fstat(file_descriptor)
        if (
            descriptor_stat.st_ino != manifest_stat.st_ino
            or descriptor_stat.st_dev != manifest_stat.st_dev
            or descriptor_stat.st_size != manifest_stat.st_size
        ):
            raise BgeAcquisitionError("BGE_COMPLETION_MANIFEST_RACE")
        raw = os.read(file_descriptor, 4_097)
        if os.read(file_descriptor, 1):
            raise BgeAcquisitionError("BGE_COMPLETION_MANIFEST_SIZE")
    finally:
        os.close(file_descriptor)
    if len(raw) != manifest_stat.st_size:
        raise BgeAcquisitionError("BGE_COMPLETION_MANIFEST_SIZE")
    try:
        payload = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BgeAcquisitionError("BGE_COMPLETION_MANIFEST_JSON") from error
    expected = {
        "artifactType": spec.artifact_type,
        "complete": True,
        "fileCount": receipt.file_count,
        "fileManifestSha256": receipt.file_manifest_sha256,
        "license": spec.license_id,
        "repository": spec.repository,
        "revision": receipt.revision,
        "totalBytes": receipt.total_bytes,
    }
    if payload != expected:
        raise BgeAcquisitionError("BGE_COMPLETION_MANIFEST_DRIFT")
    return receipt


def _validate_destination_boundary(packet_root: Path, *, manifest_path: Path) -> None:
    if packet_root.exists() or packet_root.is_symlink():
        raise BgeAcquisitionError("BGE_DESTINATION_EXISTS")
    if manifest_path.exists() or manifest_path.is_symlink():
        raise BgeAcquisitionError("BGE_MANIFEST_EXISTS")
    if packet_root.parent != manifest_path.parent:
        raise BgeAcquisitionError("BGE_MANIFEST_PARENT_MISMATCH")
    if (
        packet_root.name in {"", ".", ".."}
        or manifest_path.name in {"", ".", ".."}
        or "/" in packet_root.name
        or "/" in manifest_path.name
    ):
        raise BgeAcquisitionError("BGE_DESTINATION_NAME")
    parent_stat = packet_root.parent.lstat()
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or stat.S_ISLNK(parent_stat.st_mode)
        or parent_stat.st_uid != os.getuid()
        or stat.S_IMODE(parent_stat.st_mode) != 0o700
    ):
        raise BgeAcquisitionError("BGE_DESTINATION_PARENT_MODE")


def _download_entry(
    client: httpx.Client,
    *,
    entry: BgeArtifactFile,
    spec: BgeArtifactSpec,
    target_path: Path,
) -> None:
    source_url = (
        f"https://{_SOURCE_HOST}/{spec.repository}/resolve/{spec.revision}/"
        f"{quote(entry.relative_path, safe='/')}?download=true"
    )
    with client.stream("GET", source_url) as response:
        if response.status_code == 200:
            _write_verified_response(response, entry=entry, target_path=target_path)
            return
        if response.status_code not in _REDIRECT_STATUSES:
            raise BgeAcquisitionError("DOWNLOAD_SOURCE_STATUS")
        location = response.headers.get("location", "")
    validated = resolve_download_redirect(location, entry=entry, spec=spec)
    redirect_url = validated.geturl()
    with client.stream("GET", redirect_url) as response:
        if response.status_code != 200 or response.headers.get("location"):
            raise BgeAcquisitionError("DOWNLOAD_REDIRECT_STATUS")
        _write_verified_response(response, entry=entry, target_path=target_path)


def resolve_download_redirect(
    location: str,
    *,
    entry: BgeArtifactFile,
    spec: BgeArtifactSpec,
) -> SplitResult:
    """CAS absolute URL 또는 pinned same-origin source-cache relative URL만 해석한다."""

    parsed = urlsplit(location)
    if parsed.scheme or parsed.netloc:
        try:
            return validate_download_redirect(location)
        except BgeArtifactError as error:
            raise BgeAcquisitionError("DOWNLOAD_REDIRECT") from error

    expected_path = (
        f"/api/resolve-cache/models/{spec.repository}/{spec.revision}/"
        f"{entry.relative_path}"
    )
    encoded_expected_path = (
        f"/api/resolve-cache/models/{spec.repository}/{spec.revision}/"
        f"{quote(entry.relative_path, safe='')}"
    )
    try:
        query = parse_qs(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=4,
        )
    except ValueError as error:
        raise BgeAcquisitionError("DOWNLOAD_REDIRECT") from error
    etag_values = query.get("etag", [])
    if (
        not location
        or parsed.path not in {expected_path, encoded_expected_path}
        or unquote(parsed.path) != expected_path
        or parsed.fragment
        or set(query) != {"download", "etag"}
        or query["download"] != ["true"]
        or len(etag_values) != 1
        or re.fullmatch(r'"[0-9a-f]{40,64}"', etag_values[0]) is None
    ):
        raise BgeAcquisitionError("DOWNLOAD_REDIRECT")
    return urlsplit(
        urlunsplit(
            (
                "https",
                _SOURCE_HOST,
                parsed.path,
                parsed.query,
                "",
            )
        )
    )


def _write_verified_response(
    response: httpx.Response,
    *,
    entry: BgeArtifactFile,
    target_path: Path,
) -> None:
    content_encoding = response.headers.get("content-encoding", "").strip().lower()
    if content_encoding not in {"", "identity"}:
        raise BgeAcquisitionError("DOWNLOAD_CONTENT_ENCODING")
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            parsed_length = int(content_length, 10)
        except ValueError as error:
            raise BgeAcquisitionError("DOWNLOAD_CONTENT_LENGTH") from error
        if parsed_length != entry.size_bytes:
            raise BgeAcquisitionError("DOWNLOAD_CONTENT_LENGTH")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    file_descriptor = os.open(target_path, flags, 0o600)
    digest = hashlib.sha256()
    bytes_written = 0
    try:
        os.fchmod(file_descriptor, 0o600)
        for chunk in response.iter_raw(chunk_size=_DOWNLOAD_CHUNK_BYTES):
            if not chunk:
                continue
            bytes_written += len(chunk)
            if bytes_written > entry.size_bytes:
                raise BgeAcquisitionError("DOWNLOAD_SIZE_MISMATCH")
            _write_all(file_descriptor, chunk)
            digest.update(chunk)
        if bytes_written != entry.size_bytes:
            raise BgeAcquisitionError("DOWNLOAD_SIZE_MISMATCH")
        if digest.hexdigest() != entry.sha256:
            raise BgeAcquisitionError("DOWNLOAD_SHA256_MISMATCH")
        os.fsync(file_descriptor)
    finally:
        os.close(file_descriptor)
    _fsync_directory(target_path.parent)


def _write_all(file_descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(file_descriptor, view)
        if written <= 0:
            raise BgeAcquisitionError("DOWNLOAD_WRITE_FAILED")
        view = view[written:]


def _ensure_secure_parents(staging_root: Path, target_parent: Path) -> None:
    relative = target_parent.relative_to(staging_root)
    current = staging_root
    for part in relative.parts:
        if part in {"", ".", ".."}:
            raise BgeAcquisitionError("DOWNLOAD_PATH_ESCAPE")
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        current_stat = current.lstat()
        if (
            not stat.S_ISDIR(current_stat.st_mode)
            or stat.S_ISLNK(current_stat.st_mode)
            or current_stat.st_uid != os.getuid()
            or stat.S_IMODE(current_stat.st_mode) != 0o700
        ):
            raise BgeAcquisitionError("DOWNLOAD_DIRECTORY_BOUNDARY")


def _publish_manifest(
    manifest_path: Path,
    *,
    spec: BgeArtifactSpec,
    receipt: BgeVerifiedPacket,
) -> None:
    payload = {
        "artifactType": spec.artifact_type,
        "complete": True,
        "fileCount": receipt.file_count,
        "fileManifestSha256": receipt.file_manifest_sha256,
        "license": spec.license_id,
        "repository": spec.repository,
        "revision": receipt.revision,
        "totalBytes": receipt.total_bytes,
    }
    content = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("utf-8")
    temporary_path = manifest_path.parent / (
        f".{manifest_path.name}.tmp-{secrets.token_hex(12)}"
    )
    file_descriptor = os.open(
        temporary_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.fchmod(file_descriptor, 0o600)
        _write_all(file_descriptor, content)
        os.fsync(file_descriptor)
    finally:
        os.close(file_descriptor)
    try:
        os.rename(temporary_path, manifest_path)
        _fsync_directory(manifest_path.parent)
    finally:
        if temporary_path.exists() and not temporary_path.is_symlink():
            temporary_path.unlink()


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_owned_staging(staging_root: Path, *, expected_parent: Path) -> None:
    if (
        staging_root.parent != expected_parent
        or not staging_root.name.startswith(".")
        or ".staging-" not in staging_root.name
    ):
        return
    try:
        staging_stat = staging_root.lstat()
    except FileNotFoundError:
        return
    if (
        stat.S_ISDIR(staging_stat.st_mode)
        and not stat.S_ISLNK(staging_stat.st_mode)
        and staging_stat.st_uid == os.getuid()
    ):
        shutil.rmtree(staging_root)


def _ensure_model_parent() -> None:
    current = _REPO_ROOT / "huggingface_model"
    root_stat = current.lstat()
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or stat.S_ISLNK(root_stat.st_mode)
        or root_stat.st_uid != os.getuid()
        or root_stat.st_mode & 0o022
    ):
        raise BgeAcquisitionError("BGE_REGISTRY_ROOT_BOUNDARY")
    for part in ("BAAI", "bge-m3"):
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        current.chmod(0o700)
        current_stat = current.lstat()
        if (
            not stat.S_ISDIR(current_stat.st_mode)
            or stat.S_ISLNK(current_stat.st_mode)
            or current_stat.st_uid != os.getuid()
            or stat.S_IMODE(current_stat.st_mode) != 0o700
        ):
            raise BgeAcquisitionError("BGE_REGISTRY_PARENT_BOUNDARY")


def main(argv: Sequence[str] | None = None) -> int:
    """코드에 고정된 exact packet/destination만 acquire하거나 재검증한다."""

    parser = argparse.ArgumentParser(
        description="Acquire the exact approved BGE-M3 ONNX data-only packet.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        _ensure_model_parent()
        if DEFAULT_MODEL_ROOT.exists() or DEFAULT_MODEL_MANIFEST.exists():
            receipt = verify_bge_completion_manifest(
                DEFAULT_MODEL_ROOT,
                manifest_path=DEFAULT_MODEL_MANIFEST,
            )
            operation = "VERIFIED_EXISTING"
        else:
            receipt = acquire_bge_packet(
                DEFAULT_MODEL_ROOT,
                manifest_path=DEFAULT_MODEL_MANIFEST,
            )
            operation = "ACQUIRED"
    except (BgeAcquisitionError, BgeArtifactError) as error:
        print(f"S4_2A_BGE_ARTIFACT_FAILED:{error}")
        return 2
    except OSError:
        print("S4_2A_BGE_ARTIFACT_FAILED:OS_BOUNDARY")
        return 2
    payload = {
        "fileCount": receipt.file_count,
        "fileManifestSha256": receipt.file_manifest_sha256,
        "operation": operation,
        "revision": receipt.revision,
        "totalBytes": receipt.total_bytes,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(
            "S4_2A_BGE_ARTIFACT_VERIFIED "
            f"operation={operation} files={receipt.file_count} bytes={receipt.total_bytes}"
        )
    return 0
