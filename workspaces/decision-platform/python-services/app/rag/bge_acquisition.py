from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import stat
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from app.data._shared.repository_root import repository_root
from typing import Protocol
from urllib.parse import SplitResult, parse_qs, quote, unquote, urlsplit, urlunsplit

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
from app.rag.source_registry import RagSourceRegistryError, validate_resolved_addresses

_SOURCE_HOST = "huggingface.co"
_ALLOWED_DOWNLOAD_HOSTS = frozenset(
    {
        _SOURCE_HOST,
        "cas-bridge.xethub.hf.co",
        "cdn-lfs.huggingface.co",
        "us.aws.cdn.hf.co",
    }
)
_DOWNLOAD_CHUNK_BYTES = 4 * 1024 * 1024
_REDIRECT_STATUSES = frozenset({302, 303, 307, 308})
_CONNECT_TIMEOUT_SECONDS = 10.0
_READ_TIMEOUT_SECONDS = 60.0
_REQUEST_HEADERS = {
    "Accept": "application/octet-stream",
    "Accept-Encoding": "identity",
    "Connection": "close",
    "User-Agent": "capstone-bge-artifact-acquirer/1",
}
_REPO_ROOT = repository_root(__file__, 5)
_MODEL_PARENT = _REPO_ROOT / "huggingface_model" / "BAAI" / "bge-m3"
DEFAULT_MODEL_ROOT = _MODEL_PARENT / APPROVED_BGE_ARTIFACT_SPEC.revision
DEFAULT_MODEL_MANIFEST = _MODEL_PARENT / (f".{APPROVED_BGE_ARTIFACT_SPEC.revision}.approved.json")


class BgeAcquisitionError(ValueError):
    """승인 packet 다운로드를 partial publish 없이 typed marker로 중단한다."""


class BgeDnsResolver(Protocol):
    def resolve(self, hostname: str) -> list[str]:
        """승인 hostname의 A/AAAA 주소를 canonical 문자열로 반환한다."""


class BgeDownloadResponse(Protocol):
    @property
    def status_code(self) -> int:
        """HTTP status code를 반환한다."""

    @property
    def headers(self) -> Mapping[str, str]:
        """소문자로 정규화한 response header를 반환한다."""

    def iter_raw(self, *, chunk_size: int) -> Iterator[bytes]:
        """압축 해제 없이 response body chunk를 반환한다."""


class BgeHttpsConnection(Protocol):
    peer_ip: str

    def __enter__(self) -> BgeHttpsConnection:
        """검증 후 GET을 보낼 수 있는 단일 연결을 반환한다."""

    def __exit__(self, *args: object) -> None:
        """response/socket을 닫는다."""

    def get(
        self,
        *,
        target: str,
        headers: Mapping[str, str],
    ) -> BgeDownloadResponse:
        """peer 검증이 끝난 연결에서만 GET을 한 번 전송한다."""


class BgeHttpsTransport(Protocol):
    def connect(
        self,
        *,
        hostname: str,
        pinned_ip: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
    ) -> BgeHttpsConnection:
        """DNS 재조회 없이 pinned IP에 TLS hostname을 보존해 연결한다."""


class _SocketBgeDnsResolver:
    def resolve(self, hostname: str) -> list[str]:
        """stdlib resolver 결과를 중복 제거한 canonical A/AAAA 집합으로 만든다."""

        try:
            results = socket.getaddrinfo(
                hostname,
                443,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
            addresses = {str(ipaddress.ip_address(result[4][0])) for result in results}
        except (OSError, ValueError) as error:
            raise BgeAcquisitionError("DOWNLOAD_DNS_RESOLUTION") from error
        return sorted(
            addresses,
            key=lambda value: (
                ipaddress.ip_address(value).version,
                ipaddress.ip_address(value).packed,
            ),
        )


class _StdlibBgeDownloadResponse:
    def __init__(self, response: http.client.HTTPResponse) -> None:
        self._response = response
        self.status_code = response.status
        headers: dict[str, str] = {}
        singleton_headers = {
            "content-encoding",
            "content-length",
            "location",
            "transfer-encoding",
        }
        for key, value in response.getheaders():
            normalized = key.strip().lower()
            if normalized in singleton_headers and normalized in headers:
                raise BgeAcquisitionError("DOWNLOAD_RESPONSE_HEADER_DUPLICATE")
            headers[normalized] = value.strip()
        self.headers = headers

    def iter_raw(self, *, chunk_size: int) -> Iterator[bytes]:
        while True:
            chunk = self._response.read(chunk_size)
            if not chunk:
                return
            yield chunk


class _StdlibBgeHttpsConnection:
    def __init__(
        self,
        *,
        hostname: str,
        pinned_ip: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
    ) -> None:
        self._hostname = hostname
        self._read_timeout_seconds = read_timeout_seconds
        self._socket: ssl.SSLSocket | None = None
        self._response: http.client.HTTPResponse | None = None
        self._request_sent = False
        raw_socket: socket.socket | None = None
        try:
            # socket target에는 hostname이 아니라 검증된 IP만 전달해 library 재해석을 막는다.
            parsed_ip = ipaddress.ip_address(pinned_ip)
            family = socket.AF_INET if parsed_ip.version == 4 else socket.AF_INET6
            raw_socket = socket.socket(
                family,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
            )
            raw_socket.settimeout(connect_timeout_seconds)
            if parsed_ip.version == 4:
                raw_socket.connect((pinned_ip, 443))
            else:
                raw_socket.connect((pinned_ip, 443, 0, 0))
            self.peer_ip = str(ipaddress.ip_address(raw_socket.getpeername()[0]))
            if self.peer_ip != str(ipaddress.ip_address(pinned_ip)):
                raise BgeAcquisitionError("DOWNLOAD_PEER_PIN_MISMATCH")
            context = ssl.create_default_context()
            self._socket = context.wrap_socket(raw_socket, server_hostname=hostname)
            raw_socket = None
        except BgeAcquisitionError:
            raise
        except (OSError, ValueError) as error:
            raise BgeAcquisitionError("DOWNLOAD_TRANSPORT_CONNECT") from error
        finally:
            if raw_socket is not None:
                raw_socket.close()

    def __enter__(self) -> BgeHttpsConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        if self._response is not None:
            self._response.close()
        if self._socket is not None:
            self._socket.close()

    def get(
        self,
        *,
        target: str,
        headers: Mapping[str, str],
    ) -> BgeDownloadResponse:
        if self._request_sent or self._socket is None:
            raise BgeAcquisitionError("DOWNLOAD_CONNECTION_STATE")
        if (
            not target.startswith("/")
            or "\r" in target
            or "\n" in target
            or any(
                not key or "\r" in key or "\n" in key or "\r" in value or "\n" in value
                for key, value in headers.items()
            )
        ):
            raise BgeAcquisitionError("DOWNLOAD_REQUEST_BOUNDARY")
        self._request_sent = True
        request = [f"GET {target} HTTP/1.1\r\n"]
        request.extend(f"{key}: {value}\r\n" for key, value in headers.items())
        request.append("\r\n")
        try:
            self._socket.sendall("".join(request).encode("ascii", errors="strict"))
            self._socket.settimeout(self._read_timeout_seconds)
            self._response = http.client.HTTPResponse(self._socket)
            self._response.begin()
            return _StdlibBgeDownloadResponse(self._response)
        except (OSError, UnicodeEncodeError, http.client.HTTPException) as error:
            raise BgeAcquisitionError("DOWNLOAD_TRANSPORT_RESPONSE") from error


class _StdlibBgeHttpsTransport:
    def connect(
        self,
        *,
        hostname: str,
        pinned_ip: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
    ) -> BgeHttpsConnection:
        return _StdlibBgeHttpsConnection(
            hostname=hostname,
            pinned_ip=pinned_ip,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
        )


def acquire_bge_packet(
    packet_root: Path,
    *,
    manifest_path: Path,
    spec: BgeArtifactSpec = APPROVED_BGE_ARTIFACT_SPEC,
    resolver: BgeDnsResolver | None = None,
    transport: BgeHttpsTransport | None = None,
) -> BgeVerifiedPacket:
    """exact revision의 allowlisted 파일만 한 번씩 내려받아 원자적으로 publish한다.

    환경 proxy·자동 redirect를 쓰지 않고, 두 번 검증한 global DNS 집합의 pinned IP에만
    연결한다. TLS hostname/Host와 실제 peer를 원 hostname에 묶고, 모든 파일의
    size/hash/mode 검증 뒤 packet root와 completion manifest를 순서대로 publish한다.
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
    active_resolver = resolver or _SocketBgeDnsResolver()
    active_transport = transport or _StdlibBgeHttpsTransport()
    try:
        staging_root.mkdir(mode=0o700)
        staging_created = True
        _fsync_directory(packet_root.parent)
        for entry in spec.files:
            target_path = staging_root / PurePosixPath(entry.relative_path)
            _ensure_secure_parents(staging_root, target_path.parent)
            try:
                _download_entry(
                    resolver=active_resolver,
                    transport=active_transport,
                    entry=entry,
                    spec=spec,
                    target_path=target_path,
                )
            except BgeAcquisitionError as error:
                raise BgeAcquisitionError(
                    f"DOWNLOAD_ENTRY_FAILED:{entry.relative_path}:{error}"
                ) from error
            except OSError as error:
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
    except (BgeArtifactError, OSError, ValueError) as error:
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
    *,
    resolver: BgeDnsResolver,
    transport: BgeHttpsTransport,
    entry: BgeArtifactFile,
    spec: BgeArtifactSpec,
    target_path: Path,
) -> None:
    source_url = (
        f"https://{_SOURCE_HOST}/{spec.repository}/resolve/{spec.revision}/"
        f"{quote(entry.relative_path, safe='/')}?download=true"
    )
    with _open_checked_response(
        source_url,
        resolver=resolver,
        transport=transport,
    ) as response:
        if response.status_code == 200:
            _write_verified_response(response, entry=entry, target_path=target_path)
            return
        if response.status_code not in _REDIRECT_STATUSES:
            raise BgeAcquisitionError("DOWNLOAD_SOURCE_STATUS")
        location = response.headers.get("location", "")
    validated = resolve_download_redirect(location, entry=entry, spec=spec)
    redirect_url = validated.geturl()
    with _open_checked_response(
        redirect_url,
        resolver=resolver,
        transport=transport,
    ) as response:
        if response.status_code != 200 or response.headers.get("location"):
            raise BgeAcquisitionError("DOWNLOAD_REDIRECT_STATUS")
        _write_verified_response(response, entry=entry, target_path=target_path)


@contextmanager
def _open_checked_response(
    url: str,
    *,
    resolver: BgeDnsResolver,
    transport: BgeHttpsTransport,
) -> Iterator[BgeDownloadResponse]:
    parsed = urlsplit(url)
    hostname = parsed.hostname
    try:
        port = parsed.port
    except ValueError as error:
        raise BgeAcquisitionError("DOWNLOAD_URL_BOUNDARY") from error
    if (
        parsed.scheme != "https"
        or hostname not in _ALLOWED_DOWNLOAD_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not parsed.path.startswith("/")
        or parsed.fragment
    ):
        raise BgeAcquisitionError("DOWNLOAD_URL_BOUNDARY")

    first_addresses = _resolve_and_validate(hostname, resolver=resolver)
    second_addresses = _resolve_and_validate(hostname, resolver=resolver)
    if set(first_addresses) != set(second_addresses):
        raise BgeAcquisitionError("DOWNLOAD_DNS_REBINDING")
    pinned_ip = first_addresses[0]
    target = urlunsplit(("", "", parsed.path, parsed.query, ""))
    try:
        connection_context = transport.connect(
            hostname=hostname,
            pinned_ip=pinned_ip,
            connect_timeout_seconds=_CONNECT_TIMEOUT_SECONDS,
            read_timeout_seconds=_READ_TIMEOUT_SECONDS,
        )
        with connection_context as connection:
            _validate_download_peer(connection.peer_ip, validated_addresses=first_addresses)
            response = connection.get(
                target=target,
                headers={**_REQUEST_HEADERS, "Host": hostname},
            )
            yield response
    except BgeAcquisitionError:
        raise
    except (OSError, TimeoutError, http.client.HTTPException) as error:
        raise BgeAcquisitionError("DOWNLOAD_TRANSPORT") from error


def _resolve_and_validate(
    hostname: str,
    *,
    resolver: BgeDnsResolver,
) -> list[str]:
    try:
        addresses = [str(ipaddress.ip_address(address)) for address in resolver.resolve(hostname)]
        validate_resolved_addresses(hostname, addresses)
    except (RagSourceRegistryError, ValueError, OSError) as error:
        raise BgeAcquisitionError("DOWNLOAD_DNS_POLICY") from error
    return sorted(
        set(addresses),
        key=lambda value: (
            ipaddress.ip_address(value).version,
            ipaddress.ip_address(value).packed,
        ),
    )


def _validate_download_peer(
    peer_ip: str,
    *,
    validated_addresses: list[str],
) -> None:
    try:
        normalized_peer = str(ipaddress.ip_address(peer_ip))
        validate_resolved_addresses("connected-peer", [normalized_peer])
    except (RagSourceRegistryError, ValueError) as error:
        raise BgeAcquisitionError("DOWNLOAD_PEER_POLICY") from error
    if normalized_peer not in set(validated_addresses):
        raise BgeAcquisitionError("DOWNLOAD_PEER_MISMATCH")


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
        f"/api/resolve-cache/models/{spec.repository}/{spec.revision}/{entry.relative_path}"
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
    response: BgeDownloadResponse,
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
    temporary_path = manifest_path.parent / (f".{manifest_path.name}.tmp-{secrets.token_hex(12)}")
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
