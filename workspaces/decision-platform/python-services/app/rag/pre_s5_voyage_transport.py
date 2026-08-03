"""Pre-S5 Voyage contextual embedding의 one-shot outbound boundary다.

이 모듈은 승인 packet을 읽거나 DB를 쓰지 않는다. caller가 local-only packet verifier에서
투영한 capability와 `VOYAGE_API_KEY`를 명시적으로 주입해야 하며, 기본 sender는 socket을
열지 않는다. request/response 원문과 credential은 object lifetime 밖으로 저장하거나 log에
투영하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Protocol

import numpy as np
from numpy.typing import NDArray

from app.rag.pre_s5_provider_control import PreS5VoyageActivation
from app.rag.rag_v2_external_exact30_voyage_runner import (
    VoyagePreChunkedChunk,
    VoyagePreChunkedDocumentGroup,
)

_VOYAGE_ORIGIN: Final = "https://api.voyageai.com"
_VOYAGE_ENDPOINT: Final = "/v1/contextualizedembeddings"
_VOYAGE_OPERATION: Final = "CONTEXTUALIZED_DOCUMENT_EMBEDDING"
_VOYAGE_MODEL: Final = "voyage-context-4"
_OUTPUT_DIMENSION: Final = 1024
_TIMEOUT_SECONDS: Final = 20
_MAX_GROUPS: Final = 256
_MAX_CHUNKS: Final = 16_000
_MAX_CHUNK_UTF8_BYTES: Final = 64 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ID = re.compile(r"^src_[a-z0-9][a-z0-9_-]{2,95}$")
_SOURCE_REVISION_ID = re.compile(r"^srv_[a-z0-9][a-z0-9_-]{2,95}$")
_CHUNK_ID = re.compile(r"^rag_v2_chk_[0-9a-f]{32}$")


class PreS5VoyageTransportError(RuntimeError):
    """Voyage request 또는 response가 Pre-S5 hard gate를 벗어났음을 나타낸다."""


@dataclass(frozen=True, slots=True)
class PreS5VoyageHttpRequest:
    """fixed-origin one-shot HTTP request다.

    sender는 `max_response_bytes`를 넘는 body를 읽지 않아야 한다. Authorization header와
    body는 provider call 중에만 존재하며 receipt, exception message, object field 외부로
    복사하지 않는다.
    """

    url: str
    headers: Mapping[str, str]
    body: bytes
    timeout_seconds: int
    max_response_bytes: int


@dataclass(frozen=True, slots=True)
class PreS5VoyageHttpResponse:
    """sender가 process-local로 반환하는 bounded response envelope다."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class PreS5VoyageHttpSender(Protocol):
    """transport가 one POST만 위임하는 narrow outbound seam이다."""

    def post(self, request: PreS5VoyageHttpRequest) -> PreS5VoyageHttpResponse: ...


class OutboundDisabledPreS5VoyageHttpSender:
    """명시적으로 enable하지 않은 process가 provider socket을 열지 못하게 막는다."""

    def post(self, request: PreS5VoyageHttpRequest) -> PreS5VoyageHttpResponse:
        del request
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_OUTBOUND_DISABLED")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """fixed origin boundary에서 redirect target을 절대 새 authorization 대상이 되게 하지 않는다."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


class UrllibPreS5VoyageHttpSender:
    """fixed HTTPS POST를 한 번만 수행하는 real sender다.

    이 sender는 redirect와 ambient proxy를 끄고 bounded response만 읽는다. retry, request
    logging, raw artifact persistence는 제공하지 않으며 caller가 single-use authority를 먼저
    소비한다.
    """

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )

    def post(self, request: PreS5VoyageHttpRequest) -> PreS5VoyageHttpResponse:
        """one fixed request를 실행하고 error body 없이 status 또는 bounded body만 반환한다."""

        _validate_http_request(request)
        outbound = urllib.request.Request(
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method="POST",
        )
        try:
            with self._opener.open(outbound, timeout=request.timeout_seconds) as stream:
                body = stream.read(request.max_response_bytes + 1)
                status = stream.getcode()
                headers = {
                    str(key).lower(): str(value)
                    for key, value in stream.headers.items()
                }
        except urllib.error.HTTPError as error:
            # HTTP error payload는 raw provider data이므로 읽거나 error message에 붙이지 않는다.
            return PreS5VoyageHttpResponse(status=error.code, headers={}, body=b"")
        except (OSError, ValueError, urllib.error.URLError) as error:
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_TRANSPORT_UNAVAILABLE") from error
        if not isinstance(status, int) or len(body) > request.max_response_bytes:
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_TRANSPORT_UNAVAILABLE")
        return PreS5VoyageHttpResponse(status=status, headers=headers, body=body)


class PreS5VoyageContext4Transport:
    """approved Voyage activation 하나를 ordered contextual embedding call 한 번으로 소비한다.

    `embed_document_groups`는 all-or-nothing vector result만 반환한다. input/response 검증 실패도
    provider attempt 뒤에는 single-use 상태를 유지하므로 partial retry 또는 query-level fallback이
    생기지 않는다.
    """

    def __init__(
        self,
        *,
        activation: PreS5VoyageActivation,
        api_key: str,
        sender: PreS5VoyageHttpSender | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        _validate_activation_shape(activation)
        if (
            not isinstance(api_key, str)
            or not 1 <= len(api_key) <= 4_096
            or api_key != api_key.strip()
            or any(character in api_key for character in ("\x00", "\r", "\n"))
        ):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_API_KEY_REQUIRED")
        self._activation = activation
        self._api_key = api_key
        self._sender = sender or OutboundDisabledPreS5VoyageHttpSender()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._consumed = False
        self._external_physical_calls = 0

    @property
    def external_physical_calls(self) -> int:
        """real sender로 위임한 physical attempt 수만 반환한다."""

        with self._lock:
            return self._external_physical_calls

    def content_free_summary(self) -> dict[str, object]:
        """credential, source text, raw response를 제외한 operator receipt projection을 반환한다."""

        with self._lock:
            consumed = self._consumed
            physical_calls = self._external_physical_calls
        return {
            "code": "PRE_S5_VOYAGE_ATTEMPT_CONSUMED" if consumed else "PRE_S5_VOYAGE_READY",
            "externalPhysicalCalls": physical_calls,
            "logicalCallsConsumed": 1 if consumed else 0,
            "operation": self._activation.operation,
            "provider": self._activation.provider,
            "rawArtifactCount": 0,
            "state": "CONSUMED" if consumed else "READY",
        }

    def embed_document_groups(
        self,
        *,
        groups: tuple[VoyagePreChunkedDocumentGroup, ...],
    ) -> NDArray[np.float32]:
        """ordered pre-chunked groups를 exactly one contextual embeddings request로 변환한다."""

        now = _require_utc_now(self._clock)
        if now >= self._activation.expires_at:
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_ACTIVATION_EXPIRED")
        request = _build_request(groups=groups, activation=self._activation, api_key=self._api_key)
        if isinstance(self._sender, OutboundDisabledPreS5VoyageHttpSender):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_OUTBOUND_DISABLED")
        self._consume_before_outbound()
        try:
            response = self._sender.post(request)
        except PreS5VoyageTransportError:
            raise
        except Exception as error:
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_TRANSPORT_UNAVAILABLE") from error
        return _parse_response(response=response, groups=groups, activation=self._activation)

    def _consume_before_outbound(self) -> None:
        """first real outbound attempt 전에 single-use capability를 원자적으로 소비한다."""

        with self._lock:
            if self._consumed:
                raise PreS5VoyageTransportError("PRE_S5_VOYAGE_SINGLE_USE")
            self._consumed = True
            self._external_physical_calls += 1


def _build_request(
    *,
    groups: tuple[VoyagePreChunkedDocumentGroup, ...],
    activation: PreS5VoyageActivation,
    api_key: str,
) -> PreS5VoyageHttpRequest:
    """caller order를 보존하면서 input hash, topology, byte/token caps를 socket 전 검증한다."""

    _validate_groups(groups=groups, activation=activation)
    inputs = [[chunk.canonical_text for chunk in group.chunks] for group in groups]
    try:
        body = json.dumps(
            {
                "enable_auto_chunking": False,
                "input_type": "document",
                "inputs": inputs,
                "model": _VOYAGE_MODEL,
                "output_dimension": _OUTPUT_DIMENSION,
                "output_dtype": "float",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError) as error:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_REQUEST_INVALID") from error
    if not 1 <= len(body) <= activation.byte_cap:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_REQUEST_INVALID")
    return PreS5VoyageHttpRequest(
        url=f"{_VOYAGE_ORIGIN}{_VOYAGE_ENDPOINT}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        body=body,
        timeout_seconds=_TIMEOUT_SECONDS,
        max_response_bytes=activation.byte_cap,
    )


def _validate_activation_shape(activation: object) -> None:
    """packet loader 외 직접 생성된 activation도 fixed provider contract로 다시 close한다."""

    if not isinstance(activation, PreS5VoyageActivation):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_ACTIVATION_INVALID")
    if (
        not _is_sha256(activation.packet_sha256)
        or not _is_sha256(activation.bundle_manifest_sha256)
        or activation.provider != "VOYAGE"
        or activation.operation != _VOYAGE_OPERATION
        or activation.origin != _VOYAGE_ORIGIN
        or activation.endpoint != _VOYAGE_ENDPOINT
        or not isinstance(activation.expires_at, datetime)
        or activation.expires_at.tzinfo is None
        or activation.logical_call_cap != 1
        or activation.physical_call_cap != 1
        or type(activation.token_cap) is not int
        or not 1 <= activation.token_cap <= 120_000
        or type(activation.byte_cap) is not int
        or not 1 <= activation.byte_cap <= 4_194_304
        or type(activation.cost_cap_microusd) is not int
        or not 0 <= activation.cost_cap_microusd <= 1_000_000_000
        or activation.retry_count != 0
        or activation.raw_artifact_count != 0
    ):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_ACTIVATION_INVALID")


def _validate_groups(
    *,
    groups: object,
    activation: PreS5VoyageActivation,
) -> None:
    """partial/mixed-profile submission을 막기 위해 ordered group topology를 strict하게 검증한다."""

    if not isinstance(groups, tuple) or not 1 <= len(groups) <= _MAX_GROUPS:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_REQUEST_INVALID")
    source_ids: list[str] = []
    revision_ids: set[str] = set()
    chunk_ids: set[str] = set()
    total_chunks = 0
    total_utf8_bytes = 0
    for group in groups:
        if (
            not isinstance(group, VoyagePreChunkedDocumentGroup)
            or _SOURCE_ID.fullmatch(group.source_id) is None
            or _SOURCE_REVISION_ID.fullmatch(group.source_revision_id) is None
            or not _is_sha256(group.context_set_hash)
            or not isinstance(group.chunks, tuple)
            or not group.chunks
        ):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_REQUEST_INVALID")
        source_ids.append(group.source_id)
        if group.source_revision_id in revision_ids:
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_REQUEST_INVALID")
        revision_ids.add(group.source_revision_id)
        for chunk in group.chunks:
            if (
                not isinstance(chunk, VoyagePreChunkedChunk)
                or _CHUNK_ID.fullmatch(chunk.chunk_id) is None
                or not isinstance(chunk.canonical_text, str)
                or not chunk.canonical_text.strip()
                or "\x00" in chunk.canonical_text
                or not _is_sha256(chunk.canonical_text_sha256)
                or not _is_sha256(chunk.embedding_input_hash)
                or hashlib.sha256(chunk.canonical_text.encode("utf-8")).hexdigest()
                != chunk.canonical_text_sha256
            ):
                raise PreS5VoyageTransportError("PRE_S5_VOYAGE_REQUEST_INVALID")
            encoded_length = len(chunk.canonical_text.encode("utf-8"))
            if not 1 <= encoded_length <= _MAX_CHUNK_UTF8_BYTES or chunk.chunk_id in chunk_ids:
                raise PreS5VoyageTransportError("PRE_S5_VOYAGE_REQUEST_INVALID")
            chunk_ids.add(chunk.chunk_id)
            total_chunks += 1
            total_utf8_bytes += encoded_length
    if (
        len(set(source_ids)) != len(source_ids)
        or tuple(source_ids) != tuple(sorted(source_ids, key=lambda value: value.encode("utf-8")))
        or not 1 <= total_chunks <= _MAX_CHUNKS
        # Byte-level pre-tokenized input can never exceed the conservative character-byte ceiling.
        or total_utf8_bytes > activation.token_cap
    ):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_REQUEST_INVALID")


def _parse_response(
    *,
    response: object,
    groups: tuple[VoyagePreChunkedDocumentGroup, ...],
    activation: PreS5VoyageActivation,
) -> NDArray[np.float32]:
    """raw provider response를 retaining하지 않고 exact ordered float32 vectors로 검증·투영한다."""

    if (
        not isinstance(response, PreS5VoyageHttpResponse)
        or response.status != 200
        or not isinstance(response.body, bytes)
        or not 1 <= len(response.body) <= activation.byte_cap
    ):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
    try:
        decoded = json.loads(
            response.body.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID") from error
    if not isinstance(decoded, dict) or set(decoded) != {"chunker_version", "data", "model", "usage"}:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
    if decoded.get("model") != _VOYAGE_MODEL or not isinstance(decoded.get("chunker_version"), str):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
    _validate_usage(decoded.get("usage"), token_cap=activation.token_cap)
    group_data = decoded.get("data")
    if not isinstance(group_data, list) or len(group_data) != len(groups):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
    vectors: list[NDArray[np.float32]] = []
    for group_index, (expected_group, received_group) in enumerate(zip(groups, group_data, strict=True)):
        if (
            not isinstance(received_group, dict)
            or set(received_group) != {"data", "index"}
            or received_group.get("index") != group_index
            or type(received_group.get("index")) is not int
        ):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
        chunk_data = received_group.get("data")
        if not isinstance(chunk_data, list) or len(chunk_data) != len(expected_group.chunks):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
        for chunk_index, (expected_chunk, received_chunk) in enumerate(
            zip(expected_group.chunks, chunk_data, strict=True)
        ):
            if (
                not isinstance(received_chunk, dict)
                or set(received_chunk) != {"embedding", "index", "text"}
                or received_chunk.get("index") != chunk_index
                or type(received_chunk.get("index")) is not int
                or received_chunk.get("text") != expected_chunk.canonical_text
            ):
                raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
            vectors.append(_vector_from_response(received_chunk.get("embedding")))
    if not vectors:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
    return np.vstack(vectors).astype(np.float32, copy=False)


def _validate_usage(value: object, *, token_cap: int) -> None:
    """provider usage가 packet token cap을 넘거나 unknown charge projection을 만들지 않게 한다."""

    if (
        not isinstance(value, dict)
        or set(value) != {"total_tokens"}
        or type(value.get("total_tokens")) is not int
        or not 0 <= value["total_tokens"] <= token_cap
    ):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")


def _vector_from_response(value: object) -> NDArray[np.float32]:
    """JSON numeric array 하나를 finite unit-norm 1024-dimension float32 vector로 좁힌다."""

    if not isinstance(value, list) or len(value) != _OUTPUT_DIMENSION:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
    numbers: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
        try:
            number = float(item)
        except (OverflowError, ValueError) as error:
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID") from error
        if not math.isfinite(number):
            raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
        numbers.append(number)
    with np.errstate(over="ignore", invalid="ignore"):
        vector = np.asarray(numbers, dtype=np.float32)
        norm = float(np.linalg.norm(vector))
    if not bool(np.isfinite(vector).all()) or not math.isfinite(norm) or not math.isclose(
        norm, 1.0, abs_tol=1e-5, rel_tol=0.0
    ):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_RESPONSE_INVALID")
    return vector


def _require_utc_now(clock: Callable[[], datetime]) -> datetime:
    """naive/custom clock을 provider authorization clock으로 쓰지 않는다."""

    try:
        now = clock()
    except Exception as error:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_ACTIVATION_INVALID") from error
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_ACTIVATION_INVALID")
    return now.astimezone(UTC)


def _validate_http_request(request: object) -> None:
    """real sender가 caller-supplied URL/header mutation을 outbound 직전에 다시 거부한다."""

    if (
        not isinstance(request, PreS5VoyageHttpRequest)
        or request.url != f"{_VOYAGE_ORIGIN}{_VOYAGE_ENDPOINT}"
        or request.timeout_seconds != _TIMEOUT_SECONDS
        or type(request.max_response_bytes) is not int
        or not 1 <= request.max_response_bytes <= 4_194_304
        or not isinstance(request.body, bytes)
        or not request.body
        or set(request.headers) != {"Accept", "Authorization", "Content-Type"}
        or request.headers.get("Accept") != "application/json"
        or request.headers.get("Content-Type") != "application/json"
        or not isinstance(request.headers.get("Authorization"), str)
        or not request.headers["Authorization"].startswith("Bearer ")
        or len(request.headers["Authorization"]) > 4_103
    ):
        raise PreS5VoyageTransportError("PRE_S5_VOYAGE_REQUEST_INVALID")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """duplicate response key를 last-write-wins parsing으로 해석하지 않는다."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None
