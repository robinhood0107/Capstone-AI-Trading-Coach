from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import SplitResult, urlsplit, urlunsplit

import fitz

from app.rag.oa112_active_registry import Oa112RegistryEntry
from app.rag.source_registry import RagSourceRegistryError, validate_resolved_addresses

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
_WRITE_NEW_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_WRITE_APPEND_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC
_MAX_PACKET_BYTES = 64 * 1024
_MAX_SOURCE_BYTES = 100 * 1024 * 1024
_MAX_TOTAL_BYTES = 112 * _MAX_SOURCE_BYTES
_DOWNLOAD_CHUNK_BYTES = 64 * 1024
_CONNECT_TIMEOUT_SECONDS = 10.0
_READ_TIMEOUT_SECONDS = 60.0
_MAX_SOURCE_ELAPSED_SECONDS = 120.0
_DNS_WORKER_INPUT_MAX_BYTES = 1_024
_DNS_WORKER_OUTPUT_MAX_BYTES = 4_096
_HEAD_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_APPROVAL_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,127}$")
_OPERATOR = re.compile(r"^[A-Za-z0-9._-]{3,128}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_SOURCE_ID = re.compile(r"^src_[a-z0-9][a-z0-9_-]{2,95}$")
_CONTENT_RANGE = re.compile(r"^bytes ([0-9]+)-([0-9]+)/([0-9]+)$")
_PACKET_FIELDS = frozenset(
    {
        "approvalId",
        "ciDigest",
        "costCapMicrousd",
        "date",
        "expiresAt",
        "headSha",
        "logicalCallCap",
        "maximumPages",
        "maximumSourceBytes",
        "maximumTotalBytes",
        "nonce",
        "operation",
        "operator",
        "physicalCallCap",
        "provider",
        "query",
        "registryDigest",
        "retryCount",
        "securityDigest",
        "sourceEndpointDigest",
        "sourceIds",
        "symbol",
        "trackedRawArtifactCount",
        "treeSha256",
    }
)
_RESUME_STATE_FIELDS = frozenset(
    {
        "rawContentSha256",
        "registryDigest",
        "sourceId",
        "sourceRevisionId",
    }
)
_DNS_RESOLVER_WORKER = (
    "import ipaddress,json,socket,sys\n"
    "raw=sys.stdin.buffer.read(1024)\n"
    "try:\n"
    "    if not raw or len(raw)>=1024: raise ValueError\n"
    "    payload=json.loads(raw.decode('utf-8'))\n"
    "    if set(payload)!={'hostname'}: raise ValueError\n"
    "    hostname=payload['hostname']\n"
    "    if not isinstance(hostname,str) or not 1<=len(hostname)<=253: raise ValueError\n"
    "    addresses=sorted({str(ipaddress.ip_address(item[4][0])) for item in socket.getaddrinfo(hostname,443,family=socket.AF_UNSPEC,type=socket.SOCK_STREAM,proto=socket.IPPROTO_TCP)})\n"
    "    output=json.dumps({'addresses':addresses},separators=(',',':'),sort_keys=True).encode('utf-8')\n"
    "    if not addresses or len(output)>4096: raise ValueError\n"
    "    sys.stdout.buffer.write(output)\n"
    "except Exception:\n"
    "    raise SystemExit(1)\n"
)


class Oa112DownloadError(ValueError):
    """OA raw local cache download가 hard gate 또는 transport 경계를 위반했음을 나타낸다."""

    def __init__(
        self,
        code: str,
        *,
        attempt_count: int = 0,
        physical_call_count: int = 0,
        failure_receipt_written: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.attempt_count = attempt_count
        self.physical_call_count = physical_call_count
        self.failure_receipt_written = failure_receipt_written


class Oa112DnsResolver(Protocol):
    """fixed HTTPS hostname의 A/AAAA 결과를 남은 source deadline 안에 반환한다."""

    def resolve(self, hostname: str, *, timeout_seconds: float) -> list[str]: ...


class Oa112DownloadResponse(Protocol):
    """자동 redirect/decompression 없는 raw response stream boundary다."""

    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> Mapping[str, str]: ...

    def set_read_timeout_seconds(self, *, timeout_seconds: float) -> None: ...

    def iter_raw(self, *, chunk_size: int) -> Iterator[bytes]: ...


class Oa112HttpsConnection(Protocol):
    """DNS 결과에 pin된 IP로 연결된 단일 HTTPS request boundary다."""

    peer_ip: str

    def __enter__(self) -> Oa112HttpsConnection: ...

    def __exit__(self, *args: object) -> None: ...

    def get(
        self,
        *,
        target: str,
        headers: Mapping[str, str],
        read_timeout_seconds: float,
    ) -> Oa112DownloadResponse: ...


class Oa112HttpsTransport(Protocol):
    """runtime transport는 hostname 재해석 없이 already-pinned IP만 연결해야 한다."""

    def connect(
        self,
        *,
        hostname: str,
        pinned_ip: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        deadline: _Oa112SourceDeadline,
    ) -> Oa112HttpsConnection: ...


@dataclass(frozen=True, slots=True)
class Oa112DownloadPacket:
    """사용자 승인 후 local-only packet root에서 읽는 one-shot OA112 physical-call permit이다."""

    approval_id: str
    head_sha: str
    tree_sha256: str
    ci_digest: str
    security_digest: str
    registry_digest: str
    source_endpoint_digest: str
    source_ids: tuple[str, ...]
    provider: str
    operation: str
    query: str
    symbol: str
    date: str
    logical_call_cap: int
    physical_call_cap: int
    maximum_source_bytes: int
    maximum_total_bytes: int
    cost_cap_microusd: int
    retry_count: int
    tracked_raw_artifact_count: int
    operator: str
    expires_at: datetime
    nonce: str
    maximum_pages: int

    def validate(
        self,
        *,
        entries: Sequence[Oa112RegistryEntry],
        registry_digest: str,
        execution_binding: Oa112DownloadBinding,
        now: datetime,
    ) -> None:
        """packet은 current registry와 exact source order를 모두 보지 못하면 outbound를 열지 않는다."""

        source_ids = tuple(entry.source_id for entry in entries)
        if self.registry_digest != registry_digest:
            raise Oa112DownloadError("OA112_PACKET_REGISTRY_DRIFT")
        if self.source_ids != source_ids:
            raise Oa112DownloadError("OA112_PACKET_SOURCE_SCOPE_DRIFT")
        if self.source_endpoint_digest != oa112_source_endpoint_digest(entries):
            raise Oa112DownloadError("OA112_PACKET_ENDPOINT_SCOPE_DRIFT")
        execution_binding.validate()
        if (
            self.head_sha != execution_binding.head_sha
            or self.tree_sha256 != execution_binding.tree_sha256
            or self.ci_digest != execution_binding.ci_digest
            or self.security_digest != execution_binding.security_digest
        ):
            raise Oa112DownloadError("OA112_PACKET_EXECUTION_BINDING")
        if (
            _APPROVAL_ID.fullmatch(self.approval_id) is None
            or _HEAD_SHA.fullmatch(self.head_sha) is None
            or any(
                _SHA256.fullmatch(value) is None
                for value in (
                    self.tree_sha256,
                    self.ci_digest,
                    self.security_digest,
                    self.registry_digest,
                    self.source_endpoint_digest,
                )
            )
            or not self.source_ids
            or len(set(self.source_ids)) != len(self.source_ids)
            or any(_SOURCE_ID.fullmatch(source_id) is None for source_id in self.source_ids)
            or self.logical_call_cap != len(self.source_ids)
            or self.physical_call_cap != len(self.source_ids)
            or not 1 <= self.maximum_source_bytes <= _MAX_SOURCE_BYTES
            or not self.maximum_source_bytes <= self.maximum_total_bytes <= _MAX_TOTAL_BYTES
            or self.maximum_total_bytes > self.maximum_source_bytes * len(self.source_ids)
            or self.cost_cap_microusd != 0
            or self.retry_count != 0
            or self.tracked_raw_artifact_count != 0
            or self.provider != "OA112_OFFICIAL_HTTPS"
            or self.operation != "OA112_RAW_LOCAL_CACHE_DOWNLOAD"
            or self.query != "NONE"
            or self.symbol != "NONE"
            or self.date != "NONE"
            or _OPERATOR.fullmatch(self.operator) is None
            or _NONCE.fullmatch(self.nonce) is None
            or self.maximum_pages < 1
            or self.maximum_pages > 500
            or self.expires_at.tzinfo != UTC
            or not now < self.expires_at <= now + timedelta(hours=1)
            or any(not entry.machine_fetch_allowed for entry in entries)
        ):
            raise Oa112DownloadError("OA112_PACKET_INVALID")


@dataclass(frozen=True, slots=True)
class Oa112DownloadBinding:
    """현재 tracked HEAD/tree와 CI/security evidence를 외부 request 직전에 결속한다."""

    head_sha: str
    tree_sha256: str
    ci_digest: str
    security_digest: str

    def validate(self) -> None:
        """ambient 값은 packet loader가 신뢰할 수 없으므로 각 형식을 독립적으로 제한한다."""

        if _HEAD_SHA.fullmatch(self.head_sha) is None or any(
            _SHA256.fullmatch(value) is None
            for value in (self.tree_sha256, self.ci_digest, self.security_digest)
        ):
            raise Oa112DownloadError("OA112_EXECUTION_EVIDENCE_INVALID")


@dataclass(frozen=True, slots=True)
class Oa112DownloadedSourceReceipt:
    """raw cache filename/path/bytes를 반환하지 않는 source-level physical receipt다."""

    source_id: str
    source_revision_id: str
    raw_content_sha256: str
    bytes_read: int
    state: str


@dataclass(frozen=True, slots=True)
class Oa112DownloadReceipt:
    """physical count와 content-free source identity만 노출하는 run receipt다."""

    attempt_count: int
    physical_call_count: int
    downloaded_source_count: int
    reused_source_count: int
    sources: tuple[Oa112DownloadedSourceReceipt, ...]

    def content_free_projection(self) -> dict[str, object]:
        return {
            "attemptCount": self.attempt_count,
            "downloadedSourceCount": self.downloaded_source_count,
            "physicalCallCount": self.physical_call_count,
            "reusedSourceCount": self.reused_source_count,
            "sources": [
                {
                    "bytesRead": item.bytes_read,
                    "rawContentSha256": item.raw_content_sha256,
                    "sourceId": item.source_id,
                    "sourceRevisionId": item.source_revision_id,
                    "state": item.state,
                }
                for item in self.sources
            ],
        }


@dataclass(frozen=True, slots=True)
class _ResponsePlan:
    declared_bytes: int | None
    total_bytes: int | None


def _utc_now() -> datetime:
    """approval expiry는 local wall clock의 UTC instant로만 비교한다."""

    return datetime.now(UTC)


def _monotonic() -> float:
    """source I/O deadline은 wall-clock 보정 영향을 받지 않는 monotonic clock을 사용한다."""

    return time.monotonic()


class _Oa112SourceDeadline:
    """한 OA source의 DNS부터 body까지를 취소 가능한 절대 deadline으로 묶는다.

    Socket idle timeout만으로는 공격자가 작은 조각을 계속 보내며 connection을 점유할 수 있다.
    따라서 watchdog은 deadline에 열린 socket을 닫고, DNS worker도 같은 남은 시간을 넘기지 못한다.
    """

    def __init__(self, *, expires_at: datetime) -> None:
        approval_remaining = (expires_at - _utc_now()).total_seconds()
        if not math.isfinite(approval_remaining) or approval_remaining <= 0:
            raise Oa112DownloadError("OA112_PACKET_EXPIRED")
        remaining = min(_MAX_SOURCE_ELAPSED_SECONDS, approval_remaining)
        self._expires_at = expires_at
        self._monotonic_deadline = _monotonic() + remaining
        self._approval_is_limiting = approval_remaining <= _MAX_SOURCE_ELAPSED_SECONDS
        self._lock = threading.RLock()
        self._cancellers: dict[int, Callable[[], None]] = {}
        self._next_canceller_id = 0
        self._closed = False
        self._timer_expired = False
        self._timer = threading.Timer(remaining, self._expire)
        self._timer.daemon = True
        self._timer.start()

    def __enter__(self) -> _Oa112SourceDeadline:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        """정상 종료 시 watchdog callback을 제거해 이후 socket 재사용을 막는다."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancellers.clear()
        self._timer.cancel()

    def remaining_seconds(self) -> float:
        """다음 blocking operation에 허용되는 양의 timeout만 반환한다."""

        source_remaining = self._monotonic_deadline - _monotonic()
        approval_remaining = (self._expires_at - _utc_now()).total_seconds()
        with self._lock:
            timer_expired = self._timer_expired
        if (
            not math.isfinite(source_remaining)
            or not math.isfinite(approval_remaining)
            or approval_remaining <= 0
            or (timer_expired and self._approval_is_limiting)
        ):
            raise Oa112DownloadError("OA112_PACKET_EXPIRED")
        if source_remaining <= 0 or timer_expired:
            raise Oa112DownloadError("OA112_DOWNLOAD_TIME_BOUND")
        return min(source_remaining, approval_remaining)

    def register_canceller(self, canceller: Callable[[], None]) -> Callable[[], None]:
        """watchdog가 source deadline에 socket을 즉시 깨울 수 있도록 callback을 등록한다."""

        run_now = False
        with self._lock:
            if self._closed:
                raise Oa112DownloadError("OA112_DOWNLOAD_CONNECTION_STATE")
            if self._timer_expired:
                run_now = True
                canceller_id = -1
            else:
                canceller_id = self._next_canceller_id
                self._next_canceller_id += 1
                self._cancellers[canceller_id] = canceller
        if run_now:
            _run_canceller(canceller)

        def unregister() -> None:
            with self._lock:
                self._cancellers.pop(canceller_id, None)

        return unregister

    def _expire(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._timer_expired = True
            cancellers = tuple(self._cancellers.values())
        for canceller in cancellers:
            _run_canceller(canceller)


def _run_canceller(canceller: Callable[[], None]) -> None:
    try:
        canceller()
    except OSError:
        # Deadline cancellation은 best-effort wakeup이고 caller가 typed boundary를 반환한다.
        return


class _SocketOa112DnsResolver:
    """fresh Python worker를 timeout 뒤 kill해 libc DNS가 멈춰도 caller를 점유하지 않는다."""

    def __init__(self, *, worker_command: Sequence[str] | None = None) -> None:
        command = worker_command or (sys.executable, "-I", "-c", _DNS_RESOLVER_WORKER)
        if not command or any(not item or "\x00" in item for item in command):
            raise ValueError("OA112 DNS worker command is invalid")
        self._worker_command = tuple(command)

    def resolve(self, hostname: str, *, timeout_seconds: float) -> list[str]:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise Oa112DownloadError("OA112_DOWNLOAD_TIME_BOUND")
        payload = json.dumps({"hostname": hostname}, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        if not payload or len(payload) >= _DNS_WORKER_INPUT_MAX_BYTES:
            raise Oa112DownloadError("OA112_DOWNLOAD_DNS")
        try:
            process = subprocess.Popen(
                self._worker_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as error:
            raise Oa112DownloadError("OA112_DOWNLOAD_DNS") from error
        try:
            output, _ = process.communicate(input=payload, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.communicate()
            raise Oa112DownloadError("OA112_DOWNLOAD_TIME_BOUND") from error
        except OSError as error:
            process.kill()
            process.communicate()
            raise Oa112DownloadError("OA112_DOWNLOAD_DNS") from error
        if process.returncode != 0 or not output or len(output) > _DNS_WORKER_OUTPUT_MAX_BYTES:
            raise Oa112DownloadError("OA112_DOWNLOAD_DNS")
        try:
            decoded = json.loads(output.decode("utf-8"))
            addresses = decoded["addresses"]
            if (
                set(decoded) != {"addresses"}
                or not isinstance(addresses, list)
                or not 1 <= len(addresses) <= 32
                or any(not isinstance(value, str) for value in addresses)
            ):
                raise ValueError
            return addresses
        except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise Oa112DownloadError("OA112_DOWNLOAD_DNS") from error


def _abort_socket(connected_socket: socket.socket | ssl.SSLSocket | None) -> None:
    """watchdog thread가 blocking connect/TLS/header/body read를 즉시 깨우도록 socket을 닫는다."""

    if connected_socket is None:
        return
    try:
        connected_socket.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        connected_socket.close()
    except OSError:
        pass


class _StdlibOa112DownloadResponse:
    def __init__(
        self, response: http.client.HTTPResponse, *, connected_socket: ssl.SSLSocket
    ) -> None:
        self._response = response
        self._socket = connected_socket
        self.status_code = response.status
        headers: dict[str, str] = {}
        for key, value in response.getheaders():
            normalized = key.strip().lower()
            if normalized in headers:
                raise Oa112DownloadError("OA112_DOWNLOAD_RESPONSE_HEADER_DUPLICATE")
            headers[normalized] = value.strip()
        self.headers = headers

    def set_read_timeout_seconds(self, *, timeout_seconds: float) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise Oa112DownloadError("OA112_DOWNLOAD_TIME_BOUND")
        self._socket.settimeout(timeout_seconds)

    def iter_raw(self, *, chunk_size: int) -> Iterator[bytes]:
        while True:
            payload = self._response.read(chunk_size)
            if not payload:
                return
            yield payload


class _StdlibOa112HttpsConnection:
    def __init__(
        self,
        *,
        hostname: str,
        pinned_ip: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        deadline: _Oa112SourceDeadline,
    ) -> None:
        self._deadline = deadline
        self._socket_lock = threading.RLock()
        self._socket: socket.socket | ssl.SSLSocket | None = None
        self._response: http.client.HTTPResponse | None = None
        self._request_sent = False
        self._unregister_canceller = self._deadline.register_canceller(self._abort)
        raw_socket: socket.socket | None = None
        try:
            self._deadline.remaining_seconds()
            parsed_ip = ipaddress.ip_address(pinned_ip)
            family = socket.AF_INET if parsed_ip.version == 4 else socket.AF_INET6
            raw_socket = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
            self._bind_socket(raw_socket)
            raw_socket.settimeout(connect_timeout_seconds)
            if parsed_ip.version == 4:
                raw_socket.connect((pinned_ip, 443))
            else:
                raw_socket.connect((pinned_ip, 443, 0, 0))
            self._deadline.remaining_seconds()
            self.peer_ip = str(ipaddress.ip_address(raw_socket.getpeername()[0]))
            if self.peer_ip != str(parsed_ip):
                raise Oa112DownloadError("OA112_DOWNLOAD_PEER_PIN_MISMATCH")
            wrapped_socket = ssl.create_default_context().wrap_socket(
                raw_socket,
                server_hostname=hostname,
            )
            self._bind_socket(wrapped_socket)
            raw_socket = None
            self._deadline.remaining_seconds()
        except Oa112DownloadError:
            self._close()
            raise
        except (OSError, ValueError) as error:
            try:
                self._deadline.remaining_seconds()
            except Oa112DownloadError as deadline_error:
                self._close()
                raise deadline_error from error
            self._close()
            raise Oa112DownloadError("OA112_DOWNLOAD_TRANSPORT") from error
        finally:
            if raw_socket is not None:
                _abort_socket(raw_socket)

    def __enter__(self) -> Oa112HttpsConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        if self._response is not None:
            self._response.close()
        self._close()

    def get(
        self,
        *,
        target: str,
        headers: Mapping[str, str],
        read_timeout_seconds: float,
    ) -> Oa112DownloadResponse:
        if self._request_sent or self._socket is None:
            raise Oa112DownloadError("OA112_DOWNLOAD_CONNECTION_STATE")
        if not math.isfinite(read_timeout_seconds) or read_timeout_seconds <= 0:
            raise Oa112DownloadError("OA112_DOWNLOAD_TIME_BOUND")
        if (
            not target.startswith("/")
            or "\r" in target
            or "\n" in target
            or any(
                not key or "\r" in key or "\n" in key or "\r" in value or "\n" in value
                for key, value in headers.items()
            )
        ):
            raise Oa112DownloadError("OA112_DOWNLOAD_REQUEST_BOUNDARY")
        self._request_sent = True
        request = [f"GET {target} HTTP/1.1\r\n"]
        request.extend(f"{key}: {value}\r\n" for key, value in headers.items())
        request.append("\r\n")
        try:
            self._deadline.remaining_seconds()
            connected_socket = self._current_ssl_socket()
            connected_socket.settimeout(read_timeout_seconds)
            connected_socket.sendall("".join(request).encode("ascii", errors="strict"))
            self._deadline.remaining_seconds()
            self._response = http.client.HTTPResponse(connected_socket)
            self._response.begin()
            self._deadline.remaining_seconds()
            return _StdlibOa112DownloadResponse(
                self._response,
                connected_socket=connected_socket,
            )
        except (OSError, UnicodeEncodeError, http.client.HTTPException) as error:
            try:
                self._deadline.remaining_seconds()
            except Oa112DownloadError as deadline_error:
                raise deadline_error from error
            raise Oa112DownloadError("OA112_DOWNLOAD_TRANSPORT") from error

    def _current_ssl_socket(self) -> ssl.SSLSocket:
        with self._socket_lock:
            if not isinstance(self._socket, ssl.SSLSocket):
                raise Oa112DownloadError("OA112_DOWNLOAD_CONNECTION_STATE")
            return self._socket

    def _abort(self) -> None:
        with self._socket_lock:
            connected_socket = self._socket
        _abort_socket(connected_socket)

    def _bind_socket(self, connected_socket: socket.socket | ssl.SSLSocket) -> None:
        """watchdog 등록과 socket 생성 사이의 race에서도 새 descriptor를 즉시 닫는다."""

        with self._socket_lock:
            self._socket = connected_socket
        try:
            self._deadline.remaining_seconds()
        except Oa112DownloadError:
            _abort_socket(connected_socket)
            raise

    def _close(self) -> None:
        self._unregister_canceller()
        self._abort()


class _StdlibOa112HttpsTransport:
    def connect(
        self,
        *,
        hostname: str,
        pinned_ip: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        deadline: _Oa112SourceDeadline,
    ) -> Oa112HttpsConnection:
        return _StdlibOa112HttpsConnection(
            hostname=hostname,
            pinned_ip=pinned_ip,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            deadline=deadline,
        )


def load_oa112_download_packet(
    *,
    approved_root: Path,
    relative_path: str,
    now: datetime | None = None,
) -> Oa112DownloadPacket:
    """operator가 0600 local packet root에 둔 packet만 strict JSON으로 읽는다.

    이 loader는 packet 발급이나 승인 확인을 대신하지 않는다. caller는 packet을 source registry와
    현재 HEAD/CI/security evidence에 맞춰 별도로 준비해야 하며, executor는 consume 전 다시
    registry/source/cap/expiry를 확인한다.
    """

    if not _is_leaf(relative_path):
        raise Oa112DownloadError("OA112_PACKET_UNSAFE")
    content = _read_private_control_file(
        root=approved_root, name=relative_path, maximum=_MAX_PACKET_BYTES
    )
    try:
        payload = _parse_canonical_json(content)
        packet = _packet_from_payload(payload)
    except Oa112DownloadError:
        raise
    except (TypeError, ValueError) as error:
        raise Oa112DownloadError("OA112_PACKET_INVALID") from error
    check_now = now or _utc_now()
    if (
        packet.expires_at.tzinfo != UTC
        or not check_now < packet.expires_at <= check_now + timedelta(hours=1)
    ):
        raise Oa112DownloadError("OA112_PACKET_INVALID")
    return packet


def load_oa112_execution_binding(
    *,
    approved_root: Path,
    relative_path: str,
    repository_root: Path,
) -> Oa112DownloadBinding:
    """local evidence와 실제 clean Git HEAD/tree를 결합해 one-shot packet binding을 만든다.

    CI/security digest는 external workflow 결과이므로 local-only evidence file에서만 읽는다. 반면
    HEAD/tree는 ambient environment를 믿지 않고 execution 직전 Git object에서 다시 계산해 stale
    packet이 다른 tracked code에 대해 provider socket을 열지 못하게 한다.
    """

    if not _is_leaf(relative_path):
        raise Oa112DownloadError("OA112_EXECUTION_EVIDENCE_UNSAFE")
    content = _read_private_control_file(
        root=approved_root,
        name=relative_path,
        maximum=_MAX_PACKET_BYTES,
        error_code="OA112_EXECUTION_EVIDENCE_UNSAFE",
    )
    try:
        payload = _parse_canonical_json(content)
        if set(payload) != {"ciDigest", "headSha", "securityDigest", "treeSha256"}:
            raise Oa112DownloadError("OA112_EXECUTION_EVIDENCE_INVALID")
        binding = Oa112DownloadBinding(
            head_sha=_required_text(payload, "headSha", maximum=40),
            tree_sha256=_required_text(payload, "treeSha256", maximum=64),
            ci_digest=_required_text(payload, "ciDigest", maximum=64),
            security_digest=_required_text(payload, "securityDigest", maximum=64),
        )
        binding.validate()
    except Oa112DownloadError:
        raise
    except (TypeError, ValueError) as error:
        raise Oa112DownloadError("OA112_EXECUTION_EVIDENCE_INVALID") from error
    actual_head_sha, actual_tree_sha256 = _current_clean_git_identity(repository_root)
    if binding.head_sha != actual_head_sha or binding.tree_sha256 != actual_tree_sha256:
        raise Oa112DownloadError("OA112_EXECUTION_EVIDENCE_GIT_DRIFT")
    return binding


def oa112_source_endpoint_digest(entries: Sequence[Oa112RegistryEntry]) -> str:
    """승인 packet에는 원문 URL 대신 ordered source/origin/endpoint identity digest만 결속한다."""

    try:
        projection = [
            {
                "endpoint": _endpoint_from_https_url(entry.canonical_url),
                "origin": _origin_from_https_url(entry.canonical_url),
                "sourceId": entry.source_id,
            }
            for entry in entries
        ]
    except Oa112DownloadError:
        raise
    encoded = json.dumps(
        projection,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def consume_oa112_download_packet(*, packet: Oa112DownloadPacket, control_root: Path) -> None:
    """one-shot packet nonce를 local 0700 control root에서 atomically consume한다."""

    root_fd = _open_private_root(control_root, error_code="OA112_PACKET_UNSAFE")
    claims_fd = -1
    try:
        claims_fd = _open_or_create_private_directory(root_fd, "oa112-packet-claims")
        claim_name = hashlib.sha256(f"oa112-download-nonce\0{packet.nonce}".encode()).hexdigest()
        content = json.dumps(
            {
                "packetDigest": _packet_digest(packet),
                "state": "CONSUMED",
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        _write_new_private_file(claims_fd, claim_name, content)
        os.fsync(claims_fd)
    except FileExistsError as error:
        raise Oa112DownloadError("OA112_PACKET_ALREADY_CONSUMED") from error
    except Oa112DownloadError:
        raise
    except OSError as error:
        raise Oa112DownloadError("OA112_PACKET_UNSAFE") from error
    finally:
        if claims_fd >= 0:
            os.close(claims_fd)
        os.close(root_fd)


def _write_oa112_run_receipt(
    *,
    control_root: Path,
    packet: Oa112DownloadPacket,
    registry_digest: str,
    state: str,
    code: str,
    attempt_count: int,
    physical_call_count: int,
    downloaded_source_count: int,
    reused_source_count: int,
) -> None:
    """소비된 packet마다 raw URL·body 없이 하나의 durable physical outcome을 남긴다."""

    if (
        state not in {"FAILED", "SUCCEEDED"}
        or not code
        or min(attempt_count, physical_call_count, downloaded_source_count, reused_source_count) < 0
        or physical_call_count > attempt_count
        or downloaded_source_count + reused_source_count > len(packet.source_ids)
    ):
        raise Oa112DownloadError("OA112_DOWNLOAD_RECEIPT_UNAVAILABLE")
    root_fd = -1
    receipts_fd = -1
    try:
        root_fd = _open_private_root(control_root, error_code="OA112_DOWNLOAD_RECEIPT_UNAVAILABLE")
        receipts_fd = _open_or_create_private_directory(root_fd, "oa112-download-receipts")
        payload = (
            json.dumps(
                {
                    "attemptCount": attempt_count,
                    "code": code,
                    "downloadedSourceCount": downloaded_source_count,
                    "packetDigest": _packet_digest(packet),
                    "physicalCallCount": physical_call_count,
                    "registryDigest": registry_digest,
                    "reusedSourceCount": reused_source_count,
                    "state": state,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        _write_new_private_file(
            receipts_fd,
            f"{_packet_digest(packet)}.json",
            payload,
        )
        os.fsync(receipts_fd)
    except Oa112DownloadError:
        raise
    except (FileExistsError, OSError) as error:
        raise Oa112DownloadError("OA112_DOWNLOAD_RECEIPT_UNAVAILABLE") from error
    finally:
        if receipts_fd >= 0:
            os.close(receipts_fd)
        if root_fd >= 0:
            os.close(root_fd)


def download_oa112_local_cache(
    *,
    entries: Sequence[Oa112RegistryEntry],
    registry_digest: str,
    packet: Oa112DownloadPacket | None,
    local_cache_root: Path,
    packet_control_root: Path,
    execution_binding: Oa112DownloadBinding | None = None,
    resolver: Oa112DnsResolver | None = None,
    transport: Oa112HttpsTransport | None = None,
    now: datetime | None = None,
) -> Oa112DownloadReceipt:
    """packet-bound OA source를 one-pass로 local raw cache에만 내려받는다.

    pre-existing hash-verified raw는 재사용하지만 mismatch는 source drift로 중단한다. pending partial은
    다음 새 packet에서 exact Range/Content-Range 응답일 때만 resume한다. redirect, DNS rebinding,
    MIME/magic mismatch, page/byte overrun, retry는 모두 fail-closed이며 첫 실패 뒤 나머지 request는
    생성하지 않는다.
    """

    selected = tuple(entries)
    check_now = now or _utc_now()
    if not selected or len(selected) > 112 or _SHA256.fullmatch(registry_digest) is None:
        raise Oa112DownloadError("OA112_DOWNLOAD_INPUT_INVALID")
    if len({entry.source_id for entry in selected}) != len(selected):
        raise Oa112DownloadError("OA112_DOWNLOAD_INPUT_INVALID")
    root_fd, raw_fd, staging_fd = _open_cache_layout(local_cache_root)
    attempt_count = 0
    physical_call_count = 0
    consumed_packet: Oa112DownloadPacket | None = None
    outcome_receipt_attempted = False
    try:
        receipts: dict[str, Oa112DownloadedSourceReceipt] = {}
        pending: list[Oa112RegistryEntry] = []
        for entry in selected:
            cached = _read_verified_cached_raw(
                raw_fd=raw_fd,
                entry=entry,
                maximum_source_bytes=_MAX_SOURCE_BYTES,
                maximum_pages=500,
            )
            if cached is None:
                pending.append(entry)
            else:
                receipts[entry.source_id] = cached
        if pending:
            if packet is None:
                raise Oa112DownloadError("OA112_PACKET_REQUIRED")
            if execution_binding is None:
                raise Oa112DownloadError("OA112_EXECUTION_EVIDENCE_REQUIRED")
            packet.validate(
                entries=selected,
                registry_digest=registry_digest,
                execution_binding=execution_binding,
                now=check_now,
            )
            consume_oa112_download_packet(packet=packet, control_root=packet_control_root)
            consumed_packet = packet
            active_resolver = resolver or _SocketOa112DnsResolver()
            active_transport = transport or _StdlibOa112HttpsTransport()
            total_bytes = 0
            for entry in pending:
                if physical_call_count >= packet.physical_call_cap:
                    raise Oa112DownloadError(
                        "OA112_PACKET_PHYSICAL_CAP",
                        attempt_count=attempt_count,
                        physical_call_count=physical_call_count,
                    )
                # packet expiry는 새 request를 만들기 직전에도 다시 확인한다.
                with _Oa112SourceDeadline(expires_at=packet.expires_at) as deadline:
                    deadline.remaining_seconds()
                    attempt_count += 1

                    def record_provider_request() -> None:
                        nonlocal physical_call_count
                        if physical_call_count >= packet.physical_call_cap:
                            raise Oa112DownloadError(
                                "OA112_PACKET_PHYSICAL_CAP",
                                attempt_count=attempt_count,
                                physical_call_count=physical_call_count,
                            )
                        physical_call_count += 1

                    receipt = _download_entry(
                        raw_fd=raw_fd,
                        staging_fd=staging_fd,
                        entry=entry,
                        registry_digest=registry_digest,
                        maximum_source_bytes=min(
                            packet.maximum_source_bytes,
                            packet.maximum_total_bytes - total_bytes,
                        ),
                        maximum_pages=packet.maximum_pages,
                        resolver=active_resolver,
                        transport=active_transport,
                        deadline=deadline,
                        record_provider_request=record_provider_request,
                    )
                total_bytes += receipt.bytes_read
                if total_bytes > packet.maximum_total_bytes:
                    raise Oa112DownloadError(
                        "OA112_PACKET_TOTAL_BYTE_CAP",
                        attempt_count=attempt_count,
                        physical_call_count=physical_call_count,
                    )
                receipts[entry.source_id] = receipt
        ordered = tuple(receipts[entry.source_id] for entry in selected)
        result = Oa112DownloadReceipt(
            attempt_count=attempt_count,
            physical_call_count=physical_call_count,
            downloaded_source_count=sum(
                item.state in {"DOWNLOADED", "RESUMED"} for item in ordered
            ),
            reused_source_count=sum(item.state == "REUSED" for item in ordered),
            sources=ordered,
        )
        if consumed_packet is not None:
            outcome_receipt_attempted = True
            _write_oa112_run_receipt(
                control_root=packet_control_root,
                packet=consumed_packet,
                registry_digest=registry_digest,
                state="SUCCEEDED",
                code="OA112_LOCAL_CACHE_READY",
                attempt_count=attempt_count,
                physical_call_count=physical_call_count,
                downloaded_source_count=result.downloaded_source_count,
                reused_source_count=result.reused_source_count,
            )
        return result
    except Oa112DownloadError as error:
        surfaced_error = error
        if error.attempt_count != attempt_count or error.physical_call_count != physical_call_count:
            surfaced_error = Oa112DownloadError(
                error.code,
                attempt_count=attempt_count,
                physical_call_count=physical_call_count,
            )
        if consumed_packet is not None and not outcome_receipt_attempted:
            outcome_receipt_attempted = True
            try:
                _write_oa112_run_receipt(
                    control_root=packet_control_root,
                    packet=consumed_packet,
                    registry_digest=registry_digest,
                    state="FAILED",
                    code=surfaced_error.code,
                    attempt_count=attempt_count,
                    physical_call_count=physical_call_count,
                    downloaded_source_count=0,
                    reused_source_count=sum(item.state == "REUSED" for item in receipts.values()),
                )
            except Oa112DownloadError as receipt_error:
                raise Oa112DownloadError(
                    receipt_error.code,
                    attempt_count=attempt_count,
                    physical_call_count=physical_call_count,
                ) from surfaced_error
            raise Oa112DownloadError(
                surfaced_error.code,
                attempt_count=attempt_count,
                physical_call_count=physical_call_count,
                failure_receipt_written=True,
            ) from error
        raise surfaced_error from error
    finally:
        os.close(staging_fd)
        os.close(raw_fd)
        os.close(root_fd)


def _download_entry(
    *,
    raw_fd: int,
    staging_fd: int,
    entry: Oa112RegistryEntry,
    registry_digest: str,
    maximum_source_bytes: int,
    maximum_pages: int,
    resolver: Oa112DnsResolver,
    transport: Oa112HttpsTransport,
    deadline: _Oa112SourceDeadline,
    record_provider_request: Callable[[], None],
) -> Oa112DownloadedSourceReceipt:
    deadline.remaining_seconds()
    if maximum_source_bytes <= 0:
        raise Oa112DownloadError("OA112_PACKET_TOTAL_BYTE_CAP")
    part_name = f"{entry.source_id}.part"
    state_name = f"{entry.source_id}.resume.json"
    raw_name = oa112_raw_cache_filename(entry)
    offset = _load_resume_offset(
        staging_fd=staging_fd,
        part_name=part_name,
        state_name=state_name,
        entry=entry,
        registry_digest=registry_digest,
        maximum_source_bytes=maximum_source_bytes,
    )
    if offset is None:
        _write_new_private_file(
            staging_fd,
            state_name,
            _resume_state(entry=entry, registry_digest=registry_digest),
        )
        try:
            part_fd = os.open(part_name, _WRITE_NEW_FLAGS, 0o600, dir_fd=staging_fd)
        except OSError as error:
            _unlink_if_present(staging_fd, state_name)
            raise Oa112DownloadError("OA112_CACHE_STAGING_UNSAFE") from error
        offset = 0
    else:
        try:
            part_fd = os.open(part_name, _WRITE_APPEND_FLAGS, dir_fd=staging_fd)
            _validate_private_regular(os.fstat(part_fd))
        except OSError as error:
            raise Oa112DownloadError("OA112_CACHE_STAGING_UNSAFE") from error
    try:
        with _open_checked_response(
            entry.canonical_url,
            resolver=resolver,
            transport=transport,
            deadline=deadline,
            record_provider_request=record_provider_request,
        ) as response:
            plan = _validate_response(
                response=response,
                entry=entry,
                offset=offset,
                maximum_source_bytes=maximum_source_bytes,
                deadline=deadline,
            )
            bytes_written = _stream_to_part(
                response=response,
                part_fd=part_fd,
                initial_bytes=offset,
                maximum_source_bytes=maximum_source_bytes,
                declared_bytes=plan.declared_bytes,
                expected_total_bytes=plan.total_bytes,
                deadline=deadline,
            )
        deadline.remaining_seconds()
        os.fsync(part_fd)
    except Oa112DownloadError as error:
        if error.code == "OA112_DOWNLOAD_TRANSPORT":
            try:
                deadline.remaining_seconds()
            except Oa112DownloadError as deadline_error:
                _retain_nonempty_partial(
                    part_fd=part_fd,
                    staging_fd=staging_fd,
                    part_name=part_name,
                    state_name=state_name,
                )
                raise deadline_error from error
        if error.code in {
            "OA112_DOWNLOAD_TRANSPORT",
            "OA112_DOWNLOAD_TIME_BOUND",
            "OA112_PACKET_EXPIRED",
        }:
            _retain_nonempty_partial(
                part_fd=part_fd,
                staging_fd=staging_fd,
                part_name=part_name,
                state_name=state_name,
            )
        else:
            _remove_partial(staging_fd=staging_fd, part_name=part_name, state_name=state_name)
        raise
    except (OSError, TimeoutError, http.client.HTTPException) as error:
        try:
            deadline.remaining_seconds()
        except Oa112DownloadError as deadline_error:
            _retain_nonempty_partial(
                part_fd=part_fd,
                staging_fd=staging_fd,
                part_name=part_name,
                state_name=state_name,
            )
            raise deadline_error from error
        _retain_nonempty_partial(
            part_fd=part_fd,
            staging_fd=staging_fd,
            part_name=part_name,
            state_name=state_name,
        )
        raise Oa112DownloadError("OA112_DOWNLOAD_TRANSPORT") from error
    finally:
        os.close(part_fd)

    try:
        payload = _read_private_file(staging_fd, part_name, maximum=maximum_source_bytes)
        if len(payload) != bytes_written:
            raise Oa112DownloadError("OA112_CACHE_STAGING_UNSAFE")
        if hashlib.sha256(payload).hexdigest() != entry.raw_content_sha256:
            raise Oa112DownloadError("OA112_DOWNLOAD_SOURCE_DRIFT")
        _validate_complete_payload(
            payload=payload,
            mime_type=entry.mime_type,
            maximum_pages=maximum_pages,
        )
        _publish_part_to_raw(
            staging_fd=staging_fd,
            raw_fd=raw_fd,
            part_name=part_name,
            raw_name=raw_name,
        )
        _unlink_if_present(staging_fd, state_name)
        os.fsync(staging_fd)
        os.fsync(raw_fd)
    except Oa112DownloadError:
        _remove_partial(staging_fd=staging_fd, part_name=part_name, state_name=state_name)
        raise
    except OSError as error:
        _remove_partial(staging_fd=staging_fd, part_name=part_name, state_name=state_name)
        raise Oa112DownloadError("OA112_CACHE_STAGING_UNSAFE") from error
    return Oa112DownloadedSourceReceipt(
        source_id=entry.source_id,
        source_revision_id=entry.source_revision_id,
        raw_content_sha256=entry.raw_content_sha256,
        bytes_read=bytes_written,
        state="RESUMED" if offset else "DOWNLOADED",
    )


@contextmanager
def _open_checked_response(
    url: str,
    *,
    resolver: Oa112DnsResolver,
    transport: Oa112HttpsTransport,
    deadline: _Oa112SourceDeadline,
    record_provider_request: Callable[[], None],
) -> Iterator[Oa112DownloadResponse]:
    parsed = _parse_https_url(url)
    hostname = parsed.hostname
    assert hostname is not None
    first_addresses = _resolve_public_addresses(hostname, resolver=resolver, deadline=deadline)
    second_addresses = _resolve_public_addresses(hostname, resolver=resolver, deadline=deadline)
    if set(first_addresses) != set(second_addresses):
        raise Oa112DownloadError("OA112_DOWNLOAD_DNS_REBINDING")
    target = urlunsplit(("", "", parsed.path, parsed.query, ""))
    headers = {
        "Accept-Encoding": "identity",
        "Connection": "close",
        "Host": hostname,
        "User-Agent": "capstone-oa112-local-materializer/1",
    }
    # _download_entry adds Range/Accept after connection open without allowing a caller origin override.
    try:
        remaining = deadline.remaining_seconds()
        with transport.connect(
            hostname=hostname,
            pinned_ip=first_addresses[0],
            connect_timeout_seconds=min(_CONNECT_TIMEOUT_SECONDS, remaining),
            read_timeout_seconds=min(_READ_TIMEOUT_SECONDS, remaining),
            deadline=deadline,
        ) as connection:
            deadline.remaining_seconds()
            _validate_peer(connection.peer_ip, first_addresses)
            # The context carries a checked connection only. The caller must supply its exact header map.
            response = _DeferredHeaderResponse(
                connection=connection,
                target=target,
                base_headers=headers,
                deadline=deadline,
                record_provider_request=record_provider_request,
            )
            yield response
    except Oa112DownloadError:
        raise
    except (OSError, TimeoutError, http.client.HTTPException) as error:
        raise Oa112DownloadError("OA112_DOWNLOAD_TRANSPORT") from error


class _DeferredHeaderResponse:
    """one-request connection에 caller-derived Range/Accept headers를 한 번만 bind한다."""

    def __init__(
        self,
        *,
        connection: Oa112HttpsConnection,
        target: str,
        base_headers: Mapping[str, str],
        deadline: _Oa112SourceDeadline,
        record_provider_request: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._target = target
        self._base_headers = dict(base_headers)
        self._deadline = deadline
        self._record_provider_request = record_provider_request
        self._response: Oa112DownloadResponse | None = None

    @property
    def status_code(self) -> int:
        return self._ensure_response().status_code

    @property
    def headers(self) -> Mapping[str, str]:
        return self._ensure_response().headers

    def bind_headers(self, headers: Mapping[str, str]) -> None:
        if self._response is not None:
            raise Oa112DownloadError("OA112_DOWNLOAD_CONNECTION_STATE")
        merged = dict(self._base_headers)
        for key, value in headers.items():
            if key in {"Host", "Connection", "Accept-Encoding", "User-Agent"}:
                if merged.get(key) != value:
                    raise Oa112DownloadError("OA112_DOWNLOAD_REQUEST_BOUNDARY")
            else:
                merged[key] = value
        remaining = self._deadline.remaining_seconds()
        self._record_provider_request()
        self._response = self._connection.get(
            target=self._target,
            headers=merged,
            read_timeout_seconds=min(_READ_TIMEOUT_SECONDS, remaining),
        )
        self._deadline.remaining_seconds()

    def set_read_timeout_seconds(self, *, timeout_seconds: float) -> None:
        self._ensure_response().set_read_timeout_seconds(timeout_seconds=timeout_seconds)

    def iter_raw(self, *, chunk_size: int) -> Iterator[bytes]:
        return self._ensure_response().iter_raw(chunk_size=chunk_size)

    def _ensure_response(self) -> Oa112DownloadResponse:
        if self._response is None:
            raise Oa112DownloadError("OA112_DOWNLOAD_CONNECTION_STATE")
        return self._response


def _validate_response(
    *,
    response: Oa112DownloadResponse,
    entry: Oa112RegistryEntry,
    offset: int,
    maximum_source_bytes: int,
    deadline: _Oa112SourceDeadline,
) -> _ResponsePlan:
    if isinstance(response, _DeferredHeaderResponse):
        request_headers = {
            "Accept": entry.mime_type,
            "Accept-Encoding": "identity",
            "Connection": "close",
            "Host": _hostname(entry.canonical_url),
            "User-Agent": "capstone-oa112-local-materializer/1",
        }
        if offset:
            request_headers["Range"] = f"bytes={offset}-"
        response.bind_headers(request_headers)
    deadline.remaining_seconds()
    headers = _normalized_headers(response.headers)
    status = response.status_code
    deadline.remaining_seconds()
    if "location" in headers or status < 200 or status >= 300:
        raise Oa112DownloadError("OA112_DOWNLOAD_REDIRECT_OR_STATUS")
    if headers.get("content-encoding", "identity").lower() != "identity":
        raise Oa112DownloadError("OA112_DOWNLOAD_ENCODING")
    transfer_encoding = headers.get("transfer-encoding")
    if transfer_encoding is not None and transfer_encoding.lower() != "chunked":
        raise Oa112DownloadError("OA112_DOWNLOAD_TRANSFER_ENCODING")
    mime_type = headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
    if mime_type != entry.mime_type:
        raise Oa112DownloadError("OA112_DOWNLOAD_MIME")
    declared_bytes = _content_length(headers)
    if declared_bytes is not None and (
        declared_bytes <= 0 or offset + declared_bytes > maximum_source_bytes
    ):
        raise Oa112DownloadError("OA112_DOWNLOAD_BYTE_BOUND")
    if offset == 0:
        if status != 200 or "content-range" in headers:
            raise Oa112DownloadError("OA112_DOWNLOAD_STATUS")
        return _ResponsePlan(declared_bytes=declared_bytes, total_bytes=declared_bytes)
    if status != 206:
        raise Oa112DownloadError("OA112_DOWNLOAD_RESUME_STATUS")
    match = _CONTENT_RANGE.fullmatch(headers.get("content-range", ""))
    if match is None:
        raise Oa112DownloadError("OA112_DOWNLOAD_RESUME_RANGE")
    start, end, total = (int(item) for item in match.groups())
    if (
        start != offset
        or end < start
        or total != end + 1
        or total > maximum_source_bytes
        or (declared_bytes is not None and declared_bytes != end - start + 1)
    ):
        raise Oa112DownloadError("OA112_DOWNLOAD_RESUME_RANGE")
    return _ResponsePlan(declared_bytes=declared_bytes, total_bytes=total)


def _stream_to_part(
    *,
    response: Oa112DownloadResponse,
    part_fd: int,
    initial_bytes: int,
    maximum_source_bytes: int,
    declared_bytes: int | None,
    expected_total_bytes: int | None = None,
    deadline: _Oa112SourceDeadline,
) -> int:
    total = initial_bytes
    new_bytes = 0
    iterator = response.iter_raw(chunk_size=_DOWNLOAD_CHUNK_BYTES)
    while True:
        remaining = deadline.remaining_seconds()
        response.set_read_timeout_seconds(timeout_seconds=min(_READ_TIMEOUT_SECONDS, remaining))
        try:
            chunk = next(iterator)
        except StopIteration:
            break
        deadline.remaining_seconds()
        if not isinstance(chunk, bytes) or not chunk:
            raise Oa112DownloadError("OA112_DOWNLOAD_BODY_INVALID")
        total += len(chunk)
        new_bytes += len(chunk)
        if total > maximum_source_bytes:
            raise Oa112DownloadError("OA112_DOWNLOAD_BYTE_BOUND")
        _write_all(part_fd, chunk)
    if (
        new_bytes == 0
        or (declared_bytes is not None and new_bytes != declared_bytes)
        or (expected_total_bytes is not None and total != expected_total_bytes)
    ):
        raise Oa112DownloadError("OA112_DOWNLOAD_BODY_INVALID")
    return total


def _read_verified_cached_raw(
    *,
    raw_fd: int,
    entry: Oa112RegistryEntry,
    maximum_source_bytes: int,
    maximum_pages: int,
) -> Oa112DownloadedSourceReceipt | None:
    name = oa112_raw_cache_filename(entry)
    try:
        metadata = os.stat(name, dir_fd=raw_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise Oa112DownloadError("OA112_CACHE_UNSAFE") from error
    try:
        _validate_private_regular(metadata)
        payload = _read_private_file(raw_fd, name, maximum=maximum_source_bytes)
    except Oa112DownloadError:
        raise
    if hashlib.sha256(payload).hexdigest() != entry.raw_content_sha256:
        raise Oa112DownloadError("OA112_LOCAL_CACHE_SOURCE_DRIFT")
    _validate_complete_payload(
        payload=payload, mime_type=entry.mime_type, maximum_pages=maximum_pages
    )
    return Oa112DownloadedSourceReceipt(
        source_id=entry.source_id,
        source_revision_id=entry.source_revision_id,
        raw_content_sha256=entry.raw_content_sha256,
        bytes_read=len(payload),
        state="REUSED",
    )


def _load_resume_offset(
    *,
    staging_fd: int,
    part_name: str,
    state_name: str,
    entry: Oa112RegistryEntry,
    registry_digest: str,
    maximum_source_bytes: int,
) -> int | None:
    part_metadata = _lstat_or_none(staging_fd, part_name)
    state_metadata = _lstat_or_none(staging_fd, state_name)
    if part_metadata is None and state_metadata is None:
        return None
    if part_metadata is None or state_metadata is None:
        raise Oa112DownloadError("OA112_CACHE_RESUME_STATE_INVALID")
    try:
        _validate_private_regular(part_metadata)
        _validate_private_regular(state_metadata)
        payload = _read_private_file(staging_fd, state_name, maximum=4_096)
        state = _parse_canonical_json(payload)
    except Oa112DownloadError:
        raise
    if (
        set(state) != _RESUME_STATE_FIELDS
        or state.get("sourceId") != entry.source_id
        or state.get("sourceRevisionId") != entry.source_revision_id
        or state.get("registryDigest") != registry_digest
        or state.get("rawContentSha256") != entry.raw_content_sha256
        or part_metadata.st_size <= 0
        or part_metadata.st_size >= maximum_source_bytes
    ):
        raise Oa112DownloadError("OA112_CACHE_RESUME_STATE_INVALID")
    return part_metadata.st_size


def _resume_state(*, entry: Oa112RegistryEntry, registry_digest: str) -> bytes:
    return (
        json.dumps(
            {
                "rawContentSha256": entry.raw_content_sha256,
                "registryDigest": registry_digest,
                "sourceId": entry.source_id,
                "sourceRevisionId": entry.source_revision_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _publish_part_to_raw(
    *,
    staging_fd: int,
    raw_fd: int,
    part_name: str,
    raw_name: str,
) -> None:
    if _lstat_or_none(raw_fd, raw_name) is not None:
        raise Oa112DownloadError("OA112_LOCAL_CACHE_SOURCE_DRIFT")
    try:
        os.link(
            part_name,
            raw_name,
            src_dir_fd=staging_fd,
            dst_dir_fd=raw_fd,
            follow_symlinks=False,
        )
        os.unlink(part_name, dir_fd=staging_fd)
    except FileExistsError as error:
        raise Oa112DownloadError("OA112_LOCAL_CACHE_SOURCE_DRIFT") from error


def _validate_complete_payload(*, payload: bytes, mime_type: str, maximum_pages: int) -> None:
    if not payload:
        raise Oa112DownloadError("OA112_DOWNLOAD_BODY_INVALID")
    if mime_type == "application/pdf":
        if not payload.startswith(b"%PDF-"):
            raise Oa112DownloadError("OA112_DOWNLOAD_MIME_MAGIC")
        try:
            document = fitz.open(stream=payload, filetype="pdf")
        except Exception as error:
            raise Oa112DownloadError("OA112_DOWNLOAD_MIME_MAGIC") from error
        try:
            if document.needs_pass or document.is_encrypted:
                raise Oa112DownloadError("OA112_DOWNLOAD_MIME_MAGIC")
            if document.page_count < 1 or document.page_count > maximum_pages:
                raise Oa112DownloadError("OA112_DOWNLOAD_PAGE_BOUND")
        finally:
            document.close()
        return
    if mime_type == "text/html" and not payload.lstrip().lower().startswith(
        (b"<!doctype html", b"<html")
    ):
        raise Oa112DownloadError("OA112_DOWNLOAD_MIME_MAGIC")
    if mime_type in {"text/html", "text/plain"}:
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise Oa112DownloadError("OA112_DOWNLOAD_MIME_MAGIC") from error
        if "\x00" in text:
            raise Oa112DownloadError("OA112_DOWNLOAD_MIME_MAGIC")


def _parse_https_url(value: str) -> SplitResult:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise Oa112DownloadError("OA112_DOWNLOAD_URL") from error
    hostname = parsed.hostname
    if hostname is not None:
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise Oa112DownloadError("OA112_DOWNLOAD_URL")
    if (
        parsed.scheme != "https"
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
        or not parsed.path.startswith("/")
        or "//" in parsed.path
        or any(segment in {".", ".."} for segment in parsed.path.split("/"))
        or any(
            character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        )
        or "\\" in value
    ):
        raise Oa112DownloadError("OA112_DOWNLOAD_URL")
    return parsed


def _origin_from_https_url(value: str) -> str:
    """origin digest에는 URL의 경로·query를 넣지 않아 source별 endpoint와 분리한다."""

    parsed = _parse_https_url(value)
    assert parsed.hostname is not None
    return f"https://{parsed.hostname.lower()}"


def _endpoint_from_https_url(value: str) -> str:
    """approved endpoint는 HTTPS path/query의 exact canonical projection만 사용한다."""

    parsed = _parse_https_url(value)
    return urlunsplit(("", "", parsed.path, parsed.query, ""))


def _current_clean_git_identity(repository_root: Path) -> tuple[str, str]:
    """실행 checkout이 clean한 current HEAD/tree인지 확인해 stale local evidence를 거부한다."""

    if not repository_root.is_absolute() or ".." in repository_root.parts:
        raise Oa112DownloadError("OA112_EXECUTION_EVIDENCE_GIT_UNAVAILABLE")
    commands = (
        ("status", "--porcelain=v1", "--untracked-files=no"),
        ("rev-parse", "HEAD"),
        ("cat-file", "tree", "HEAD^{tree}"),
    )
    outputs: list[bytes] = []
    for command in commands:
        try:
            completed = subprocess.run(
                ("git", "-C", str(repository_root), *command),
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5.0,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise Oa112DownloadError("OA112_EXECUTION_EVIDENCE_GIT_UNAVAILABLE") from error
        if completed.returncode != 0:
            raise Oa112DownloadError("OA112_EXECUTION_EVIDENCE_GIT_UNAVAILABLE")
        outputs.append(completed.stdout)
    if outputs[0]:
        raise Oa112DownloadError("OA112_EXECUTION_EVIDENCE_WORKTREE_DIRTY")
    try:
        head_sha = outputs[1].decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise Oa112DownloadError("OA112_EXECUTION_EVIDENCE_GIT_UNAVAILABLE") from error
    tree_sha256 = hashlib.sha256(outputs[2]).hexdigest()
    if _HEAD_SHA.fullmatch(head_sha) is None:
        raise Oa112DownloadError("OA112_EXECUTION_EVIDENCE_GIT_UNAVAILABLE")
    return head_sha, tree_sha256


def _hostname(url: str) -> str:
    parsed = _parse_https_url(url)
    assert parsed.hostname is not None
    return parsed.hostname


def _resolve_public_addresses(
    hostname: str,
    *,
    resolver: Oa112DnsResolver,
    deadline: _Oa112SourceDeadline,
) -> list[str]:
    try:
        addresses = [
            str(ipaddress.ip_address(value))
            for value in resolver.resolve(
                hostname,
                timeout_seconds=deadline.remaining_seconds(),
            )
        ]
        deadline.remaining_seconds()
        validate_resolved_addresses(hostname, addresses)
    except Oa112DownloadError:
        raise
    except (RagSourceRegistryError, OSError, ValueError) as error:
        raise Oa112DownloadError("OA112_DOWNLOAD_DNS") from error
    return sorted(
        set(addresses),
        key=lambda value: (ipaddress.ip_address(value).version, ipaddress.ip_address(value).packed),
    )


def _validate_peer(peer_ip: str, addresses: Sequence[str]) -> None:
    try:
        normalized = str(ipaddress.ip_address(peer_ip))
        validate_resolved_addresses("connected-peer", [normalized])
    except (RagSourceRegistryError, ValueError) as error:
        raise Oa112DownloadError("OA112_DOWNLOAD_PEER") from error
    if normalized not in set(addresses):
        raise Oa112DownloadError("OA112_DOWNLOAD_PEER")


def _normalized_headers(headers: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in headers.items():
        normalized = key.strip().lower()
        if (
            not normalized
            or normalized in result
            or not isinstance(value, str)
            or "\r" in value
            or "\n" in value
        ):
            raise Oa112DownloadError("OA112_DOWNLOAD_RESPONSE_HEADER")
        result[normalized] = value.strip()
    return result


def _content_length(headers: Mapping[str, str]) -> int | None:
    value = headers.get("content-length")
    if value is None:
        return None
    if not re.fullmatch(r"[0-9]+", value):
        raise Oa112DownloadError("OA112_DOWNLOAD_RESPONSE_HEADER")
    return int(value)


def _open_cache_layout(root: Path) -> tuple[int, int, int]:
    root_fd = _open_private_root(root, error_code="OA112_CACHE_UNSAFE")
    raw_fd = -1
    staging_fd = -1
    try:
        raw_fd = _open_or_create_private_directory(root_fd, "oa-raw")
        staging_fd = _open_or_create_private_directory(root_fd, "download-staging")
    except BaseException:
        if staging_fd >= 0:
            os.close(staging_fd)
        if raw_fd >= 0:
            os.close(raw_fd)
        os.close(root_fd)
        raise
    return root_fd, raw_fd, staging_fd


def _open_private_root(root: Path, *, error_code: str) -> int:
    if os.name != "posix" or not root.is_absolute() or ".." in root.parts:
        raise Oa112DownloadError(error_code)
    current_fd = -1
    try:
        current_fd = os.open("/", _DIRECTORY_FLAGS)
        for component in root.parts[1:]:
            next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        metadata = os.fstat(current_fd)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise Oa112DownloadError(error_code)
        return current_fd
    except Oa112DownloadError:
        if current_fd >= 0:
            os.close(current_fd)
        raise
    except OSError as error:
        if current_fd >= 0:
            os.close(current_fd)
        raise Oa112DownloadError(error_code) from error


def _open_or_create_private_directory(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileExistsError:
        pass
    except OSError as error:
        raise Oa112DownloadError("OA112_CACHE_UNSAFE") from error
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise Oa112DownloadError("OA112_CACHE_UNSAFE")
        return descriptor
    except BaseException:
        if "descriptor" in locals():
            os.close(descriptor)
        raise


def _read_private_control_file(
    *,
    root: Path,
    name: str,
    maximum: int,
    error_code: str = "OA112_PACKET_UNSAFE",
) -> bytes:
    root_fd = _open_private_root(root, error_code=error_code)
    try:
        return _read_private_file(root_fd, name, maximum=maximum)
    except Oa112DownloadError:
        raise Oa112DownloadError(error_code) from None
    finally:
        os.close(root_fd)


def _read_private_file(directory_fd: int, name: str, *, maximum: int) -> bytes:
    try:
        descriptor = os.open(name, _READ_FLAGS, dir_fd=directory_fd)
    except OSError as error:
        raise Oa112DownloadError("OA112_CACHE_UNSAFE") from error
    try:
        before = os.fstat(descriptor)
        _validate_private_regular(before)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(_DOWNLOAD_CHUNK_BYTES, maximum + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise Oa112DownloadError("OA112_CACHE_UNSAFE")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if _stable_metadata(before) != _stable_metadata(after):
            raise Oa112DownloadError("OA112_CACHE_UNSAFE")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_new_private_file(directory_fd: int, name: str, content: bytes) -> None:
    if not content or len(content) > _MAX_PACKET_BYTES:
        raise Oa112DownloadError("OA112_CACHE_UNSAFE")
    descriptor = os.open(name, _WRITE_NEW_FLAGS, 0o600, dir_fd=directory_fd)
    try:
        _write_all(descriptor, content)
        os.fsync(descriptor)
        _validate_private_regular(os.fstat(descriptor))
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, content: bytes) -> None:
    written = 0
    while written < len(content):
        count = os.write(descriptor, content[written:])
        if count <= 0:
            raise Oa112DownloadError("OA112_CACHE_STAGING_UNSAFE")
        written += count


def _validate_private_regular(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise Oa112DownloadError("OA112_CACHE_UNSAFE")


def _stable_metadata(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _lstat_or_none(directory_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise Oa112DownloadError("OA112_CACHE_UNSAFE") from error


def _unlink_if_present(directory_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        return


def _remove_partial(*, staging_fd: int, part_name: str, state_name: str) -> None:
    for name in (part_name, state_name):
        try:
            metadata = _lstat_or_none(staging_fd, name)
            if metadata is not None:
                _validate_private_regular(metadata)
                os.unlink(name, dir_fd=staging_fd)
        except Oa112DownloadError:
            return
        except OSError:
            return


def _retain_nonempty_partial(
    *,
    part_fd: int,
    staging_fd: int,
    part_name: str,
    state_name: str,
) -> None:
    """새 packet으로 Range resume할 수 있는 durable nonzero partial만 남긴다.

    watchdog가 header 전에 socket을 닫으면 empty part/state 쌍이 남을 수 있다. 그 쌍은 다음
    invocation을 resume-state-invalid으로 영구 차단하므로, size와 durability를 모두 확인한 뒤에만 보존한다.
    """

    try:
        os.fsync(part_fd)
        metadata = os.fstat(part_fd)
        _validate_private_regular(metadata)
        if metadata.st_size > 0:
            return
    except (OSError, Oa112DownloadError):
        pass
    _remove_partial(staging_fd=staging_fd, part_name=part_name, state_name=state_name)


def oa112_raw_cache_filename(entry: Oa112RegistryEntry) -> str:
    """verified OA raw cache의 fixed leaf name을 반환하고 caller-supplied path를 허용하지 않는다."""

    extension = {
        "application/pdf": ".pdf",
        "text/html": ".html",
        "text/plain": ".txt",
    }.get(entry.mime_type)
    if extension is None or _SOURCE_ID.fullmatch(entry.source_id) is None:
        raise Oa112DownloadError("OA112_DOWNLOAD_INPUT_INVALID")
    return f"{entry.source_id}{extension}"


def _packet_from_payload(payload: Mapping[str, object]) -> Oa112DownloadPacket:
    if set(payload) != _PACKET_FIELDS:
        raise Oa112DownloadError("OA112_PACKET_INVALID")
    expires_at = _parse_utc(_required_text(payload, "expiresAt", maximum=32))
    source_ids_value = payload.get("sourceIds")
    if not isinstance(source_ids_value, list):
        raise Oa112DownloadError("OA112_PACKET_INVALID")
    source_ids = tuple(_require_source_id(item) for item in source_ids_value)
    if len(source_ids) != len(set(source_ids)):
        raise Oa112DownloadError("OA112_PACKET_INVALID")
    return Oa112DownloadPacket(
        approval_id=_required_text(payload, "approvalId", maximum=128),
        head_sha=_required_text(payload, "headSha", maximum=40),
        tree_sha256=_required_text(payload, "treeSha256", maximum=64),
        ci_digest=_required_text(payload, "ciDigest", maximum=64),
        security_digest=_required_text(payload, "securityDigest", maximum=64),
        registry_digest=_required_text(payload, "registryDigest", maximum=64),
        source_endpoint_digest=_required_text(payload, "sourceEndpointDigest", maximum=64),
        source_ids=source_ids,
        provider=_required_text(payload, "provider", maximum=64),
        operation=_required_text(payload, "operation", maximum=64),
        query=_required_text(payload, "query", maximum=64),
        symbol=_required_text(payload, "symbol", maximum=64),
        date=_required_text(payload, "date", maximum=64),
        logical_call_cap=_required_int(payload, "logicalCallCap"),
        physical_call_cap=_required_int(payload, "physicalCallCap"),
        maximum_source_bytes=_required_int(payload, "maximumSourceBytes"),
        maximum_total_bytes=_required_int(payload, "maximumTotalBytes"),
        cost_cap_microusd=_required_int(payload, "costCapMicrousd"),
        retry_count=_required_int(payload, "retryCount"),
        tracked_raw_artifact_count=_required_int(payload, "trackedRawArtifactCount"),
        operator=_required_text(payload, "operator", maximum=128),
        expires_at=expires_at,
        nonce=_required_text(payload, "nonce", maximum=128),
        maximum_pages=_required_int(payload, "maximumPages"),
    )


def _packet_digest(packet: Oa112DownloadPacket) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "approvalId": packet.approval_id,
                "ciDigest": packet.ci_digest,
                "date": packet.date,
                "headSha": packet.head_sha,
                "maximumPages": packet.maximum_pages,
                "maximumSourceBytes": packet.maximum_source_bytes,
                "maximumTotalBytes": packet.maximum_total_bytes,
                "nonce": packet.nonce,
                "operation": packet.operation,
                "physicalCallCap": packet.physical_call_cap,
                "provider": packet.provider,
                "query": packet.query,
                "registryDigest": packet.registry_digest,
                "securityDigest": packet.security_digest,
                "sourceEndpointDigest": packet.source_endpoint_digest,
                "sourceIds": packet.source_ids,
                "symbol": packet.symbol,
                "treeSha256": packet.tree_sha256,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _parse_canonical_json(content: bytes) -> dict[str, object]:
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise Oa112DownloadError("OA112_PACKET_INVALID") from error
    if not text.endswith("\n") or "\r" in text:
        raise Oa112DownloadError("OA112_PACKET_INVALID")
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, TypeError) as error:
        raise Oa112DownloadError("OA112_PACKET_INVALID") from error
    if not isinstance(value, dict):
        raise Oa112DownloadError("OA112_PACKET_INVALID")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise Oa112DownloadError("OA112_PACKET_INVALID")
        result[key] = value
    return result


def _parse_utc(value: str) -> datetime:
    if not value.endswith("Z"):
        raise Oa112DownloadError("OA112_PACKET_INVALID")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise Oa112DownloadError("OA112_PACKET_INVALID") from error
    if parsed.tzinfo != UTC or parsed.isoformat().replace("+00:00", "Z") != value:
        raise Oa112DownloadError("OA112_PACKET_INVALID")
    return parsed


def _required_text(value: Mapping[str, object], key: str, *, maximum: int) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item or item != item.strip() or len(item) > maximum:
        raise Oa112DownloadError("OA112_PACKET_INVALID")
    return item


def _required_int(value: Mapping[str, object], key: str) -> int:
    item = value.get(key)
    if type(item) is not int:
        raise Oa112DownloadError("OA112_PACKET_INVALID")
    return item


def _require_source_id(value: object) -> str:
    if not isinstance(value, str) or _SOURCE_ID.fullmatch(value) is None:
        raise Oa112DownloadError("OA112_PACKET_INVALID")
    return value


def _is_leaf(relative_path: str) -> bool:
    if (
        not relative_path
        or relative_path.startswith("/")
        or "\\" in relative_path
        or "\x00" in relative_path
    ):
        return False
    parts = Path(relative_path).parts
    return len(parts) == 1 and parts[0] not in {"", ".", ".."}
