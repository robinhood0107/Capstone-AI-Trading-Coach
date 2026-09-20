"""GDELT bulk fixture의 framing/hash/압축 안전 경계다. 이 모듈은 socket을 열지 않는다."""

from __future__ import annotations

import gzip
import hashlib
import io
import re
import base64
import binascii
import ipaddress
import socket
import zlib
from dataclasses import dataclass
from typing import Callable, Final
from urllib.parse import urljoin, urlsplit

import httpx

_ALLOWED_HOSTS: Final = frozenset({"data.gdeltproject.org", "storage.googleapis.com"})
_GOOG_HASH = re.compile(r"^(crc32c|md5)=([A-Za-z0-9+/]+={0,2})$", re.IGNORECASE)
_CRC32C_POLYNOMIAL: Final = 0x82F63B78
_BULK_PATH = re.compile(r"^/(?:gdelt-open-data/)?gdeltv3/(gqg|gemg)/[0-9]{14}\.\1\.json\.gz$")


class GdeltWorldNewsTransportError(ValueError):
    """GDELT fixture가 bounded transport 계약을 위반했다."""


@dataclass(frozen=True, slots=True)
class GdeltResponseFixture:
    requested_url: str
    final_url: str
    redirect_count: int
    tls_hostname_verified: bool
    dns_public_addresses_verified: bool
    headers: tuple[tuple[str, str], ...]
    body: bytes


class GdeltHttpClient:
    """고정 origin GQG/GEMG 한 파일을 retry 없이 bounded raw bytes로 가져온다."""

    def __init__(
        self,
        *,
        network_enabled: bool = False,
        transport: httpx.BaseTransport | None = None,
        resolver: Callable[[str], tuple[str, ...]] | None = None,
        # 실측에서 GQG 한 파일이 32MiB 해제 한도를 넘어 수집이 끊겼다. 한도의 목적은
        # "정상 파일을 거르는 것"이 아니라 원격 파일 하나가 컨테이너를 죽이지 않는 것이다.
        #
        # 실측 파일은 압축 3MiB 안쪽이라 아래 값은 그 수십 배다. 완전히 없애지는 않는다 -
        # 압축분을 메모리에 모은 뒤 다시 전개하므로, 한도가 컨테이너 메모리(2GiB)에
        # 근접하면 OOM-kill 로 수집 전체가 멈춘다. 지금 없애려는 그 실패다.
        # 크기 초과는 건너뛸 수 있는 실패라(_SKIPPABLE_FILE_FAILURES) 한도에 걸려도
        # 그 파일만 빠지고 사이클은 계속된다.
        compressed_limit: int = 64 * 1024 * 1024,
        decompressed_limit: int = 256 * 1024 * 1024,
        physical_call_cap: int = 2,
    ) -> None:
        if (
            compressed_limit < 1
            or decompressed_limit < compressed_limit
            or physical_call_cap not in range(1, 10_001)
        ):
            raise ValueError("GDELT transport limits are invalid")
        if not network_enabled and transport is None:
            raise ValueError("GDELT network transport is disabled")
        self._client = httpx.Client(
            transport=transport or httpx.HTTPTransport(verify=True, retries=0),
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(connect=4.0, read=30.0, write=5.0, pool=4.0),
            headers={"Accept": "application/gzip", "Accept-Encoding": "identity"},
        )
        self._resolver = resolver or _resolve_public_addresses
        self._mock_transport = isinstance(transport, httpx.MockTransport)
        self._compressed_limit = compressed_limit
        self._decompressed_limit = decompressed_limit
        self._physical_call_cap = physical_call_cap
        self.physical_calls = 0
        self.received_raw_bytes = 0

    def __enter__(self) -> GdeltHttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """장기 실행기가 공유하는 connection pool을 명시적으로 닫는다."""

        self._client.close()

    def fetch(self, url: str) -> bytes:
        """최대 두 redirect를 같은 allowlist에서만 따라가고 검증된 압축 해제 결과를 반환한다."""

        requested_url = url
        current_url = url
        redirect_count = 0
        while True:
            _validate_bulk_url(current_url)
            host = urlsplit(current_url).hostname
            resolved = self._resolver(host) if host is not None else ()
            if host is None or not resolved:
                raise GdeltWorldNewsTransportError("GDELT_DNS_TLS_UNVERIFIED")
            try:
                if self.physical_calls >= self._physical_call_cap:
                    raise GdeltWorldNewsTransportError("GDELT_PHYSICAL_CALL_CAP")
                # stream context 진입 중에도 request bytes가 전송될 수 있으므로 호출 예산을 먼저 차감한다.
                self.physical_calls += 1
                with self._client.stream("GET", current_url) as response:
                    peer = _response_peer_address(response)
                    resolved_after = self._resolver(host)
                    if resolved_after != resolved or (
                        not self._mock_transport and peer not in resolved
                    ):
                        raise GdeltWorldNewsTransportError("GDELT_DNS_REBINDING")
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location or redirect_count >= 2:
                            raise GdeltWorldNewsTransportError("GDELT_REDIRECT_LIMIT")
                        current_url = urljoin(current_url, location)
                        redirect_count += 1
                        continue
                    if response.status_code != 200:
                        raise GdeltWorldNewsTransportError(
                            "GDELT_NOT_PUBLISHED"
                            if response.status_code == 404
                            else "GDELT_HTTP_STATUS"
                        )
                    chunks: list[bytes] = []
                    received = 0
                    for chunk in response.iter_raw():
                        received += len(chunk)
                        self.received_raw_bytes += len(chunk)
                        if received > self._compressed_limit:
                            raise GdeltWorldNewsTransportError("GDELT_COMPRESSED_SIZE")
                        chunks.append(chunk)
                    fixture = GdeltResponseFixture(
                        requested_url=requested_url,
                        final_url=current_url,
                        redirect_count=redirect_count,
                        tls_hostname_verified=True,
                        dns_public_addresses_verified=True,
                        headers=tuple(response.headers.multi_items()),
                        body=b"".join(chunks),
                    )
            except GdeltWorldNewsTransportError:
                raise
            except (httpx.TimeoutException, httpx.TransportError):
                # request 객체에는 URL이 남을 수 있어 credential 없는 이 경계에서도 원본 예외를 전파하지 않는다.
                raise GdeltWorldNewsTransportError("GDELT_NETWORK_ERROR") from None
            return validate_gdelt_response(
                fixture,
                compressed_limit=self._compressed_limit,
                decompressed_limit=self._decompressed_limit,
            )


def validate_gdelt_response(
    response: GdeltResponseFixture,
    *,
    compressed_limit: int = 8 * 1024 * 1024,
    decompressed_limit: int = 32 * 1024 * 1024,
) -> bytes:
    """반복 x-goog-hash는 허용하고 framing, CRC/hash, zip bomb와 truncation은 거부한다."""

    if response.redirect_count not in range(0, 3):
        raise GdeltWorldNewsTransportError("GDELT_REDIRECT_LIMIT")
    for url in (response.requested_url, response.final_url):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _ALLOWED_HOSTS
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise GdeltWorldNewsTransportError("GDELT_FIXED_ORIGIN")
    if not response.tls_hostname_verified or not response.dns_public_addresses_verified:
        raise GdeltWorldNewsTransportError("GDELT_DNS_TLS_UNVERIFIED")
    if not 1 <= len(response.body) <= compressed_limit:
        raise GdeltWorldNewsTransportError("GDELT_COMPRESSED_SIZE")

    headers: dict[str, list[str]] = {}
    for name, value in response.headers:
        headers.setdefault(name.lower().strip(), []).append(value.strip())
    if "content-length" in headers and "transfer-encoding" in headers:
        raise GdeltWorldNewsTransportError("GDELT_FRAMING_CONFLICT")
    if len(headers.get("content-length", ())) > 1:
        raise GdeltWorldNewsTransportError("GDELT_CONTENT_LENGTH_AMBIGUOUS")
    if "content-length" in headers:
        try:
            declared = int(headers["content-length"][0])
        except ValueError as error:
            raise GdeltWorldNewsTransportError("GDELT_CONTENT_LENGTH_INVALID") from error
        if declared != len(response.body):
            raise GdeltWorldNewsTransportError("GDELT_TRUNCATED")

    encoding = headers.get("content-encoding", [""])[0].lower()
    try:
        if encoding == "gzip" or response.final_url.endswith(".gz"):
            with gzip.GzipFile(fileobj=io.BytesIO(response.body), mode="rb") as archive:
                content = archive.read(decompressed_limit + 1)
        elif encoding in {"", "identity"}:
            content = response.body
        else:
            raise GdeltWorldNewsTransportError("GDELT_ENCODING_UNSUPPORTED")
    except (EOFError, OSError, zlib.error) as error:
        raise GdeltWorldNewsTransportError("GDELT_CRC_OR_TRUNCATION") from error
    if len(content) > decompressed_limit:
        raise GdeltWorldNewsTransportError("GDELT_DECOMPRESSED_SIZE")

    expected = _parse_google_hashes(headers.get("x-goog-hash", []))
    content_md5 = headers.get("content-md5", [])
    if len(content_md5) > 1 or (content_md5 and "md5" in expected):
        values = set(content_md5 + ([expected["md5"]] if "md5" in expected else []))
        if len(values) > 1:
            raise GdeltWorldNewsTransportError("GDELT_HASH_AMBIGUOUS")
    if content_md5 and "md5" not in expected:
        expected["md5"] = content_md5[0]
    actual = {
        "md5": base64.b64encode(
            hashlib.md5(response.body, usedforsecurity=False).digest()
        ).decode(),
        "crc32c": base64.b64encode(_crc32c(response.body).to_bytes(4, "big")).decode(),
    }
    for algorithm, expected_value in expected.items():
        if actual[algorithm] != expected_value:
            raise GdeltWorldNewsTransportError(
                "GDELT_CRC_MISMATCH" if algorithm == "crc32c" else "GDELT_HASH_MISMATCH"
            )
    return content


def _parse_google_hashes(values: list[str]) -> dict[str, str]:
    """공식 x-goog-hash의 알고리즘 이름만 case-fold하고 Base64 값은 그대로 검증한다."""

    parsed: dict[str, str] = {}
    for value in values:
        for token in value.split(","):
            normalized = token.strip()
            match = _GOOG_HASH.fullmatch(normalized)
            if match is None:
                # 알 수 없는 extension은 무시하지만 알려진 알고리즘의 손상된 값은 성공으로 축소하지 않는다.
                algorithm_name = normalized.partition("=")[0].lower()
                if algorithm_name in {"crc32c", "md5"}:
                    raise GdeltWorldNewsTransportError("GDELT_HASH_INVALID")
                continue
            algorithm, encoded = match.group(1).lower(), match.group(2)
            try:
                decoded = base64.b64decode(encoded, validate=True)
            except binascii.Error as error:
                raise GdeltWorldNewsTransportError("GDELT_HASH_INVALID") from error
            expected_size = 4 if algorithm == "crc32c" else 16
            if len(decoded) != expected_size:
                raise GdeltWorldNewsTransportError("GDELT_HASH_INVALID")
            previous = parsed.get(algorithm)
            if previous is not None and previous != encoded:
                raise GdeltWorldNewsTransportError("GDELT_HASH_AMBIGUOUS")
            parsed[algorithm] = encoded
    return parsed


def _crc32c(value: bytes) -> int:
    """Castagnoli CRC32C를 계산한다. zlib.crc32(IEEE)와 혼용하지 않는다."""

    checksum = 0xFFFFFFFF
    for byte in value:
        checksum ^= byte
        for _ in range(8):
            checksum = (checksum >> 1) ^ (_CRC32C_POLYNOMIAL if checksum & 1 else 0)
    return checksum ^ 0xFFFFFFFF


def _validate_bulk_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_HOSTS
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not _BULK_PATH.fullmatch(parsed.path)
    ):
        raise GdeltWorldNewsTransportError("GDELT_FIXED_ORIGIN")


def _resolve_public_addresses(host: str) -> tuple[str, ...]:
    """요청 직전 DNS 답이 모두 전역 public인지 확인한다."""

    try:
        values = tuple(
            sorted(
                {str(item[4][0]) for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
            )
        )
    except OSError:
        return ()
    if not values:
        return ()
    for value in values:
        address = ipaddress.ip_address(value)
        if not address.is_global:
            return ()
    return values


def _response_peer_address(response: httpx.Response) -> str | None:
    stream = response.extensions.get("network_stream")
    getter = getattr(stream, "get_extra_info", None)
    if not callable(getter):
        return None
    value = getter("server_addr")
    if isinstance(value, tuple) and value and isinstance(value[0], str):
        return value[0]
    return value if isinstance(value, str) else None
